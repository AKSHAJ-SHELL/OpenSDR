"""Celery tasks — thin async-bridging wrappers around the pure modules."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from craftsman.core.db import session_scope
from craftsman.core.models import Company, Enrollment, Lead, Mailbox, Message, SequenceStep, Variant
from craftsman.core.schemas import ResearchBrief
from craftsman.llm.client import get_llm
from craftsman.workers.celery_app import app

log = logging.getLogger(__name__)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ sequencer


@app.task
def sequencer_tick():
    from craftsman.sequencer.tick import tick

    with session_scope() as db:
        handled = tick(
            db,
            enqueue_research=lambda eid: research_enrollment.delay(eid),
            enqueue_send=lambda eid: generate_and_send.delay(eid),
        )
    log.info("tick handled %d enrollments", handled)
    return handled


# ------------------------------------------------------------------ research


@app.task(bind=True, max_retries=2, default_retry_delay=300)
def research_enrollment(self, enrollment_id: str):
    from craftsman.research.agent import ResearchError, research_company
    from craftsman.sequencer.machine import Event
    from craftsman.sequencer.tick import apply_event

    from craftsman.core.logging import bind_log_context

    with session_scope() as db:
        enrollment = db.get(Enrollment, enrollment_id)
        if enrollment is None or enrollment.state != "researching":
            return
        lead = db.get(Lead, enrollment.lead_id)
        company = db.get(Company, lead.company_id)
        bind_log_context(enrollment_id=str(enrollment.id), lead_id=str(lead.id))
        try:
            _run(research_company(db, company, get_llm()))
            apply_event(db, enrollment, Event.RESEARCH_DONE)
            enrollment.next_action_at = datetime.now(timezone.utc)
        except ResearchError as e:
            log.warning("research failed for %s: %s", company.domain, e)
            apply_event(db, enrollment, Event.RESEARCH_FAILED, detail={"error": str(e)})


# ------------------------------------------------------------------ generate + send


@app.task(bind=True, max_retries=3)
def generate_and_send(self, enrollment_id: str):
    from sqlalchemy.exc import IntegrityError

    from craftsman.bandit.thompson import Arm, pick_arm
    from craftsman.compliance.suppression import is_suppressed
    from craftsman.copywriter.fill import generate_copy
    from craftsman.core.models import ReviewQueueItem
    from craftsman.sender.smtp import (
        PreparedEmail,
        SendBlocked,
        build_email,
        deliver,
        last_outbound_in_thread,
        release_campaign_slot,
        reserve_campaign_slot,
        run_presend_checks,
    )
    from craftsman.sequencer.machine import Event
    from craftsman.sequencer.tick import apply_event, schedule_next_step

    from craftsman.core.logging import bind_log_context

    with session_scope() as db:
        enrollment = db.get(Enrollment, enrollment_id)
        if enrollment is None or enrollment.state != "ready":
            return
        lead = db.get(Lead, enrollment.lead_id)
        campaign = enrollment.campaign
        company = db.get(Company, lead.company_id)
        bind_log_context(enrollment_id=str(enrollment.id), lead_id=str(lead.id))

        # generation-time suppression re-check
        if is_suppressed(db, lead.email):
            apply_event(db, enrollment, Event.UNSUBSCRIBE, detail={"at": "generation"})
            return

        step_order = max(enrollment.current_step, 1)
        step = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign.id,
                SequenceStep.step_order == step_order,
            )
        )
        variants = list(
            db.scalars(select(Variant).where(Variant.step_id == step.id, Variant.active)).all()
        )
        if not variants:
            apply_event(db, enrollment, Event.SEND_FAILED, detail={"error": "no active variants"})
            return

        arms = [Arm(id=str(v.id), alpha=v.alpha, beta=v.beta) for v in variants]
        chosen_arm = pick_arm(arms)  # rng from get_bandit_rng() (seedable via BANDIT_SEED)
        variant = next(v for v in variants if str(v.id) == chosen_arm.id)

        brief = ResearchBrief.model_validate(company.research_brief or {})
        copy = _run(
            generate_copy(
                llm=get_llm(),
                brief=brief,
                skeleton=variant.skeleton,
                value_prop=campaign.value_prop,
                persona=campaign.sender_persona or {},
                first_name=lead.first_name or "",
            )
        )
        if not copy.ok:
            enrollment.state = "error"
            db.add(enrollment)
            db.add(
                ReviewQueueItem(
                    kind="copywriter",
                    enrollment_id=enrollment.id,
                    payload={"errors": copy.validation.errors, "slots": copy.slots},
                )
            )
            log.warning("copywriter rejected twice for %s", lead.email)
            return

        # pre-send checks (suppression / mailbox capacity / per-mailbox rate limit)
        try:
            mailbox = run_presend_checks(db, lead, campaign)
        except SendBlocked as e:
            if e.retry_in:
                raise self.retry(countdown=e.retry_in + 1)
            if e.reason == "no_mailbox_capacity":
                raise self.retry(countdown=3600)
            apply_event(db, enrollment, Event.UNSUBSCRIBE, detail={"at": "send", "reason": e.reason})
            return

        campaign_id = campaign.id

        # per-campaign daily cap: atomic reserve, committed immediately so the row lock
        # is not held across the SMTP send. Concurrent workers can't collectively exceed.
        if not reserve_campaign_slot(db, campaign):
            raise self.retry(countdown=3600)  # cap reached; resets at midnight
        db.commit()

        prev = last_outbound_in_thread(db, enrollment.id)
        if prev is not None and prev.smtp_message_id and prev.subject:
            subject = prev.subject if prev.subject.startswith("Re:") else f"Re: {prev.subject}"
        else:
            subject = copy.subject
        prepared = PreparedEmail(subject=subject, body=copy.body, to_email=lead.email)

        now = datetime.now(timezone.utc)
        wait_days = step.wait_days if step else 3

        # Idempotency claim: insert the outbound row (message_id/sent_at still NULL)
        # BEFORE any network I/O. The partial unique index on (enrollment_id,
        # step_order) makes an acks_late redelivery trip IntegrityError here and skip,
        # so a worker killed mid-send never produces a duplicate email.
        claim = Message(
            enrollment_id=enrollment.id,
            variant_id=variant.id,
            direction="outbound",
            step_order=step_order,
            mailbox_id=mailbox.id,
            subject=prepared.subject,
            body=prepared.body,
            bandit_outcome="pending",
            outcome_deadline=now + timedelta(days=wait_days),
        )
        db.add(claim)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()  # this step was already sent by a prior attempt
            release_campaign_slot(db, campaign_id)  # give back the slot we just reserved
            db.commit()
            log.info("duplicate send suppressed: enrollment %s step %s", enrollment_id, step_order)
            return
        db.commit()  # claim durable before we hit the wire

        in_reply_to = prev.smtp_message_id if (prev and prev.smtp_message_id) else None
        refs = [in_reply_to] if in_reply_to else None
        email_msg, message_id = build_email(
            db=db, mailbox=mailbox, lead=lead, prepared=prepared,
            in_reply_to=in_reply_to, references=refs,
        )
        try:
            _run(deliver(mailbox, email_msg))
        except Exception:
            # In-process failure means it did NOT send — drop the claim and free the
            # slot so the retry re-sends cleanly. (Only a hard crash leaves a stuck claim.)
            db.delete(claim)
            release_campaign_slot(db, campaign_id)
            db.commit()
            raise self.retry(countdown=60)

        # finalize the claimed row and advance the sequence
        claim.smtp_message_id = message_id
        claim.sent_at = now
        db.add(claim)
        mailbox.sent_today += 1
        db.add(mailbox)
        enrollment.current_step = step_order  # record which step was actually sent
        apply_event(db, enrollment, Event.SEND_OK, detail={"step": step_order})
        schedule_next_step(db, enrollment, wait_days, lead.timezone)


# ------------------------------------------------------------------ enrich


@app.task
def enrich_lead(lead_id: str):
    from craftsman.ingest.verify import verify_email

    with session_scope() as db:
        lead = db.get(Lead, lead_id)
        if lead is None:
            return
        if verify_email(lead.email):
            lead.email_verified = True
            if lead.status == "new":
                lead.status = "verified"
        db.add(lead)


# ------------------------------------------------------------------ inbox


@app.task
def poll_inboxes():
    from craftsman.core.config import get_settings
    from craftsman.inbox.pipeline import handle_inbound
    from craftsman.inbox.poller import fetch_mailpit, fetch_unseen

    llm = get_llm()
    settings = get_settings()
    with session_scope() as db:
        mailboxes = db.scalars(
            select(Mailbox).where(
                Mailbox.imap_host.isnot(None),
                Mailbox.imap_host != "",
            )
        ).all()
        for mailbox in mailboxes:
            for inbound in fetch_unseen(mailbox):
                _run(handle_inbound(db, llm, inbound, mailbox_id=mailbox.id))

        if settings.mailpit_url:
            for inbound in fetch_mailpit(db, settings.mailpit_url):
                # attribute to first healthy mailbox when present
                box_id = mailboxes[0].id if mailboxes else None
                if box_id is None:
                    any_box = db.scalars(select(Mailbox).limit(1)).first()
                    box_id = any_box.id if any_box else None
                _run(handle_inbound(db, llm, inbound, mailbox_id=box_id))


# ------------------------------------------------------------------ settle + housekeeping


@app.task
def settle_bandit():
    from craftsman.bandit.settle import settle_expired

    with session_scope() as db:
        return settle_expired(db)


@app.task
def reset_daily_counters():
    from sqlalchemy import update

    from craftsman.core.models import Campaign

    with session_scope() as db:
        for mailbox in db.scalars(select(Mailbox)).all():
            mailbox.sent_today = 0
            mailbox.hard_bounces_today = 0
            if mailbox.health == "degraded":
                mailbox.health = "ok"
            if mailbox.warmup_stage < 4:
                mailbox.warmup_stage += 1
            db.add(mailbox)
        # zero the per-campaign send counters used by the atomic cap reserve
        db.execute(update(Campaign).values(sent_today=0))
