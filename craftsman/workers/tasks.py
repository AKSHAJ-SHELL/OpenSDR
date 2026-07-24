"""Celery tasks — thin async-bridging wrappers around the pure modules."""

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from craftsman.core.db import session_scope
from craftsman.core.models import (
    Campaign,
    Company,
    Enrollment,
    Lead,
    Mailbox,
    Message,
    SequenceStep,
    Variant,
)
from craftsman.core.schemas import ResearchBrief
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.llm.client import get_llm
from craftsman.workers.celery_app import app

log = logging.getLogger(__name__)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@contextmanager
def _org_task_scope(model, entity_id):
    """session_scope + tenancy bootstrap for an id-addressed task (M5.1).

    The id came from our own queue, so the row load is a justified unscoped
    read; the row's org is entered before anything else runs, scoping every
    later query in the task body. Yields (db, row); row is None when the id
    no longer resolves (erased, rolled back) — the tasks' existing
    early-return contract."""
    with session_scope() as db:
        with unscoped_context():
            row = db.get(model, entity_id)
        if row is None:
            yield db, None
        else:
            with org_context(row.org_id):
                yield db, row


def _org_ids(db):
    """All org ids — for beat sweeps, which iterate tenants one org at a time
    so every row they create is stamped into the org it belongs to (M5.1)."""
    from craftsman.core.models import Org

    with unscoped_context():
        return list(db.scalars(select(Org.id)))


# ------------------------------------------------------------------ sequencer


@app.task
def sequencer_tick():
    from craftsman.sequencer.tick import tick

    with session_scope() as db:
        handled = 0
        for oid in _org_ids(db):
            with org_context(oid):
                handled += tick(
                    db,
                    enqueue_research=lambda eid: research_enrollment.delay(eid),
                    enqueue_send=lambda eid: generate_and_send.delay(eid),
                    enqueue_task=lambda eid: generate_touch_task.delay(eid),
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

    with _org_task_scope(Enrollment, enrollment_id) as (db, enrollment):
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
        release_org_slot,
        reserve_campaign_slot,
        reserve_org_slot,
        run_presend_checks,
    )
    from craftsman.sequencer.machine import Event
    from craftsman.sequencer.tick import apply_event, schedule_next_step

    from craftsman.core.logging import bind_log_context

    with _org_task_scope(Enrollment, enrollment_id) as (db, enrollment):
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
        # Channel guard (M3.1): email is the only channel this task may ever act on.
        # A mis-enqueued task step must not produce an email — re-route it to the
        # task generator, never send.
        if step is not None and step.channel != "email":
            log.warning(
                "generate_and_send re-routing %s step %s (enrollment %s) to task queue",
                step.channel, step_order, enrollment_id,
            )
            generate_touch_task.delay(enrollment_id)
            return
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

        from craftsman.core.metrics import record_rejection

        # pre-send checks (suppression / mailbox capacity / per-mailbox rate limit)
        try:
            mailbox = run_presend_checks(db, lead, campaign)
        except SendBlocked as e:
            record_rejection(e.reason)
            if e.retry_in:
                raise self.retry(countdown=e.retry_in + 1)
            if e.reason == "no_mailbox_capacity":
                raise self.retry(countdown=3600)
            apply_event(db, enrollment, Event.UNSUBSCRIBE, detail={"at": "send", "reason": e.reason})
            return

        campaign_id = campaign.id
        org_id = campaign.org_id

        # per-campaign daily cap: atomic reserve, committed immediately so the row lock
        # is not held across the SMTP send. Concurrent workers can't collectively exceed.
        if not reserve_campaign_slot(db, campaign):
            record_rejection("campaign_daily_cap")
            raise self.retry(countdown=3600)  # cap reached; resets at midnight
        # per-org daily cap (M5.1c): a second atomic reserve; NULL = unlimited
        if not reserve_org_slot(db, org_id):
            release_campaign_slot(db, campaign_id)
            db.commit()
            record_rejection("org_daily_cap")
            raise self.retry(countdown=3600)
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
            release_campaign_slot(db, campaign_id)  # give back the slots we just reserved
            release_org_slot(db, org_id)
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
            release_org_slot(db, org_id)
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


