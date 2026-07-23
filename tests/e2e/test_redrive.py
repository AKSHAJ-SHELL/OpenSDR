"""Re-drive: unstick error enrollments and unsent claims (M0.6b Phase 4)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from craftsman.core.models import (
    AuditLog,
    Campaign,
    Company,
    Enrollment,
    Lead,
    Message,
    ReviewQueueItem,
    SequenceStep,
)
from craftsman.sequencer.redrive import redrive_enrollment, redrive_unsent_claims


def _scenario(db, *, state="error", current_step=1, with_brief=True, steps=1, cap_used=0):
    company = Company(
        domain=f"rd-{uuid.uuid4().hex[:8]}.test",
        research_brief={"what_they_do": "x", "industry": "y"} if with_brief else None,
    )
    db.add(company)
    db.flush()
    campaign = Campaign(name="rd", icp_description="x", value_prop="y", sent_today=cap_used)
    db.add(campaign)
    db.flush()
    for i in range(1, steps + 1):
        db.add(SequenceStep(campaign_id=campaign.id, step_order=i, wait_days=0))
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@rd.test", company_id=company.id, status="verified")
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state=state, current_step=current_step)
    db.add(enr)
    db.flush()
    return enr, campaign


# ---------------------------------------------------------------- service: enrollment


def test_retry_with_brief_goes_ready_and_keeps_step(db):
    enr, _ = _scenario(db, current_step=2, with_brief=True)
    new = redrive_enrollment(db, enr, "retry")
    assert new == "ready"
    assert enr.current_step == 2
    assert enr.next_action_at is not None


def test_retry_without_brief_goes_queued_from_step_zero(db):
    enr, _ = _scenario(db, current_step=0, with_brief=False)
    new = redrive_enrollment(db, enr, "retry")
    assert new == "queued"
    assert enr.current_step == 0


def test_skip_advances_step(db):
    enr, _ = _scenario(db, current_step=1, steps=3)
    new = redrive_enrollment(db, enr, "skip")
    assert new == "ready"
    assert enr.current_step == 2


def test_skip_past_last_step_finishes(db):
    enr, _ = _scenario(db, current_step=3, steps=3)
    new = redrive_enrollment(db, enr, "skip")
    assert new == "finished_no_reply"
    assert enr.next_action_at is None


def test_kill_stays_terminal(db):
    enr, _ = _scenario(db, state="error", current_step=1)
    new = redrive_enrollment(db, enr, "kill")
    assert new == "error"
    assert enr.next_action_at is None


def test_redrive_writes_audit(db):
    enr, _ = _scenario(db)
    redrive_enrollment(db, enr, "retry")
    db.flush()
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.enrollment_id == enr.id, AuditLog.event == "redrive_retry"
        )
    )
    assert audit is not None


def test_unknown_action_raises(db):
    enr, _ = _scenario(db)
    with pytest.raises(ValueError):
        redrive_enrollment(db, enr, "explode")


# ---------------------------------------------------------------- endpoint


def test_review_action_retries_and_resolves(client, make_key, db):
    enr, _ = _scenario(db, state="error", current_step=1, with_brief=True)
    item = ReviewQueueItem(kind="copywriter", enrollment_id=enr.id, payload={"errors": ["x"]})
    db.add(item)
    db.flush()

    h = {"Authorization": f"Bearer {make_key('operate')}"}
    resp = client.post(f"/inbox/review/{item.id}/action", json={"action": "retry"}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["new_state"] == "ready"

    # the endpoint shares this test's session (get_db override), so the changes are visible
    # in-session without a refresh (which would revert the not-yet-flushed writes)
    assert item.resolved is True
    assert enr.state == "ready"


def test_review_action_rejects_bad_action(client, make_key, db):
    enr, _ = _scenario(db)
    item = ReviewQueueItem(kind="copywriter", enrollment_id=enr.id, payload={})
    db.add(item)
    db.flush()
    h = {"Authorization": f"Bearer {make_key('operate')}"}
    assert client.post(f"/inbox/review/{item.id}/action", json={"action": "nope"}, headers=h).status_code == 400


def test_review_action_unknown_item_404(client, make_key):
    h = {"Authorization": f"Bearer {make_key('operate')}"}
    r = client.post(f"/inbox/review/{uuid.uuid4()}/action", json={"action": "retry"}, headers=h)
    assert r.status_code == 404


def test_review_action_requires_operate(client, make_key, db):
    enr, _ = _scenario(db)
    item = ReviewQueueItem(kind="copywriter", enrollment_id=enr.id, payload={})
    db.add(item)
    db.flush()
    h = {"Authorization": f"Bearer {make_key('read')}"}  # read is insufficient
    assert client.post(f"/inbox/review/{item.id}/action", json={"action": "retry"}, headers=h).status_code == 403


# ---------------------------------------------------------------- unsent-claim sweep


def _claim(db, enr, *, age_minutes, sent=False, step=1):
    now = datetime.now(timezone.utc)
    return Message(
        enrollment_id=enr.id, direction="outbound", step_order=step,
        subject="s", body="b", bandit_outcome="pending",
        created_at=now - timedelta(minutes=age_minutes),
        sent_at=now if sent else None,
    )


def test_sweep_redrives_old_stuck_claim(db):
    enr, campaign = _scenario(db, state="ready", current_step=1, cap_used=1)
    enr.next_action_at = None  # tick nulls this on dispatch
    claim = _claim(db, enr, age_minutes=30)  # older than the 15-min cutoff
    db.add(claim)
    db.flush()
    claim_id = claim.id

    n = redrive_unsent_claims(db, after_minutes=15)
    db.flush()

    assert n == 1
    assert db.get(Message, claim_id) is None  # stuck claim removed
    db.refresh(enr)
    assert enr.next_action_at is not None  # re-armed for the next tick
    db.refresh(campaign)
    assert campaign.sent_today == 0  # slot released


def test_sweep_leaves_fresh_claim_alone(db):
    enr, _ = _scenario(db, state="ready", current_step=1)
    db.add(_claim(db, enr, age_minutes=1))  # within the window — could be in-flight
    db.flush()
    assert redrive_unsent_claims(db, after_minutes=15) == 0


def test_sweep_leaves_sent_message_alone(db):
    enr, _ = _scenario(db, state="waiting", current_step=1)
    db.add(_claim(db, enr, age_minutes=60, sent=True))  # already delivered
    db.flush()
    assert redrive_unsent_claims(db, after_minutes=15) == 0
