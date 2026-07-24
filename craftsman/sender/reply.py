"""Reply dispatch (M4.1): send an approved draft through the normal send engine.

One function, two callers: the inbox endpoint (a human clicked send — the only path
under Copilot) and the Autopilot policy engine (M4.4, opt-in, auto=True). Both get
identical treatment: suppression re-check, mailbox capacity + per-mailbox rate
limit, threading headers, idempotency claim before I/O. Replies never advance the
sequence (the enrollment already left the sending cycle when the reply landed) and
never touch bandit posteriors (bandit_outcome stays NULL — a different reward
process; acceptance rate is the metric instead).

The campaign daily cap is deliberately NOT applied: it bounds cold outbound volume,
and blocking an answer to an engaged human because the campaign hit its cold-send
budget would be the wrong kind of safety. Mailbox-level limits still apply.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from craftsman.compliance.suppression import is_suppressed
from craftsman.core.models import Campaign, Enrollment, Lead, Mailbox, Message, ReplyDraft
from craftsman.deliverability.health import record_domain_send
from craftsman.ingest.verify import domain_of
from craftsman.sender.limiter import acquire_domain_slot, acquire_send_slot
from craftsman.sender.smtp import (
    PreparedEmail,
    SendBlocked,
    build_email,
    deliver,
    effective_daily_limit,
    last_outbound_in_thread,
)

log = logging.getLogger(__name__)


class DraftUnavailable(Exception):
    """The draft is not in a sendable state (double click, already resolved)."""


class DraftInvalid(Exception):
    """An edited body failed re-validation; the draft returns to pending."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _claim(db: Session, draft: ReplyDraft, *, auto: bool = False) -> None:
    """CAS pending→sending — the idempotency claim, committed before any I/O so a
    concurrent second click (or a duplicate Autopilot evaluation) trips on rowcount
    0 and never produces a duplicate email.

    The auto path also stamps ``auto_sent`` HERE, not after delivery: the partial
    unique index ``uq_auto_reply_per_thread`` then makes the ≤1-auto-reply-per-
    thread invariant structural — a second qualifying draft in the same thread
    (two workers that both read a zero prior-auto-reply count) fails this claim
    with IntegrityError before any mail could leave (F-05, findings/12)."""
    values: dict = {"status": "sending"}
    if auto:
        values["auto_sent"] = True
    try:
        result = db.execute(
            update(ReplyDraft)
            .where(ReplyDraft.id == draft.id, ReplyDraft.status == "pending")
            .values(**values)
        )
    except IntegrityError:
        db.rollback()
        raise DraftUnavailable("auto_reply_already_sent_in_thread")
    if result.rowcount != 1:
        raise DraftUnavailable(f"draft is not pending (status={draft.status!r})")
    db.commit()
    db.refresh(draft)


def _release(db: Session, draft: ReplyDraft, status: str, detail: dict | None = None) -> None:
    draft.status = status
    # only "sent"/"edited_sent" ever bypass _release — an undispatched draft holds
    # no thread reservation, so a failed/blocked auto attempt frees the index slot
    draft.auto_sent = False
    if detail is not None:
        draft.detail = {**(draft.detail or {}), **detail}
    db.add(draft)
    db.commit()


