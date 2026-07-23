"""M3.1: touch-task lifecycle against real Postgres — resolution advances the
sequence, expiry honors skip_on_expire (⛔ Gate M3: default holds), replies cancel
open tasks, suppression gates the queue, erasure removes task PII.

No LLM here: tasks are created directly (generation is covered by the task-fill
tests); this file proves the state plumbing around them.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from craftsman.core.models import (
    AuditLog,
    Campaign,
    Company,
    Enrollment,
    Lead,
    SequenceStep,
    TouchTask,
)
from craftsman.sequencer.tick import tick
from craftsman.sequencer.touch import cancel_open_tasks, resolve_task


def _scenario(
    db,
    *,
    channels=("email", "linkedin_task", "email"),
    state="awaiting_human_touch",
    current_step=2,
    skip_on_expire=False,
    task_status="open",
    due_in_hours=24,
    with_task=True,
):
    """Campaign whose step 2 is a task channel; enrollment sitting on it."""
    company = Company(
        domain=f"tt-{uuid.uuid4().hex[:8]}.test",
        name="Acme Corp",
        research_brief={
            "what_they_do": "widgets",
            "industry": "manufacturing",
            "trigger_events": [{"claim": "raised a $4M round", "source_url": "u", "approx_date": "2026"}],
            "likely_pain_points": ["slow onboarding"],
        },
    )
    db.add(company)
    db.flush()
    campaign = Campaign(name="tt", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    for i, channel in enumerate(channels, start=1):
        db.add(
            SequenceStep(
                campaign_id=campaign.id,
                step_order=i,
                wait_days=1,
                channel=channel,
                skip_on_expire=skip_on_expire if channel != "email" else False,
            )
        )
    lead = Lead(
        email=f"{uuid.uuid4().hex[:8]}@tt.test",
        company_id=company.id,
        status="verified",
        first_name="Pat",
        linkedin_url="https://www.linkedin.com/in/pat",
        timezone="UTC",
    )
    db.add(lead)
    db.flush()
    now = datetime.now(timezone.utc)
    enr = Enrollment(
        lead_id=lead.id,
        campaign_id=campaign.id,
        state=state,
        current_step=current_step,
        next_action_at=(now + timedelta(hours=due_in_hours)) if skip_on_expire else None,
    )
    db.add(enr)
    db.flush()
    task = None
    if with_task:
        task = TouchTask(
            enrollment_id=enr.id,
            step_order=current_step,
            channel="linkedin_task",
            payload={"message": "Hi Pat, saw the $4M round. Worth a look?", "char_count": 44},
            status=task_status,
            due_at=now + timedelta(hours=due_in_hours),
        )
        db.add(task)
        db.flush()
    return enr, task, lead, campaign


# ---------------------------------------------------------------- resolution


def test_complete_advances_to_waiting_and_schedules(db):
    enr, task, _, _ = _scenario(db)
    new = resolve_task(db, task, "done", outcome="sent")
    assert new == "waiting"
    assert task.status == "done"
    assert task.outcome == "sent"
    assert task.resolved_at is not None
    assert enr.next_action_at is not None  # next step scheduled
    audit = db.scalars(select(AuditLog).where(AuditLog.enrollment_id == enr.id)).all()
    assert any(a.event == "task_done" for a in audit)


def test_skip_advances_with_distinct_audit_event(db):
    enr, task, _, _ = _scenario(db)
    assert resolve_task(db, task, "skipped") == "waiting"
    audit = db.scalars(select(AuditLog).where(AuditLog.enrollment_id == enr.id)).all()
    assert any(a.event == "task_skipped" for a in audit)


def test_resolved_task_cannot_resolve_again(db):
    _, task, _, _ = _scenario(db, task_status="done")
    try:
        resolve_task(db, task, "done")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------- expiry via tick


def test_tick_expires_due_task_when_skip_on_expire(db):
    enr, task, _, _ = _scenario(db, skip_on_expire=True, due_in_hours=-1)
    enr.next_action_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(enr)
    db.flush()
    handled = tick(db, lambda _eid: None, lambda _eid: None)
    assert handled == 1

    assert task.status == "expired"
    assert enr.state == "waiting"


def test_holding_task_is_never_due(db):
    """⛔ Gate M3 default: skip_on_expire=false ⇒ next_action_at NULL ⇒ the tick
    never touches the enrollment; the task simply shows as overdue in the UI."""
    enr, task, _, _ = _scenario(db, skip_on_expire=False, due_in_hours=-48)
    handled = tick(db, lambda _eid: None, lambda _eid: None)
    assert handled == 0

    assert task.status == "open"
    assert enr.state == "awaiting_human_touch"


def test_tick_routes_ready_task_step_to_enqueue_task(db):
    enr, _, _, _ = _scenario(db, state="ready", with_task=False)
    enr.next_action_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(enr)
    db.flush()
    sends, tasks_q = [], []
    tick(db, lambda _eid: None, sends.append, tasks_q.append)
    assert tasks_q == [str(enr.id)]
    assert sends == []


def test_tick_routes_ready_email_step_to_enqueue_send(db):
    enr, _, _, _ = _scenario(db, state="ready", current_step=1, with_task=False)
    enr.next_action_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(enr)
    db.flush()
    sends, tasks_q = [], []
    tick(db, lambda _eid: None, sends.append, tasks_q.append)
    assert sends == [str(enr.id)]
    assert tasks_q == []


def test_tick_without_enqueue_task_leaves_task_step_untouched(db):
    """Pre-M3 call sites (enqueue_task omitted) must never mis-route a task step to
    the email sender."""
    enr, _, _, _ = _scenario(db, state="ready", with_task=False)
    enr.next_action_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(enr)
    db.flush()
    sends = []
    tick(db, lambda _eid: None, sends.append)
    assert sends == []
    assert enr.state == "ready"


# ---------------------------------------------------------------- cancellation


def test_reply_while_task_open_cancels_it(db):
    from craftsman.core.models import Message
    from craftsman.core.schemas import ReplyClassification
    from craftsman.inbox.pipeline import apply_classification

    enr, task, lead, _ = _scenario(db)
    outbound = Message(
        enrollment_id=enr.id, direction="outbound", step_order=1,
        subject="s", body="b", sent_at=datetime.now(timezone.utc),
    )
    db.add(outbound)
    db.flush()
    apply_classification(
        db, enr, outbound,
        ReplyClassification(label="interested", confidence=0.99),
    )
    assert enr.state == "replied_interested"

    assert task.status == "cancelled"
    assert task.outcome == "reply:interested"


def test_cancel_open_tasks_is_idempotent(db):
    enr, task, _, _ = _scenario(db)
    assert cancel_open_tasks(db, enr.id, reason="test") == 1
    assert cancel_open_tasks(db, enr.id, reason="test") == 0

    assert task.status == "cancelled"


# ---------------------------------------------------------------- API surface


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_task_api_lifecycle(client, db, make_key):
    enr, task, _, campaign = _scenario(db)
    token = make_key("read", "operate")

    listed = client.get("/tasks", headers=_auth(token))
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    item = body[0]
    assert item["channel"] == "linkedin_task"
    assert item["payload"]["message"].startswith("Hi Pat")
    assert item["campaign_name"] == "tt"
    assert item["linkedin_url"] == "https://www.linkedin.com/in/pat"
    assert "raised a $4M round" in item["brief_highlights"]

    done = client.post(f"/tasks/{task.id}/complete", json={}, headers=_auth(token))
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["outcome"] == "sent"  # channel default outcome
    db.refresh(enr)
    assert enr.state == "waiting"

    again = client.post(f"/tasks/{task.id}/complete", json={}, headers=_auth(token))
    assert again.status_code == 409  # no double-advance


def test_task_complete_rejects_bad_outcome(client, db, make_key):
    _, task, _, _ = _scenario(db)
    token = make_key("operate")
    r = client.post(
        f"/tasks/{task.id}/complete", json={"outcome": "connected"}, headers=_auth(token)
    )
    assert r.status_code == 422  # 'connected' is a call outcome, not linkedin


def test_task_list_requires_read_scope(client, db, make_key):
    _scenario(db)
    assert client.get("/tasks").status_code == 401
    token = make_key("read")
    assert client.get("/tasks", headers=_auth(token)).status_code == 200


def test_task_complete_requires_operate_scope(client, db, make_key):
    _, task, _, _ = _scenario(db)
    token = make_key("read")
    r = client.post(f"/tasks/{task.id}/complete", json={}, headers=_auth(token))
    assert r.status_code == 403


def test_suppressed_lead_task_is_cancelled_not_shown(client, db, make_key):
    from craftsman.compliance.suppression import suppress

    enr, task, lead, _ = _scenario(db)
    suppress(db, lead.email, reason="unsubscribe")
    token = make_key("read", "operate")
    listed = client.get("/tasks", headers=_auth(token))
    assert listed.json() == []

    assert task.status == "cancelled"
    assert task.outcome == "suppressed"


def test_overdue_flag(client, db, make_key):
    _scenario(db, due_in_hours=-4)
    token = make_key("read")
    listed = client.get("/tasks", headers=_auth(token)).json()
    assert listed[0]["overdue"] is True


# ---------------------------------------------------------------- erasure + timeline


def test_erase_lead_deletes_touch_tasks(db):
    from craftsman.compliance.suppression import erase_lead

    enr, task, lead, _ = _scenario(db)
    erase_lead(db, lead)
    assert db.get(TouchTask, task.id) is None
    assert db.get(Enrollment, enr.id) is None


def test_timeline_unifies_sends_replies_and_tasks(client, db, make_key):
    from craftsman.core.models import Message

    enr, task, lead, _ = _scenario(db, task_status="done")
    task.outcome = "sent"
    task.resolved_at = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    db.add_all([
        Message(enrollment_id=enr.id, direction="outbound", step_order=1,
                subject="hello", body="b", sent_at=now - timedelta(days=2)),
        Message(enrollment_id=enr.id, direction="inbound", subject="Re: hello",
                body="tell me more", classification="interested"),
    ])
    db.flush()
    token = make_key("read")
    r = client.get(f"/leads/{lead.id}/timeline", headers=_auth(token))
    assert r.status_code == 200
    kinds = [item["kind"] for item in r.json()]
    assert set(kinds) == {"email_sent", "reply", "task"}
    task_item = next(i for i in r.json() if i["kind"] == "task")
    assert task_item["channel"] == "linkedin_task"
    assert task_item["status"] == "done"
    assert task_item["campaign_name"] == "tt"
    # newest first
    ats = [item["at"] for item in r.json()]
    assert ats == sorted(ats, reverse=True)
