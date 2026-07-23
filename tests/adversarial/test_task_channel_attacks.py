"""M3 adversarial: the assisted channels' safety properties (TESTING.md §3 method —
prediction stated above each test, then run).

The properties that must hold no matter what:
1. A task step NEVER produces an outbound email — not via the tick, not via a
   mis-enqueued generate_and_send, not via task resolution.
2. Task activity NEVER moves bandit posteriors (completion ≠ reply).
3. The idempotency claim holds: one task per (enrollment, step), ever.
4. Suppression gates task generation exactly like email generation.
5. The validator gates task content exactly like email content (magnitude attack).
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from craftsman.core.models import (
    Campaign,
    Company,
    Enrollment,
    Lead,
    Message,
    SequenceStep,
    TouchTask,
    Variant,
)
from craftsman.core.schemas import LinkedInSlotFill
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks as task_mod

GOOD_LI = LinkedInSlotFill(
    personalization_hook="saw Acme Robotics opened a new site in Austin.",
    value_bridge="Flowbot cuts picking costs by a third.",
    cta_question="Worth connecting?",
)

BRIEF_JSON = {
    "what_they_do": "Acme Robotics builds warehouse automation robots.",
    "industry": "logistics",
    "trigger_events": [
        {
            "claim": "Acme Robotics raised $4M and opened a new site in Austin",
            "source_url": "https://acme.com/news",
            "approx_date": "2026-05",
        }
    ],
    "likely_pain_points": ["manual picking costs"],
    "evidence_quotes": [],
}

LI_SKELETON = "Hi {{first_name}}, {{personalization_hook}} {{value_bridge}} {{cta_question}}"


def _scenario(db, *, channel="linkedin_task", state="ready", current_step=1):
    company = Company(domain=f"atk-{uuid.uuid4().hex[:8]}.test", name="Acme Robotics",
                      research_brief=BRIEF_JSON)
    db.add(company)
    db.flush()
    campaign = Campaign(
        name="atk", icp_description="x",
        value_prop="Flowbot cuts picking costs by a third.",
        sender_persona={"name": "Sam", "company": "Flowbot"},
    )
    db.add(campaign)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=1, channel=channel)
    db.add(step)
    db.flush()
    variant = None
    if channel != "call_task":
        variant = Variant(step_id=step.id, name="v1", skeleton=LI_SKELETON,
                          slot_schema={}, alpha=1.0, beta=1.0)
        db.add(variant)
        db.flush()
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@atk.test", company_id=company.id,
                status="verified", email_verified=True, first_name="Dana", timezone="UTC")
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state=state,
                     current_step=current_step, next_action_at=datetime.now(timezone.utc))
    db.add(enr)
    db.flush()
    return enr, lead, campaign, step, variant


@pytest.fixture()
def wired(db, monkeypatch):
    """Task worker wired to the test transaction, mock LLM, SMTP tripwired."""

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    llm = MockLLM()
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)

    # tripwire: ANY smtp delivery attempt during task-channel work is a failure
    async def _boom(*a, **k):
        raise AssertionError("SMTP deliver() invoked during a task-channel operation")

    monkeypatch.setattr("craftsman.sender.smtp.deliver", _boom)
    return llm


# Predicted: generate_touch_task creates exactly one open TouchTask, zero Message
# rows, and never touches SMTP (tripwire above would raise).
def test_task_generation_creates_task_and_no_email(db, wired):
    enr, lead, campaign, step, variant = _scenario(db)
    wired.enqueue(GOOD_LI)
    task_mod.generate_touch_task.run(str(enr.id))

    tasks = db.scalars(select(TouchTask).where(TouchTask.enrollment_id == enr.id)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "open"
    assert tasks[0].payload["message"].startswith("Hi Dana")
    messages = db.scalars(select(Message).where(Message.enrollment_id == enr.id)).all()
    assert messages == []  # property 1: no outbound email, no claim row, nothing
    assert enr.state == "awaiting_human_touch"
    assert enr.next_action_at is None  # Gate M3 default: task holds the sequence


# Predicted: generate_and_send on a linkedin step refuses to send — no Message row,
# no SMTP — and re-routes to the task generator instead.
def test_mis_enqueued_send_task_reroutes_never_emails(db, wired, monkeypatch):
    enr, *_ = _scenario(db)
    rerouted = []
    monkeypatch.setattr(
        task_mod.generate_touch_task, "delay", lambda eid: rerouted.append(eid)
    )
    task_mod.generate_and_send.run(str(enr.id))

    assert rerouted == [str(enr.id)]
    assert db.scalars(select(Message).where(Message.enrollment_id == enr.id)).all() == []
    campaign = db.get(Campaign, enr.campaign_id)
    assert campaign.sent_today == 0  # no cap slot consumed


# Predicted: a second generate_touch_task for the same (enrollment, step) trips the
# unique claim and leaves exactly one task. Runs on real committed sessions (not the
# rollback fixture) because the duplicate path's db.rollback() is the behavior under
# test — same harness pattern as test_send_idempotency.
def test_duplicate_generation_is_idempotent(engine, monkeypatch):
    from contextlib import contextmanager as _cm

    from sqlalchemy.orm import Session

    with Session(bind=engine) as setup:
        enr, lead, campaign, step, variant = _scenario(setup)
        enr_id = str(enr.id)
        lead_id, company_id = lead.id, lead.company_id
        campaign_id, step_id = campaign.id, step.id
        setup.commit()

    @_cm
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

    monkeypatch.setattr(task_mod, "session_scope", scope)
    llm = MockLLM()
    llm.respond_with(LinkedInSlotFill, lambda system, user: GOOD_LI.model_copy())
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)

    try:
        task_mod.generate_touch_task.run(enr_id)
        with Session(bind=engine) as s:
            e = s.get(Enrollment, uuid.UUID(enr_id))
            e.state = "ready"  # as a crash-retry would find it
            s.add(e)
            s.commit()
        task_mod.generate_touch_task.run(enr_id)

        with Session(bind=engine) as s:
            tasks = s.scalars(
                select(TouchTask).where(TouchTask.enrollment_id == uuid.UUID(enr_id))
            ).all()
            assert len(tasks) == 1
    finally:
        with Session(bind=engine) as s:
            s.query(TouchTask).filter(TouchTask.enrollment_id == uuid.UUID(enr_id)).delete()
            from craftsman.core.models import AuditLog

            s.query(AuditLog).filter(AuditLog.enrollment_id == uuid.UUID(enr_id)).delete()
            s.query(Enrollment).filter(Enrollment.id == uuid.UUID(enr_id)).delete()
            s.query(Variant).filter(Variant.step_id == step_id).delete()
            s.query(SequenceStep).filter(SequenceStep.campaign_id == campaign_id).delete()
            s.query(Lead).filter(Lead.id == lead_id).delete()
            s.query(Campaign).filter(Campaign.id == campaign_id).delete()
            s.query(Company).filter(Company.id == company_id).delete()
            s.commit()


# Predicted: completing and skipping tasks moves no variant posterior — alpha/beta
# stay at their priors bit-for-bit.
def test_task_resolution_never_updates_bandit(db, wired):
    from craftsman.sequencer.touch import resolve_task

    enr, lead, campaign, step, variant = _scenario(db)
    wired.enqueue(GOOD_LI)
    task_mod.generate_touch_task.run(str(enr.id))
    task = db.scalar(select(TouchTask).where(TouchTask.enrollment_id == enr.id))
    a0, b0 = variant.alpha, variant.beta

    resolve_task(db, task, "done", outcome="sent")
    db.flush()
    assert (variant.alpha, variant.beta) == (a0, b0)  # property 2


# Predicted: a suppressed lead's task step generates nothing and the enrollment
# routes to unsubscribed — same policy as email generation.
def test_suppressed_lead_generates_no_task(db, wired):
    from craftsman.compliance.suppression import suppress

    enr, lead, *_ = _scenario(db)
    suppress(db, lead.email, reason="unsubscribe")
    task_mod.generate_touch_task.run(str(enr.id))

    assert db.scalars(select(TouchTask).where(TouchTask.enrollment_id == enr.id)).all() == []
    assert enr.state == "unsubscribed"


# Predicted: the magnitude attack ($4M brief → $40M note) is rejected twice, the
# enrollment lands in error, and the rejected copy goes to human review — with the
# channel recorded.
def test_magnitude_attack_in_note_goes_to_review(db, wired):
    from craftsman.core.models import ReviewQueueItem

    enr, *_ = _scenario(db)
    bad = LinkedInSlotFill(
        personalization_hook="congrats on the $40M raise.",
        value_bridge="Flowbot cuts picking costs by a third.",
        cta_question="Worth connecting?",
    )
    wired.enqueue(bad)
    wired.enqueue(bad)
    task_mod.generate_touch_task.run(str(enr.id))

    assert enr.state == "error"
    assert db.scalars(select(TouchTask).where(TouchTask.enrollment_id == enr.id)).all() == []
    item = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.enrollment_id == enr.id))
    assert item is not None
    assert item.kind == "copywriter"
    assert item.payload["channel"] == "linkedin_task"
    assert any("$40M" in e for e in item.payload["errors"])


# Predicted: a call_task step generates a structured brief with no skeleton and no
# variant; the payload has opener/pain_hypotheses/objection_notes and no message.
def test_call_step_generates_brief_not_message(db, wired):
    from craftsman.core.schemas import CallBrief

    enr, *_ = _scenario(db, channel="call_task")
    wired.enqueue(CallBrief(
        opener="Calling from Flowbot about picking costs at Acme Robotics.",
        pain_hypotheses=["manual picking costs may be rising"],
        objection_notes="Flowbot cuts picking costs by a third.",
    ))
    task_mod.generate_touch_task.run(str(enr.id))

    task = db.scalar(select(TouchTask).where(TouchTask.enrollment_id == enr.id))
    assert task is not None
    assert task.channel == "call_task"
    assert task.variant_id is None
    assert set(task.payload["brief"]) == {"opener", "pain_hypotheses", "objection_notes"}
    assert "message" not in task.payload
    assert db.scalars(select(Message).where(Message.enrollment_id == enr.id)).all() == []


# Predicted: an expired-and-advanced task step hands off to an email step through
# the normal wait cycle — the email is generated by the email path with all its
# gates, not shortcut by the task path.
def test_expiry_advance_stays_in_state_machine(db, wired):
    enr, lead, campaign, step, variant = _scenario(db)
    step.skip_on_expire = True
    db.add(step)
    db.add(SequenceStep(campaign_id=campaign.id, step_order=2, wait_days=1, channel="email"))
    db.flush()
    wired.enqueue(GOOD_LI)
    task_mod.generate_touch_task.run(str(enr.id))
    assert enr.next_action_at is not None  # armed for expiry

    from craftsman.sequencer.tick import tick

    enr.next_action_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(enr)
    db.flush()
    sends = []
    tick(db, lambda e: None, sends.append, lambda e: None)
    assert enr.state == "waiting"  # advanced by expiry...
    assert sends == []  # ...but no send was dispatched now; the wait cycle owns it
    assert enr.next_action_at is not None  # scheduled into the next step's window