async def send_reply_draft(
    db: Session,
    draft: ReplyDraft,
    *,
    edited_body: str | None = None,
    auto: bool = False,
) -> Message:
    """Dispatch a pending draft. Raises DraftUnavailable (not pending),
    DraftInvalid (edit failed re-validation; draft back to pending), or
    SendBlocked (suppressed / no mailbox / rate limited; draft back to pending
    except suppression, which discards it — that lead must never be contacted)."""
    inbound = db.get(Message, draft.inbound_message_id)
    enrollment = db.get(Enrollment, draft.enrollment_id) if draft.enrollment_id else None
    if inbound is None or enrollment is None:
        raise DraftUnavailable("draft has no inbound message/enrollment (erased?)")
    lead = db.get(Lead, enrollment.lead_id)
    campaign = db.get(Campaign, enrollment.campaign_id)

    body = draft.body or ""
    edited = False
    if edited_body is not None and edited_body.strip() and edited_body != draft.body:
        # the human's edit is re-validated with the same gates the LLM faced —
        # Craftsman never sends what its validator rejected, whoever typed it
        from craftsman.copywriter.validator import validate_reply_fill
        from craftsman.core.config import get_settings
        from craftsman.core.models import Company
        from craftsman.core.schemas import ResearchBrief

        company = db.get(Company, lead.company_id) if lead else None
        brief = ResearchBrief.model_validate(
            (company.research_brief if company else None) or {}
        )
        result = validate_reply_fill(
            slots={"body": edited_body},
            rendered_body=edited_body,
            grounding_sources=[
                brief.model_dump(),
                {"value_prop": campaign.value_prop if campaign else ""},
                campaign.sender_persona or {} if campaign else {},
                {"first_name": lead.first_name if lead else ""},
            ],
            reply_text=inbound.body or "",
            campaign_sources=[
                {"value_prop": campaign.value_prop if campaign else ""},
                campaign.sender_persona or {} if campaign else {},
            ],
            max_words=get_settings().reply_draft_max_words,
        )
        if not result.ok:
            raise DraftInvalid(result.errors)
        body = edited_body
        edited = True

    _claim(db, draft, auto=auto)  # durable pending→sending (+ thread reservation
    # for auto) before any check with side effects

    try:
        if lead is None or is_suppressed(db, lead.email):
            _release(db, draft, "discarded", {"reason": "suppressed_at_send"})
            raise SendBlocked("suppressed")

        # reply from the mailbox that owns the thread — From consistency beats
        # capacity balancing; fall back to the inbound's mailbox if needed
        anchor = last_outbound_in_thread(db, enrollment.id)
        mailbox_id = (anchor.mailbox_id if anchor else None) or inbound.mailbox_id
        mailbox = db.get(Mailbox, mailbox_id) if mailbox_id else None
        if mailbox is None or mailbox.health == "paused":
            _release(db, draft, "pending")
            raise SendBlocked("no_mailbox_capacity")
        limit = effective_daily_limit(mailbox.daily_limit, mailbox.warmup_stage)
        if mailbox.health == "degraded":
            limit = max(1, limit // 2)
        if mailbox.sent_today >= limit:
            _release(db, draft, "pending")
            raise SendBlocked("no_mailbox_capacity")
        wait = acquire_send_slot(str(mailbox.id))
        if wait > 0:
            _release(db, draft, "pending")
            raise SendBlocked("rate_limited", retry_in=wait)
        wait = acquire_domain_slot(domain_of(mailbox.email))
        if wait > 0:
            _release(db, draft, "pending")
            raise SendBlocked("domain_rate_limited", retry_in=wait)

        subject = inbound.subject or (anchor.subject if anchor else "") or ""
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        now = datetime.now(timezone.utc)
        outbound = Message(
            enrollment_id=enrollment.id,
            direction="outbound",
            mailbox_id=mailbox.id,
            subject=subject,
            body=body,
            bandit_outcome=None,  # replies never settle into the bandit
        )
        db.add(outbound)
        db.flush()

        in_reply_to = inbound.smtp_message_id
        refs = [in_reply_to] if in_reply_to else None
        email_msg, message_id = build_email(
            db=db, mailbox=mailbox, lead=lead,
            prepared=PreparedEmail(subject=subject, body=body, to_email=lead.email),
            in_reply_to=in_reply_to, references=refs,
        )
        try:
            await deliver(mailbox, email_msg)
        except Exception:
            db.delete(outbound)
            _release(db, draft, "pending", {"last_error": "delivery_failed"})
            raise

        outbound.smtp_message_id = message_id
        outbound.sent_at = now
        db.add(outbound)
        mailbox.sent_today += 1
        db.add(mailbox)
        record_domain_send(db, domain_of(mailbox.email))  # M5.3 per-domain rollup
        draft.status = "edited_sent" if edited else "sent"
        draft.auto_sent = auto
        draft.sent_message_id = outbound.id
        draft.resolved_at = now
        db.add(draft)
        db.commit()
        log.info(
            "reply draft %s dispatched (%s%s) to %s",
            draft.id, draft.status, ", autopilot" if auto else "", lead.email,
        )
        return outbound
    except SendBlocked:
        raise
    except Exception:
        # any unexpected failure after the claim: don't strand the draft in 'sending'
        if draft.status == "sending":
            _release(db, draft, "pending", {"last_error": "unexpected_failure"})
        raise


def discard_reply_draft(db: Session, draft: ReplyDraft, reason: str = "human_discard") -> None:
    """CAS pending→discarded; raises DraftUnavailable if the draft already resolved."""
    result = db.execute(
        update(ReplyDraft)
        .where(ReplyDraft.id == draft.id, ReplyDraft.status == "pending")
        .values(
            status="discarded",
            resolved_at=datetime.now(timezone.utc),
            detail={**(draft.detail or {}), "reason": reason},
        )
    )
    if result.rowcount != 1:
        raise DraftUnavailable(f"draft is not pending (status={draft.status!r})")
    db.expire(draft)
