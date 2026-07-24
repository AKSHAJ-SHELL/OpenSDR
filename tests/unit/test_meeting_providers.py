"""M4.3: CalendarProvider protocol — Cal.com HMAC verification + payload parsing."""

import hashlib
import hmac
import json
from types import SimpleNamespace

from craftsman.meetings.providers import CalComProvider, build_provider
from craftsman.sequencer.machine import Event, next_state

SECRET = "whsec_test"


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payload(trigger="BOOKING_CREATED", uid="bk_123", **kw):
    body = {
        "uid": uid,
        "startTime": kw.get("start", "2026-08-01T15:00:00Z"),
        "attendees": kw.get("attendees", [{"email": "Dana@Acme.test", "name": "Dana"}]),
    }
    return {"triggerEvent": trigger, "payload": body}


# ---------------------------------------------------------------- verification


def test_valid_signature_accepts():
    p = CalComProvider(SECRET)
    raw = json.dumps(_payload()).encode()
    assert p.verify_webhook(raw, _sign(raw))


def test_bad_signature_rejects():
    p = CalComProvider(SECRET)
    raw = json.dumps(_payload()).encode()
    assert not p.verify_webhook(raw, _sign(raw, "other-secret"))
    assert not p.verify_webhook(raw, "deadbeef")
    assert not p.verify_webhook(raw, None)
    assert not p.verify_webhook(raw, "")


def test_signature_is_over_exact_bytes():
    p = CalComProvider(SECRET)
    raw = json.dumps(_payload()).encode()
    tampered = raw.replace(b"bk_123", b"bk_124")
    assert not p.verify_webhook(tampered, _sign(raw))


def test_keyless_off():
    assert build_provider(SimpleNamespace(calcom_webhook_secret="")) is None
    p = build_provider(SimpleNamespace(calcom_webhook_secret=SECRET))
    assert p is not None and p.name == "calcom"


# ---------------------------------------------------------------- parsing


def test_parse_booking_created():
    e = CalComProvider(SECRET).parse_webhook(_payload())
    assert e.status == "booked"
    assert e.provider_event_id == "bk_123"
    assert e.start_at is not None and e.start_at.year == 2026
    assert e.attendee_emails == ("dana@acme.test",)  # lowercased


def test_parse_status_mapping():
    p = CalComProvider(SECRET)
    for trigger, status in [
        ("BOOKING_REQUESTED", "proposed"),
        ("BOOKING_RESCHEDULED", "booked"),
        ("BOOKING_CANCELLED", "cancelled"),
        ("BOOKING_REJECTED", "cancelled"),
        ("MEETING_ENDED", "completed"),
        ("BOOKING_NO_SHOW_UPDATED", "no_show"),
    ]:
        assert p.parse_webhook(_payload(trigger=trigger)).status == status


def test_unknown_trigger_and_missing_uid_ignored():
    p = CalComProvider(SECRET)
    assert p.parse_webhook(_payload(trigger="FORM_SUBMITTED")) is None
    assert p.parse_webhook({"triggerEvent": "BOOKING_CREATED", "payload": {}}) is None


def test_bad_start_time_is_tolerated():
    e = CalComProvider(SECRET).parse_webhook(_payload(start="not-a-date"))
    assert e is not None and e.start_at is None


# ---------------------------------------------------------------- machine pairs


def test_meeting_booked_from_live_states():
    for state in ("waiting", "awaiting_human_touch", "ooo_rescheduled", "ready"):
        assert next_state(state, Event.MEETING_BOOKED) == "meeting_booked"


def test_meeting_booked_is_terminal():
    from craftsman.sequencer.machine import is_terminal

    assert is_terminal("meeting_booked")
