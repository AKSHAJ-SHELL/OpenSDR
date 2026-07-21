"""Send idempotency (M0.6a, finding B3b).

Two layers:
  - the partial unique index enforces at-most-one outbound per (enrollment, step)
    (pure DB tests on the rollback fixture);
  - the send task claims that row BEFORE delivering, so an acks_late redelivery that
    finds an existing claim skips instead of sending a duplicate (task-level tests on
    dedicated committed sessions, since the task commits internally).
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from craftsman.core.schemas import SlotFill
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks

SKELETON = "Subject: {{subject_hook}}\n\nHi {{first_name}},\n\n{{personalization_sentence}} {{value_prop_bridge}} {{cta_question}}\n\n{{signature}}"

# A fill with no proper nouns/numbers (grounds trivially), no banned phrases, short.
GOOD_FILL = SlotFill(
    subject_hook="a note on operations",
    personalization_sentence="you focus on operations work.",
    value_prop_bridge="we help teams cut costs.",
    cta_question="worth a look?",
)


# ---------------------------------------------------------------- pure index tests


def _minimal_enrollment(db):
    company = Company(domain=f"idx-{uuid.uuid4().hex[:8]}.test")
    db.add(company)
    db.flush()
    campaign = Campaign(name="idx", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@idx.test", company_id=company.id, status="verified")
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="ready", current_step=1)
    db.add(enr)
    db.flush()
    return enr


def _outbound(enr_id, step):
    return Message(
        enrollment_id=enr_id, direction="outbound", step_order=step,
        subject="s", body="b", bandit_outcome="pending",
    )


def test_unique_index_blocks_duplicate_outbound_step(db):
    enr = _minimal_enrollment(db)
    db.add(_outbound(enr.id, 1))
    db.flush()
    db.add(_outbound(enr.id, 1))  # same (enrollment, step) → violates the partial index
    with pytest.raises(IntegrityError):
        db.flush()


def test_different_steps_are_allowed(db):
    enr = _minimal_enrollment(db)
    db.add(_outbound(enr.id, 1))
    db.add(_outbound(enr.id, 2))
    db.flush()  # no raise — different steps
    n = db.scalar(
        select(func.count(Message.id)).where(Message.enrollment_id == enr.id)
    )
    assert n == 2


def test_inbound_messages_are_not_constrained(db):
    enr = _minimal_enrollment(db)
    db.add(Message(enrollment_id=enr.id, direction="inbound", subject="r1", body="a"))
    db.add(Message(enrollment_id=enr.id, direction="inbound", subject="r2", body="b"))
    db.flush()  # partial index is WHERE direction='outbound' — inbound unaffected
    n = db.scalar(
        select(func.count(Message.id)).where(
            Message.enrollment_id == enr.id, Message.direction == "inbound"
        )
    )
    assert n == 2


# ---------------------------------------------------------------- task-level tests


def _setup_sendable(engine):
    """Commit a complete sendable scenario. Returns an ids dict."""
    with Session(bind=engine) as s:
        company = Company(
            domain=f"send-{uuid.uuid4().hex[:8]}.test",
            research_brief={"what_they_do": "operations software", "industry": "ops"},
            research_fetched_at=datetime.now(timezone.utc),
        )
        s.add(company)
        s.flush()
        campaign = Campaign(
            name=f"send-{uuid.uuid4().hex[:6]}", icp_description="ops",
            value_prop="we help teams cut costs", daily_cap=100, status="active",
            sender_persona={"name": "Sam", "title": "Founder"},
        )
        s.add(campaign)
        s.flush()
        step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=0)
        s.add(step)
        s.flush()
        variant = Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={})
        s.add(variant)
        lead = Lead(
            email=f"{uuid.uuid4().hex[:8]}@send.test", company_id=company.id,
            first_name=None, status="verified", email_verified=True,
        )
        s.add(lead)
        s.flush()
        enr = Enrollment(
            lead_id=lead.id, campaign_id=campaign.id, state="ready", current_step=1,
            next_action_at=datetime.now(timezone.utc),
        )
        s.add(enr)
        mailbox = Mailbox(
            email=f"box-{uuid.uuid4().hex[:6]}@send.test", smtp_host="localhost",
            smtp_port=1025, daily_limit=1000, warmup_stage=4, health="ok",
        )
        s.add(mailbox)
        s.commit()
        return {
            "enrollment_id": str(enr.id), "campaign_id": campaign.id,
            "company_id": company.id, "lead_id": lead.id,
            "lead_email": lead.email, "mailbox_id": mailbox.id,
        }


def _cleanup(engine, ids):
    from craftsman.core.models import AuditLog, SuppressionEntry, UnsubscribeToken

    with Session(bind=engine) as s:
        enr = s.get(Enrollment, uuid.UUID(ids["enrollment_id"]))
        if enr is not None:
            s.query(Message).filter(Message.enrollment_id == enr.id).delete()
            s.query(AuditLog).filter(AuditLog.enrollment_id == enr.id).delete()
            s.delete(enr)
        s.query(UnsubscribeToken).filter(
            UnsubscribeToken.lead_email == ids["lead_email"].lower()
        ).delete()
        s.query(Variant).filter(
            Variant.step_id.in_(
                select(SequenceStep.id).where(SequenceStep.campaign_id == ids["campaign_id"])
            )
        ).delete(synchronize_session=False)
        s.query(SequenceStep).filter(SequenceStep.campaign_id == ids["campaign_id"]).delete()
        lead = s.get(Lead, ids["lead_id"])
        if lead is not None:
            s.delete(lead)
        camp = s.get(Campaign, ids["campaign_id"])
        if camp is not None:
            s.delete(camp)
        box = s.get(Mailbox, ids["mailbox_id"])
        if box is not None:
            s.delete(box)
        comp = s.get(Company, ids["company_id"])
        if comp is not None:
            s.delete(comp)
        s.query(SuppressionEntry).filter(
            SuppressionEntry.email == ids["lead_email"].lower()
        ).delete()
        s.commit()


def _patch_send(engine, monkeypatch):
    """Patch the task to run on the test engine with a mock LLM and a counting deliver.
    Returns the deliver-call list."""
    deliver_calls: list = []

    async def fake_deliver(mailbox, msg):
        deliver_calls.append(1)

    mock = MockLLM()
    mock.respond_with(SlotFill, lambda system, user: GOOD_FILL.model_copy())

    monkeypatch.setattr("craftsman.sender.smtp.deliver", fake_deliver)
    monkeypatch.setattr("craftsman.sender.smtp.acquire_send_slot", lambda *a, **k: 0.0)
    monkeypatch.setattr(tasks, "get_llm", lambda: mock)

    @contextmanager
    def scope():
        s = Session(bind=engine)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(tasks, "session_scope", scope)
    return deliver_calls


def test_happy_path_sends_exactly_once(engine, monkeypatch):
    ids = _setup_sendable(engine)
    try:
        deliver_calls = _patch_send(engine, monkeypatch)
        tasks.generate_and_send.run(ids["enrollment_id"])

        assert len(deliver_calls) == 1
        with Session(bind=engine) as s:
            msgs = s.scalars(
                select(Message).where(
                    Message.enrollment_id == uuid.UUID(ids["enrollment_id"]),
                    Message.direction == "outbound",
                )
            ).all()
            assert len(msgs) == 1
            assert msgs[0].sent_at is not None and msgs[0].smtp_message_id
            assert s.get(Campaign, ids["campaign_id"]).sent_today == 1  # one slot used
    finally:
        _cleanup(engine, ids)


def test_existing_claim_suppresses_resend(engine, monkeypatch):
    """Simulates an acks_late redelivery after a crash that committed the claim but not
    the finalize: the claim row exists and the enrollment is still 'ready'. The retry
    must NOT deliver again, and must release the slot it briefly reserved."""
    ids = _setup_sendable(engine)
    try:
        # a prior attempt's claim (sent_at NULL, as if the worker died before finalize)
        with Session(bind=engine) as s:
            s.add(Message(
                enrollment_id=uuid.UUID(ids["enrollment_id"]), direction="outbound",
                step_order=1, subject="prior", body="prior", bandit_outcome="pending",
            ))
            s.commit()

        deliver_calls = _patch_send(engine, monkeypatch)
        tasks.generate_and_send.run(ids["enrollment_id"])

        assert deliver_calls == []  # duplicate claim → no second send
        with Session(bind=engine) as s:
            n = s.scalar(
                select(func.count(Message.id)).where(
                    Message.enrollment_id == uuid.UUID(ids["enrollment_id"]),
                    Message.direction == "outbound",
                )
            )
            assert n == 1  # still just the one claim
            assert s.get(Campaign, ids["campaign_id"]).sent_today == 0  # reserved then released
    finally:
        _cleanup(engine, ids)
