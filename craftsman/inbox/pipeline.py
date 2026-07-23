"""Inbound pipeline: reply → classify → state machine → bandit → human handoff."""

import logging
from datetime import datetime, time, timezone

import httpx
from sqlalchemy.orm import Session

from craftsman.bandit.settle import record_reply_outcome
from craftsman.bandit.thompson import outcome_for_label
from craftsman.compliance.suppression import suppress
from craftsman.core.config import get_settings
from craftsman.core.models import Enrollment, Lead, Message, ReviewQueueItem
from craftsman.core.schemas import ReplyClassification
from craftsman.inbox.classifier import classify_reply
from craftsman.inbox.poller import InboundEmail, match_thread
from craftsman.inbox.reply_parser import strip_quoted
from craftsman.llm.client import LLMClient
from craftsman.sender.smtp import record_bounce
from craftsman.sequencer.machine import Event, InvalidTransition
from craftsman.sequencer.tick import apply_event

log = logging.getLogger(__name__)

LABEL_TO_EVENT = {
    "interested": Event.REPLY_INTERESTED,
    "objection": Event.REPLY_OBJECTION,
    "not_now": Event.REPLY_NOT_NOW,
    "ooo": Event.REPLY_OOO,
    "unsubscribe": Event.UNSUBSCRIBE,
    "bounce_or_auto": Event.BOUNCE,
}


async def handle_inbound(
    db: Session,
    llm: LLMClient,
    inbound: InboundEmail,
    mailbox_id=None,
) -> Message | None:
    """Full inbound flow for one email. Returns the stored inbound Message, or None
    if it couldn't be matched to a thread we own."""
    from craftsman.core.logging import bind_log_context

    outbound = match_thread(db, inbound)
    if outbound is None:
        log.info("unmatched inbound from %s — ignoring", inbound.from_addr)
        return None

    enrollment = db.get(Enrollment, outbound.enrollment_id)
    bind_log_context(
        enrollment_id=str(outbound.enrollment_id) if outbound.enrollment_id else None,
        message_id=str(outbound.id),
    )
    fresh_text = strip_quoted(inbound.body)
    classification = await classify_reply(llm, fresh_text or inbound.body)

    inbound_msg = Message(
        enrollment_id=outbound.enrollment_id,
        direction="inbound",
        mailbox_id=mailbox_id,
        subject=inbound.subject,
        body=fresh_text or inbound.body,
        smtp_message_id=inbound.message_id,
        classification=classification.label,
        classification_confidence=classification.confidence,
    )
    db.add(inbound_msg)
    db.flush()

    settings = get_settings()
    if classification.confidence < settings.classifier_confidence_threshold:
        db.add(
            ReviewQueueItem(
                kind="classification",
                message_id=inbound_msg.id,
                enrollment_id=enrollment.id if enrollment else None,
                payload={
                    "label": classification.label,
                    "confidence": classification.confidence,
                    "from": inbound.from_addr,
                },
            )
        )
        log.info(
            "low-confidence (%.2f) classification '%s' routed to review queue",
            classification.confidence, classification.label,
        )
        return inbound_msg

    apply_classification(db, enrollment, outbound, classification)
    return inbound_msg


def apply_classification(
    db: Session,
    enrollment: Enrollment | None,
    outbound: Message,
    classification: ReplyClassification,
) -> None:
    """State transition + bandit update + side effects for a confident classification."""
    label = classification.label
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None

    # --- state machine
    if enrollment is not None:
        was_awaiting_touch = enrollment.state == "awaiting_human_touch"
        event = LABEL_TO_EVENT[label]
        try:
            apply_event(db, enrollment, event, detail={"label": label})
        except InvalidTransition as e:
            log.warning("classification transition skipped: %s", e)
        # M3.1: a reply/bounce/unsub that moved the enrollment out of
        # awaiting_human_touch orphans its open task — cancel it so nobody performs
        # a touch on a lead who already answered (or must not be contacted).
        if was_awaiting_touch and enrollment.state != "awaiting_human_touch":
            from craftsman.sequencer.touch import cancel_open_tasks

            cancel_open_tasks(db, enrollment.id, reason=f"reply:{label}")
        if label == "ooo" and classification.ooo_return_date:
            enrollment.next_action_at = datetime.combine(
                classification.ooo_return_date, time(17, 0), tzinfo=timezone.utc
            )  # return date + next-day send window handled by tick
        if label in ("interested", "objection", "not_now", "unsubscribe", "bounce_or_auto"):
            if label not in ("bounce_or_auto",):
                enrollment.next_action_at = None
        db.add(enrollment)

    # --- bandit
    outcome = outcome_for_label(label)
    if outcome is not None:
        record_reply_outcome(db, outbound, success=outcome)

    # --- side effects
    if lead is not None:
        if label == "unsubscribe":
            suppress(db, lead.email, reason="unsubscribe")
        elif label == "bounce_or_auto":
            suppress(db, lead.email, reason="bounce")
            mailbox = None
            if outbound.mailbox_id is not None:
                from craftsman.core.models import Mailbox

                mailbox = db.get(Mailbox, outbound.mailbox_id)
            if mailbox is not None:
                record_bounce(db, mailbox)
        elif label == "interested":
            notify_interested(lead, outbound)


def notify_interested(lead: Lead, outbound: Message) -> None:
    """Slack webhook with lead context. The sequence stops; a human takes over."""
    url = get_settings().slack_webhook_url
    if not url:
        log.info("interested reply from %s (no Slack webhook configured)", lead.email)
        return
    try:
        httpx.post(
            url,
            json={
                "text": (
                    f":tada: *Interested reply* from {lead.first_name or ''} "
                    f"{lead.last_name or ''} <{lead.email}>\n"
                    f"Title: {lead.title or 'unknown'}\n"
                    f"Thread subject: {outbound.subject}\n"
                    f"Go reply like a human."
                )
            },
            timeout=10,
        )
    except httpx.HTTPError as e:
        log.warning("Slack notify failed: %s", e)
