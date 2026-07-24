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
from craftsman.webhooks.events import safe_emit

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
    enqueue_draft=None,
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

    # M5.4: reply.received fires for every matched+classified reply — including
    # low-confidence ones headed for the review queue. safe_emit: a webhook
    # problem can never break classification.
    safe_emit(db, "reply.received", {
        "message_id": str(inbound_msg.id),
        "enrollment_id": str(outbound.enrollment_id) if outbound.enrollment_id else None,
        "classification": classification.label,
        "confidence": classification.confidence,
        "subject": inbound.subject,
    })

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
        # M4.2: escalation still runs on unconfident replies — a legal threat must
        # suppress and page a human even when the classifier hedged. (The keyword
        # rules don't depend on the label; review routing above already happened,
        # so a duplicate review action is skipped by the executor.)
        decision = _evaluate_escalation(db, enrollment, classification, inbound_msg)
        execute_escalation(
            db, decision,
            enrollment=enrollment, outbound=outbound, inbound_msg=inbound_msg,
            label=classification.label, skip_review_item=True,
        )
        return inbound_msg

    apply_classification(
        db, enrollment, outbound, classification,
        inbound_message_id=inbound_msg.id, enqueue_draft=enqueue_draft,
    )
    return inbound_msg


def apply_classification(
    db: Session,
    enrollment: Enrollment | None,
    outbound: Message,
    classification: ReplyClassification,
    *,
    inbound_message_id=None,
    enqueue_draft=None,
) -> None:
    """State transition + bandit update + side effects for a confident classification.

    `enqueue_draft` (M4.1): callable(inbound_message_id) that queues Copilot reply
    drafting for interested/objection replies — injected like tick's enqueue_* so the
    pure pipeline never imports Celery. Drafting is async and can never block or fail
    classification. A None callable means no drafting (tests, callers that opt out).

    M4.2: the escalation ruleset is evaluated here (built-in defaults + DB rules) and
    executed — the default interested-notify rule reproduces the pre-M4.2 Slack ping
    exactly, and `block_draft` gates the M4.1 enqueue below.
    """
    label = classification.label
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None

    inbound_msg = db.get(Message, inbound_message_id) if inbound_message_id else None
    escalation = _evaluate_escalation(db, enrollment, classification, inbound_msg)

    # --- state machine
    if enrollment is not None:
        was_awaiting_touch = enrollment.state == "awaiting_human_touch"
        from_state = enrollment.state
        event = LABEL_TO_EVENT[label]
        try:
            apply_event(db, enrollment, event, detail={"label": label})
        except InvalidTransition as e:
            log.warning("classification transition skipped: %s", e)
        # M5.4: lead.status_changed — emitted from HERE and the manual suppress
        # endpoint only (granularity documented in webhooks/events.py)
        if enrollment.state != from_state:
            safe_emit(db, "lead.status_changed", {
                "lead_id": str(enrollment.lead_id),
                "enrollment_id": str(enrollment.id),
                "from_state": from_state,
                "to_state": enrollment.state,
                "label": label,
            })
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
                # M5.3: the inbound body is the bounce diagnostic — spam/block/
                # reputation wording classifies it as the complaint proxy
                record_bounce(
                    db, mailbox,
                    diagnostic=(inbound_msg.body if inbound_msg is not None else None),
                )
    # --- escalation (M4.2): notify/urgent/suppress/review as the ruleset decides.
    # The interested Slack ping now lives in the builtin:interested-notify rule.
    execute_escalation(
        db, escalation,
        enrollment=enrollment, outbound=outbound, inbound_msg=inbound_msg, label=label,
    )

    # --- Copilot draft (M4.1): interested/objection replies get a validated draft
    # queued for a human. Suppression re-checked inside the generator; unsubscribe/
    # bounce labels never reach here with a drafting label. An escalation match with
    # block_draft (e.g. the legal-threat tripwire) means NO draft, ever.
    if (
        enqueue_draft is not None
        and inbound_message_id is not None
        and label in ("interested", "objection")
        and not escalation.block_draft
    ):
        enqueue_draft(inbound_message_id)


def _evaluate_escalation(db, enrollment, classification, inbound_msg):
    """Effective ruleset (defaults + global + campaign DB rules) → decision."""
    from craftsman.inbox.escalation import evaluate, load_rules

    settings = get_settings()
    rules = load_rules(
        db,
        enrollment.campaign_id if enrollment else None,
        settings.classifier_confidence_threshold,
    )
    return evaluate(
        rules,
        label=classification.label,
        confidence=classification.confidence,
        reply_text=(inbound_msg.body if inbound_msg is not None else "") or "",
    )


def execute_escalation(
    db, decision, *, enrollment, outbound, inbound_msg, label, skip_review_item=False,
) -> None:
    """Apply a decision's I/O actions. block_draft/block_autopilot are consumed by
    the callers (draft enqueue above; Autopilot policy in M4.4) — not here."""
    if not decision.any_action:
        return
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None
    log.info(
        "escalation matched %s for %s", decision.matched_rules,
        lead.email if lead else "unknown lead",
    )
    # M5.4: escalation.fired — the decision as data, before its I/O actions run
    safe_emit(db, "escalation.fired", {
        "rules": decision.matched_rules,
        "label": label,
        "enrollment_id": str(enrollment.id) if enrollment else None,
        "suppress": decision.suppress,
        "urgent_notify": decision.urgent_notify,
        "review_queue": decision.review_queue,
    })
    if decision.suppress and lead is not None:
        suppress(db, lead.email, reason="escalation")
    if decision.review_queue and not skip_review_item:
        db.add(
            ReviewQueueItem(
                kind="escalation",
                message_id=inbound_msg.id if inbound_msg is not None else None,
                enrollment_id=enrollment.id if enrollment else None,
                payload={
                    "rules": decision.matched_rules,
                    "label": label,
                    "suppressed": decision.suppress,
                },
            )
        )
    if decision.urgent_notify:
        snippet = ((inbound_msg.body if inbound_msg is not None else "") or "")[:300]
        _slack(
            f":rotating_light: *Escalation* ({', '.join(decision.matched_rules)})\n"
            f"From: {lead.email if lead else 'unknown'}\n"
            f"Label: {label}"
            f"{' · suppressed' if decision.suppress else ''}\n"
            f"> {snippet}"
        )
    elif decision.notify and lead is not None and outbound is not None:
        notify_interested(lead, outbound)


def _slack(text: str) -> None:
    url = get_settings().slack_webhook_url
    if not url:
        log.info("slack notify skipped (no webhook configured): %s", text.splitlines()[0])
        return
    try:
        httpx.post(url, json={"text": text}, timeout=10)
    except httpx.HTTPError as e:
        log.warning("Slack notify failed: %s", e)


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
