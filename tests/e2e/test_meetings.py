"""M4.3 e2e: Cal.com webhook → meeting row → meeting_booked, against real Postgres."""

import hashlib
import hmac
import json
import uuid

from sqlalchemy import select

from craftsman.core.models import Meeting, TouchTask
from craftsman.meetings.providers import CalComProvider

from tests.e2e.test_reply_drafts import _auth, _scenario

SECRET = "whsec_e2e"


def _sign(raw: bytes) -> str:
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _post_webhook(client, payload, sig=None):
    raw = json.dumps(payload).encode()
    return client.post(
        "/meetings/webhooks/calcom",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Cal-Signature-256": sig if sig is not None else _sign(raw),
        },
    )


def _booking(lead_email, uid=None, trigger="BOOKING_CREATED"):
    return {
        "triggerEvent": trigger,
        "payload": {
            "uid": uid or f"bk_{uuid.uuid4().hex[:8]}",
            "startTime": "2026-08-01T15:00:00Z",
            "attendees": [{"email": lead_email}],
        },
    }


def _configured(monkeypatch):
    monkeypatch.setattr(
        "craftsman.api.routers.meetings.build_provider",
        lambda settings: CalComProvider(SECRET),
    )


def test_webhook_503_when_unconfigured(client):
    r = client.post("/meetings/webhooks/calcom", json={})
    assert r.status_code == 503


def test_webhook_rejects_bad_signature(client, db, monkeypatch):
    _configured(monkeypatch)
    r = _post_webhook(client, _booking("x@y.test"), sig="0" * 64)
    assert r.status_code == 401
    assert db.scalar(select(Meeting)) is None


def test_booking_books_the_enrollment(client, db, monkeypatch):
    _configured(monkeypatch)
    enr, lead, campaign, *_ = _scenario(db)  # replied_interested
    r = _post_webhook(client, _booking(lead.email, uid="bk_book1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handled"] is True and body["matched_enrollment"] is True

    meeting = db.scalar(select(Meeting).where(Meeting.provider_event_id == "bk_book1"))
    assert meeting.status == "booked" and meeting.booked_at is not None
    db.refresh(enr)
    assert enr.state == "meeting_booked"


def test_duplicate_delivery_is_idempotent(client, db, monkeypatch):
    _configured(monkeypatch)
    enr, lead, *_ = _scenario(db)
    _post_webhook(client, _booking(lead.email, uid="bk_dup"))
    booked_at_1 = db.scalar(select(Meeting.booked_at).where(Meeting.provider_event_id == "bk_dup"))
    r = _post_webhook(client, _booking(lead.email, uid="bk_dup"))
    assert r.status_code == 200
    rows = db.scalars(select(Meeting).where(Meeting.provider_event_id == "bk_dup")).all()
    assert len(rows) == 1
    assert rows[0].booked_at == booked_at_1  # first booking timestamp stands


def test_cancellation_updates_row_not_state(client, db, monkeypatch):
    _configured(monkeypatch)
    enr, lead, *_ = _scenario(db)
    _post_webhook(client, _booking(lead.email, uid="bk_cx"))
    db.refresh(enr)
    assert enr.state == "meeting_booked"
    r = _post_webhook(client, _booking(lead.email, uid="bk_cx", trigger="BOOKING_CANCELLED"))
    assert r.status_code == 200
    meeting = db.scalar(select(Meeting).where(Meeting.provider_event_id == "bk_cx"))
    assert meeting.status == "cancelled"
    db.refresh(enr)
    assert enr.state == "meeting_booked"  # the funnel win is recorded; row shows the cancel


def test_booking_from_awaiting_touch_cancels_open_task(client, db, monkeypatch):
    _configured(monkeypatch)
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(
        db, state="awaiting_human_touch"
    )
    task = TouchTask(
        enrollment_id=enr.id, step_order=1, channel="linkedin_task",
        payload={"message": "hi"}, status="open",
        due_at=outbound.sent_at,
    )
    db.add(task)
    db.flush()
    _post_webhook(client, _booking(lead.email))
    db.refresh(enr)
    db.refresh(task)
    assert enr.state == "meeting_booked"
    assert task.status == "cancelled" and task.outcome == "meeting_booked"


def test_unknown_attendee_is_recorded_unmatched(client, db, monkeypatch):
    _configured(monkeypatch)
    r = _post_webhook(client, _booking("nobody@unknown.test", uid="bk_ghost"))
    assert r.status_code == 200
    assert r.json() == {
        "handled": True,
        "meeting_id": r.json()["meeting_id"],
        "status": "booked",
        "matched_enrollment": False,
    }


def test_unknown_trigger_acknowledged(client, db, monkeypatch):
    _configured(monkeypatch)
    r = _post_webhook(client, {"triggerEvent": "FORM_SUBMITTED", "payload": {"uid": "x"}})
    assert r.status_code == 200 and r.json() == {"handled": False}


def test_meetings_list_and_funnel(client, db, make_key, monkeypatch):
    _configured(monkeypatch)
    enr, lead, campaign, *_ = _scenario(db)
    _post_webhook(client, _booking(lead.email))
    token = make_key("read")
    listed = client.get("/meetings", headers=_auth(token))
    assert listed.status_code == 200
    assert listed.json()[0]["lead_email"] == lead.email
    assert listed.json()[0]["campaign_name"] == "rd"
    overview = client.get("/analytics/overview", headers=_auth(token)).json()
    assert overview["booked"] == 1
    assert overview["funnel"]["booked"] == 1
    # anonymous list is still 401 (only the webhook is on the allowlist)
    assert client.get("/meetings").status_code == 401


def test_erase_lead_deletes_meetings(client, db, monkeypatch):
    from craftsman.compliance.suppression import erase_lead

    _configured(monkeypatch)
    enr, lead, *_ = _scenario(db)
    _post_webhook(client, _booking(lead.email))
    erase_lead(db, lead)
    db.flush()
    assert db.scalar(select(Meeting)) is None


def test_scheduling_url_lands_in_draft(db, monkeypatch):
    """Campaign.scheduling_url → static scheduling_line in the interested draft."""
    from contextlib import contextmanager

    from craftsman.core.models import ReplyDraft
    from craftsman.llm.mock_impl import MockLLM
    from craftsman.workers import tasks as task_mod

    from tests.e2e.test_reply_drafts import GOOD_FILL

    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    campaign.scheduling_url = "https://cal.test/sam/15min"
    db.add(campaign)
    db.flush()

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    llm = MockLLM()
    llm.enqueue(GOOD_FILL)
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "pending"
    assert "https://cal.test/sam/15min" in draft.body
