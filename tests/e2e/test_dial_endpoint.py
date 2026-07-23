"""M3.3: POST /tasks/{id}/dial — keyless-off, suppression-gated, never resolves the task."""

import uuid
from datetime import datetime, timedelta, timezone

from craftsman.core.models import Campaign, Company, Enrollment, Lead, SequenceStep, TouchTask


def _call_task(db, *, phone="+14155550123"):
    company = Company(domain=f"dial-{uuid.uuid4().hex[:8]}.test")
    db.add(company)
    db.flush()
    campaign = Campaign(name="dial", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    db.add(SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=1, channel="call_task"))
    lead = Lead(
        email=f"{uuid.uuid4().hex[:8]}@dial.test", company_id=company.id,
        status="verified", phone=phone, timezone="UTC",
    )
    db.add(lead)
    db.flush()
    enr = Enrollment(
        lead_id=lead.id, campaign_id=campaign.id, state="awaiting_human_touch", current_step=1
    )
    db.add(enr)
    db.flush()
    task = TouchTask(
        enrollment_id=enr.id, step_order=1, channel="call_task",
        payload={"brief": {"opener": "o", "pain_hypotheses": ["p"], "objection_notes": "n"}},
        status="open", due_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(task)
    db.flush()
    return task, lead


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_dial_400_when_unconfigured(client, db, make_key):
    task, _ = _call_task(db)
    token = make_key("operate")
    r = client.post(f"/tasks/{task.id}/dial", headers=_auth(token))
    assert r.status_code == 400
    assert "TWILIO" in r.json()["detail"]
    assert task.status == "open"  # dialing config trouble never resolves the task


def test_dial_calls_configured_dialer(client, db, make_key, monkeypatch):
    class FakeDialer:
        operator_number = "+15550002222"

        async def dial(self, phone):
            assert phone == "+14155550123"
            return "CA_fake"

    monkeypatch.setattr(
        "craftsman.sender.dialer.build_dialer", lambda settings: FakeDialer()
    )
    task, _ = _call_task(db)
    token = make_key("operate")
    r = client.post(f"/tasks/{task.id}/dial", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"call_sid": "CA_fake", "to_operator": "+15550002222"}
    assert task.status == "open"  # the human still records the outcome via /complete


def test_dial_suppressed_lead_cancels_and_409s(client, db, make_key, monkeypatch):
    from craftsman.compliance.suppression import suppress

    class FakeDialer:  # would succeed if reached — it must not be
        operator_number = "+15550002222"

        async def dial(self, phone):  # pragma: no cover
            raise AssertionError("dialed a suppressed lead")

    monkeypatch.setattr(
        "craftsman.sender.dialer.build_dialer", lambda settings: FakeDialer()
    )
    task, lead = _call_task(db)
    suppress(db, lead.email, reason="unsubscribe")
    token = make_key("operate")
    r = client.post(f"/tasks/{task.id}/dial", headers=_auth(token))
    assert r.status_code == 409
    assert task.status == "cancelled"


def test_dial_rejects_non_call_tasks(client, db, make_key):
    task, _ = _call_task(db)
    task.channel = "linkedin_task"
    db.add(task)
    db.flush()
    token = make_key("operate")
    assert client.post(f"/tasks/{task.id}/dial", headers=_auth(token)).status_code == 422


def test_dial_requires_operate_scope(client, db, make_key):
    task, _ = _call_task(db)
    token = make_key("read")
    assert client.post(f"/tasks/{task.id}/dial", headers=_auth(token)).status_code == 403