# ------------------------------------------------------------------ touch tasks (M3)


@app.task(bind=True, max_retries=3)
def generate_touch_task(self, enrollment_id: str):
    """Assisted-channel step: generate validated content and queue it for a human.

    Mirrors generate_and_send's policies exactly — suppression re-check, uniform
    variant pick (no posterior updates: completion ≠ reply), validate-twice-then-
    review-queue, idempotency claim — but the output is a TouchTask, never an email.
    Nothing here touches SMTP, mailbox counters, campaign caps, or the bandit.
    """
    from sqlalchemy.exc import IntegrityError

    from craftsman.bandit.thompson import Arm, pick_arm
    from craftsman.channels import get_channel
    from craftsman.compliance.suppression import is_suppressed
    from craftsman.copywriter.task_fill import generate_call_brief, generate_linkedin_copy
    from craftsman.core.config import get_settings
    from craftsman.core.logging import bind_log_context
    from craftsman.core.models import ReviewQueueItem, TouchTask
    from craftsman.sequencer.machine import Event
    from craftsman.sequencer.scheduling import add_business_days
    from craftsman.sequencer.tick import apply_event

    with _org_task_scope(Enrollment, enrollment_id) as (db, enrollment):
        if enrollment is None or enrollment.state != "ready":
            return
        lead = db.get(Lead, enrollment.lead_id)
        campaign = enrollment.campaign
        company = db.get(Company, lead.company_id)
        bind_log_context(enrollment_id=str(enrollment.id), lead_id=str(lead.id))

        # generation-time suppression re-check — do-not-contact gates every channel
        if is_suppressed(db, lead.email):
            apply_event(db, enrollment, Event.UNSUBSCRIBE, detail={"at": "task_generation"})
            return

        step_order = max(enrollment.current_step, 1)
        step = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign.id,
                SequenceStep.step_order == step_order,
            )
        )
        if step is None or not get_channel(step.channel).assisted:
            log.error(
                "generate_touch_task refused step %s (channel %s) for enrollment %s",
                step_order, step.channel if step else "?", enrollment_id,
            )
            return

        brief = ResearchBrief.model_validate(company.research_brief or {})
        variant = None
        if step.channel == "linkedin_task":
            variants = list(
                db.scalars(
                    select(Variant).where(Variant.step_id == step.id, Variant.active)
                ).all()
            )
            if not variants:
                enrollment.state = "error"
                db.add(enrollment)
                db.add(ReviewQueueItem(
                    kind="copywriter",
                    enrollment_id=enrollment.id,
                    payload={"errors": ["no active variants on linkedin step"], "channel": step.channel},
                ))
                return
            # uniform rotation: priors never update for task channels (M3.3 decision),
            # so Thompson sampling over Beta(1,1) is an unbiased coin — reused for
            # consistency and the day M6 revisits reward semantics
            arms = [Arm(id=str(v.id), alpha=v.alpha, beta=v.beta) for v in variants]
            chosen = pick_arm(arms)
            variant = next(v for v in variants if str(v.id) == chosen.id)
            copy = _run(generate_linkedin_copy(
                llm=get_llm(),
                brief=brief,
                skeleton=variant.skeleton,
                value_prop=campaign.value_prop,
                persona=campaign.sender_persona or {},
                first_name=lead.first_name or "",
            ))
        else:  # call_task
            copy = _run(generate_call_brief(
                llm=get_llm(),
                brief=brief,
                value_prop=campaign.value_prop,
                persona=campaign.sender_persona or {},
            ))

        if not copy.ok:
            enrollment.state = "error"
            db.add(enrollment)
            db.add(ReviewQueueItem(
                kind="copywriter",
                enrollment_id=enrollment.id,
                payload={
                    "errors": copy.validation.errors if copy.validation else [],
                    "slots": copy.slots,
                    "channel": step.channel,
                },
            ))
            log.warning("task copywriter rejected twice for %s (%s)", lead.email, step.channel)
            return

        now = datetime.now(timezone.utc)
        due = add_business_days(now, get_settings().touch_task_due_days)
        task_row = TouchTask(
            enrollment_id=enrollment.id,
            step_order=step_order,
            channel=step.channel,
            variant_id=variant.id if variant else None,
            payload=copy.payload,
            status="open",
            due_at=due,
        )
        db.add(task_row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()  # this step already has a task from a prior attempt
            log.info("duplicate task suppressed: enrollment %s step %s", enrollment_id, step_order)
            return

        enrollment.current_step = step_order
        apply_event(
            db, enrollment, Event.TASK_CREATED,
            detail={"channel": step.channel, "task_id": str(task_row.id), "due_at": due.isoformat()},
        )
        # Gate M3 default: an undone task holds the sequence (next_action_at stays
        # NULL). Only skip_on_expire steps arm the tick to expire-and-advance.
        enrollment.next_action_at = due if step.skip_on_expire else None
        db.add(enrollment)


# ------------------------------------------------------------------ reply drafts (M4.1)


@app.task(bind=True, max_retries=3)
def generate_reply_draft(self, inbound_message_id: str):
    """Copilot: draft a validated reply for a confident interested/objection reply.

    Same policies as every generator — suppression re-check, validate-twice-then-
    review-queue, idempotency claim (UNIQUE inbound_message_id) — and the same hard
    line: this creates a ReplyDraft row, never an email. Dispatch is a separate,
    human-clicked path (sender/reply.py). Nothing here touches SMTP, mailbox
    counters, campaign caps, or the bandit.
    """
    from sqlalchemy.exc import IntegrityError

    from craftsman.compliance.suppression import is_suppressed
    from craftsman.copywriter.reply_fill import generate_reply_copy
    from craftsman.core.models import ReplyDraft, ReviewQueueItem

    with _org_task_scope(Message, inbound_message_id) as (db, inbound):
        if inbound is None or inbound.direction != "inbound":
            return
        if inbound.classification not in ("interested", "objection"):
            return
        enrollment = db.get(Enrollment, inbound.enrollment_id) if inbound.enrollment_id else None
        if enrollment is None:
            return
        lead = db.get(Lead, enrollment.lead_id)
        campaign = db.get(Campaign, enrollment.campaign_id)
        company = db.get(Company, lead.company_id) if lead else None

        from craftsman.core.logging import bind_log_context

        bind_log_context(enrollment_id=str(enrollment.id), message_id=str(inbound.id))

        # do-not-contact gates drafting too: a suppressed lead gets no draft at all
        if lead is None or is_suppressed(db, lead.email):
            return

        # idempotency claim BEFORE the LLM call — a redelivered task must not
        # burn tokens or race a human already acting on the first draft
        claim = ReplyDraft(
            inbound_message_id=inbound.id,
            enrollment_id=enrollment.id,
            status="generating",
        )
        db.add(claim)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            log.info("duplicate reply draft suppressed for message %s", inbound_message_id)
            return
        db.commit()  # claim durable before the LLM round-trip

        brief = ResearchBrief.model_validate(
            (company.research_brief if company else None) or {}
        )
        scheduling_line = _reply_scheduling_line(campaign)
        info_line = _reply_info_line(campaign)
        try:
            copy = _run(generate_reply_copy(
                llm=get_llm(),
                brief=brief,
                value_prop=campaign.value_prop,
                persona=campaign.sender_persona or {},
                first_name=lead.first_name or "",
                reply_text=inbound.body or "",
                label=inbound.classification,
                scheduling_line=scheduling_line,
                info_line=info_line,
            ))
        except Exception:
            # in-process failure: drop the claim so the retry regenerates cleanly
            # (only a hard crash leaves a stuck 'generating' row — same caveat as
            # the email send claim)
            db.delete(claim)
            db.commit()
            raise self.retry(countdown=60)

        now = datetime.now(timezone.utc)
        if copy.ok:
            claim.skeleton_key = copy.skeleton_key
            claim.slots = copy.slots
            claim.body = copy.body
            claim.status = "pending"
            claim.detail = {"attempts": copy.attempts}
            db.add(claim)
            # M4.4: Guarded Autopilot may dispatch this one draft — same validator,
            # same send engine, policy-gated. Any refusal leaves the pending draft
            # for a human (Copilot behavior is the fallback, always).
            _maybe_autopilot_send(db, claim, inbound, enrollment, campaign, lead)
            return
        elif copy.skipped_reason:
            claim.status = "skipped"
            claim.resolved_at = now
            claim.detail = {"reason": copy.skipped_reason}
        else:
            claim.status = "failed"
            claim.resolved_at = now
            claim.detail = {
                "errors": copy.validation.errors if copy.validation else [],
                "slots": copy.slots,
            }
            db.add(ReviewQueueItem(
                kind="reply_draft",
                message_id=inbound.id,
                enrollment_id=enrollment.id,
                payload={
                    "errors": copy.validation.errors if copy.validation else [],
                    "slots": copy.slots,
                },
            ))
            log.warning("reply copywriter rejected twice for %s", lead.email)
        db.add(claim)


def _maybe_autopilot_send(db, draft, inbound, enrollment, campaign, lead):
    """Evaluate the Autopilot policy for a fresh pending draft; dispatch iff every
    gate passes. Never raises — a refused or failed auto-send leaves the pending
    draft exactly where Copilot would have left it."""
    from craftsman.core.config import get_settings
    from craftsman.core.models import AuditLog
    from craftsman.inbox.autopilot import AutopilotContext, decide, prior_auto_replies
    from craftsman.inbox.escalation import evaluate, load_rules
    from craftsman.sender.reply import DraftUnavailable, send_reply_draft
    from craftsman.sender.smtp import SendBlocked

    settings = get_settings()
    rules = load_rules(db, campaign.id, settings.classifier_confidence_threshold)
    escalation = evaluate(
        rules,
        label=inbound.classification or "",
        confidence=inbound.classification_confidence or 0.0,
        reply_text=inbound.body or "",
    )
    ctx = AutopilotContext(
        autopilot_enabled=campaign.autopilot_enabled,
        skeleton_key=draft.skeleton_key,
        classification_confidence=inbound.classification_confidence,
        escalation_blocks=escalation.block_autopilot,
        prior_auto_replies_in_thread=prior_auto_replies(db, enrollment.id),
        scheduling_url=campaign.scheduling_url,
        info_doc_url=campaign.info_doc_url,
        now_utc=datetime.now(timezone.utc),
        lead_tz=lead.timezone,
    )
    decision = decide(ctx)
    if not decision.send:
        if campaign.autopilot_enabled:
            # the operator opted in — record WHY this one stayed with a human
            log.info("autopilot declined draft %s: %s", draft.id, decision.reason)
            db.add(AuditLog(
                enrollment_id=enrollment.id,
                event="autopilot_declined",
                detail={"draft_id": str(draft.id), "reason": decision.reason},
            ))
        return

    db.commit()  # pending status durable before the dispatch CAS reads the row
    try:
        _run(send_reply_draft(db, draft, auto=True))
        db.add(AuditLog(
            enrollment_id=enrollment.id,
            event="autopilot_send",
            detail={
                "draft_id": str(draft.id),
                "skeleton": draft.skeleton_key,
                "confidence": inbound.classification_confidence,
            },
        ))
        log.info("autopilot dispatched draft %s (%s)", draft.id, draft.skeleton_key)
    except SendBlocked as e:
        # rate limit / capacity → draft is back in pending for a human; suppression
        # → discarded. Either way the policy result is honest and audited.
        log.warning("autopilot send blocked (%s) for draft %s", e.reason, draft.id)
        db.add(AuditLog(
            enrollment_id=enrollment.id,
            event="autopilot_declined",
            detail={"draft_id": str(draft.id), "reason": f"send_blocked:{e.reason}"},
        ))
    except DraftUnavailable as e:
        # lost the structural thread-guard race (F-05): another auto-reply claimed
        # this thread between our policy read and the dispatch CAS — the draft
        # stays pending for a human, which is exactly the invariant's promise
        log.warning("autopilot claim refused for draft %s: %s", draft.id, e)
        db.add(AuditLog(
            enrollment_id=enrollment.id,
            event="autopilot_declined",
            detail={"draft_id": str(draft.id), "reason": "thread_guard"},
        ))
    except Exception as e:  # never fail the generation task over an auto-send
        log.warning("autopilot send failed for draft %s: %s", draft.id, e)


def _reply_scheduling_line(campaign: Campaign) -> str:
    """Static scheduling-link line for interested drafts — campaign config
    (campaigns.scheduling_url, M4.3), never LLM content."""
    url = campaign.scheduling_url
    return f"If it is easier, grab any time here: {url}" if url else ""


def _reply_info_line(campaign: Campaign) -> str:
    """Static one-pager line for info-objection drafts — campaign config only."""
    url = campaign.info_doc_url
    return f"Here is the short version: {url}" if url else ""


# ------------------------------------------------------------------ dry run (M1.2)


@app.task
def run_dry_run(dry_run_id: str):
    """Preflight: the real pipeline (research → bandit pick → fill → validate → build →
    send) for N sample leads, delivered to Mailpit only. By construction this touches
    none of the production state: no enrollments, no Message rows, no campaign slots,
    no mailbox counters, no bandit updates, no unsubscribe tokens, and lead scores are
    computed in memory without being persisted."""
    from craftsman.bandit.thompson import Arm, pick_arm
    from craftsman.compliance.suppression import is_suppressed
    from craftsman.copywriter.fill import generate_copy
    from craftsman.core.models import DryRun, DryRunItem
    from craftsman.research.agent import research_company
    from craftsman.scoring.embeddings import get_embedder
    from craftsman.scoring.icp import icp_score, lead_text
    from craftsman.sender.smtp import PreparedEmail, build_dry_run_email, deliver_to_mailpit

    with _org_task_scope(DryRun, dry_run_id) as (db, run):
        if run is None or run.status != "running":
            return
        campaign = db.get(Campaign, run.campaign_id)

        try:
            step = db.scalar(
                select(SequenceStep).where(
                    SequenceStep.campaign_id == campaign.id, SequenceStep.step_order == 1
                )
            )
            variants = list(
                db.scalars(
                    select(Variant).where(Variant.step_id == step.id, Variant.active)
                ).all()
            ) if step else []
            if not variants:
                raise RuntimeError("no active variants on step 1")

            embedder = get_embedder()
            icp_emb = (
                list(campaign.icp_embedding)
                if campaign.icp_embedding is not None
                else _run(embedder.embed([campaign.icp_description]))[0]
            )

            # Score candidates in memory — unlike activate, nothing is persisted.
            candidates = []
            for lead in db.scalars(
                select(Lead).where(Lead.email_verified.is_(True), Lead.status == "verified")
            ).all():
                if is_suppressed(db, lead.email):
                    continue
                company = db.get(Company, lead.company_id) if lead.company_id else None
                text = lead_text(lead.title, company.name if company else None, None)
                lead_emb = _run(embedder.embed([text]))[0]
                candidates.append((icp_score(lead_emb, icp_emb, lead.title), lead, company))
            candidates.sort(key=lambda c: c[0], reverse=True)

            for score, lead, company in candidates[: run.requested_n]:
                item = DryRunItem(
                    dry_run_id=run.id,
                    lead_id=lead.id,
                    lead_email=lead.email,
                    lead_name=" ".join(
                        x for x in [lead.first_name, lead.last_name] if x
                    ) or None,
                    icp_score=score,
                )
                db.add(item)
                try:
                    brief = _run(research_company(db, company, get_llm()))
                    arms = [Arm(id=str(v.id), alpha=v.alpha, beta=v.beta) for v in variants]
                    chosen = next(v for v in variants if str(v.id) == pick_arm(arms).id)
                    item.variant_id = chosen.id
                    item.variant_name = chosen.name
                    copy = _run(
                        generate_copy(
                            llm=get_llm(),
                            brief=brief,
                            skeleton=chosen.skeleton,
                            value_prop=campaign.value_prop,
                            persona=campaign.sender_persona or {},
                            first_name=lead.first_name or "",
                        )
                    )
                    item.validator_ok = copy.ok
                    if copy.ok:
                        item.subject, item.body = copy.subject, copy.body
                        prepared = PreparedEmail(
                            subject=copy.subject, body=copy.body, to_email=lead.email
                        )
                        _run(deliver_to_mailpit(build_dry_run_email(prepared)))
                        item.delivered = True
                    else:
                        item.validator_errors = copy.validation.errors
                except Exception as e:  # noqa: BLE001 — a bad lead never kills the run
                    item.error = str(e)
                db.add(item)
                db.commit()  # each item durable as it lands, so the UI can stream

            run.status = "complete"
            if not candidates:
                run.error = "no verified, unsuppressed leads to sample"
        except Exception as e:  # noqa: BLE001
            log.warning("dry run %s failed: %s", dry_run_id, e)
            run.status = "failed"
            run.error = str(e)
        run.finished_at = datetime.now(timezone.utc)
        db.add(run)


# ------------------------------------------------------------------ enrich


@app.task
def enrich_lead(lead_id: str):
    """Verify, then enrich. Order matters twice over: enrichment only spends
    provider API budget on addresses that verified, and any enrichment failure
    is swallowed so it can never cost the lead its verification."""
    from craftsman.core.config import get_settings
    from craftsman.ingest.enrichment import (
        EnrichmentInput,
        apply_enrichment,
        build_enrichment_chain,
        chain_enrich,
        reserve_enrichment_calls,
    )
    from craftsman.ingest.verify import verify_email

    with _org_task_scope(Lead, lead_id) as (db, lead):
        if lead is None:
            return
        if verify_email(lead.email):
            lead.email_verified = True
            if lead.status == "new":
                lead.status = "verified"
        db.add(lead)

        if not lead.email_verified:
            return
        try:  # noqa: SIM105 — enrichment must never un-verify a lead
            chain = build_enrichment_chain(get_settings())
            if not chain:
                return
            # per-org daily enrichment budget (M5.1c): exhausted ⇒ verify-only
            if not reserve_enrichment_calls(db, lead.org_id, len(chain)):
                log.info(
                    "enrichment budget exhausted for org %s — verify-only for lead %s",
                    lead.org_id, lead_id,
                )
                return
            company = db.get(Company, lead.company_id) if lead.company_id else None
            inp = EnrichmentInput(
                email=lead.email, company_domain=company.domain if company else None
            )
            merged, provenance = _run(chain_enrich(chain, inp))
            if merged:
                apply_enrichment(db, lead, merged, provenance)
        except Exception as e:  # noqa: BLE001
            log.warning("enrichment for lead %s failed (verify kept): %s", lead_id, e)


# ------------------------------------------------------------------ signals


@app.task
def collect_signals():
    """Run enabled intent collectors, persist new signals, and fire matching signal_rules
    (boost/notify/enroll). Disabled by default — a no-op unless SIGNAL_COLLECTORS is set."""
    from craftsman.core.config import get_settings
    from craftsman.core.models import Signal
    from craftsman.scoring.collectors import build_collectors
    from craftsman.scoring.rules import evaluate_rules

    settings = get_settings()
    collectors = build_collectors(settings)
    if not collectors:
        return 0

    notify = _slack_notifier(settings)
    total = 0

    def _collect_for_current_org(db) -> int:
        n = 0
        for collector in collectors:
            try:
                collected = _run(collector.collect(db))
            except Exception as e:  # noqa: BLE001 — one collector never sinks the sweep
                log.warning("collector %s failed: %s", getattr(collector, "name", "?"), e)
                continue
            for cs in collected:
                signal = Signal(
                    company_id=cs.company_id, type=cs.type, payload=cs.payload, source=cs.source
                )
                db.add(signal)
                db.flush()
                evaluate_rules(db, signal, settings.icp_threshold, notify=notify)
                n += 1
        return n

    with session_scope() as db:
        for oid in _org_ids(db):
            with org_context(oid):
                total += _collect_for_current_org(db)
    log.info("collect_signals recorded %d new signals", total)
    return total


def _slack_notifier(settings):
    """Return a callable(text) that posts to Slack, or None if no webhook is configured."""
    url = settings.slack_webhook_url
    if not url:
        return None

    def _notify(text: str):
        try:
            import httpx

            httpx.post(url, json={"text": text}, timeout=5)
        except Exception as e:  # noqa: BLE001
            log.warning("slack notify failed: %s", e)

    return _notify


# ------------------------------------------------------------------ inbox


@app.task
def poll_inboxes():
    from craftsman.core.config import get_settings
    from craftsman.inbox.pipeline import handle_inbound
    from craftsman.inbox.poller import fetch_mailpit, fetch_unseen

    llm = get_llm()
    settings = get_settings()
    enqueue_draft = lambda message_id: generate_reply_draft.delay(str(message_id))  # noqa: E731
    with session_scope() as db:
        for oid in _org_ids(db):
            with org_context(oid):
                mailboxes = db.scalars(
                    select(Mailbox).where(
                        Mailbox.imap_host.isnot(None),
                        Mailbox.imap_host != "",
                    )
                ).all()
                for mailbox in mailboxes:
                    for inbound in fetch_unseen(mailbox):
                        _run(handle_inbound(
                            db, llm, inbound, mailbox_id=mailbox.id, enqueue_draft=enqueue_draft
                        ))

                if settings.mailpit_url:
                    for inbound in fetch_mailpit(db, settings.mailpit_url):
                        # attribute to first healthy mailbox when present
                        box_id = mailboxes[0].id if mailboxes else None
                        if box_id is None:
                            any_box = db.scalars(select(Mailbox).limit(1)).first()
                            box_id = any_box.id if any_box else None
                        _run(handle_inbound(
                            db, llm, inbound, mailbox_id=box_id, enqueue_draft=enqueue_draft
                        ))


# ------------------------------------------------------------------ settle + housekeeping


@app.task
def settle_bandit():
    from craftsman.bandit.settle import settle_expired

    with session_scope() as db:
        total = 0
        for oid in _org_ids(db):
            with org_context(oid):
                total += settle_expired(db)
        return total


@app.task
def redrive_unsent():
    """Periodic sweep: re-drive outbound claims left unsent by a hard crash."""
    from craftsman.core.config import get_settings
    from craftsman.sequencer.redrive import redrive_unsent_claims

    with session_scope() as db:
        n = 0
        for oid in _org_ids(db):
            with org_context(oid):
                n += redrive_unsent_claims(
                    db, after_minutes=get_settings().redrive_unsent_after_minutes
                )
    if n:
        log.info("redrive_unsent swept %d stuck claim(s)", n)
    return n


@app.task
def reset_daily_counters():
    from sqlalchemy import update

    from craftsman.core.models import Campaign

    with session_scope() as db:
        for oid in _org_ids(db):
            with org_context(oid):
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
        # zero the per-org quota counters (M5.1c) — Org is not org-scoped, so this
        # single UPDATE covers every tenant
        from craftsman.core.models import Org

        db.execute(update(Org).values(sent_today=0, enrichment_calls_today=0))
