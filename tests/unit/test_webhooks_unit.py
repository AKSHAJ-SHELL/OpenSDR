"""Outbound webhooks (M5.4) unit layer: registry, emission filtering,
signature scheme, backoff schedule. No network — the enqueue seam is patched."""

import json
import uuid

import pytest

from craftsman.core.models import WebhookDelivery, WebhookEndpoint
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.meetings.providers import CalComProvider
from craftsman.webhooks import delivery as wd
from craftsman.webhooks import events


# ---------------------------------------------------------------- registry


def test_registry_is_exactly_the_five_documented_events():
    assert set(events.EVENT_TYPES) == {
        "lead.status_changed",
        "reply.received",
        "meeting.updated",
        "autopilot.sent",
        "escalation.fired",
    }


def test_ping_is_not_a_subscribable_event():
    # the synthetic test event can never be emitted or subscribed to
    assert events.PING_EVENT not in events.EVENT_TYPES


def test_forged_event_type_is_refused_before_any_db_work():
    # validation precedes the endpoint lookup — db=None proves no query ran
    with pytest.raises(ValueError, match="unknown webhook event type"):
        events.emit_event(None, "lead.deleted", {"x": 1})
    with pytest.raises(ValueError):
        events.emit_event(None, events.PING_EVENT, {})  # ping can't be emitted either


# ---------------------------------------------------------------- emission filtering


def _endpoint(db, mask, active=True, org_id=None):
    ep = WebhookEndpoint(
        url="https://93.184.216.34/hook",
        secret_enc="enc",
        event_mask=mask,
        active=active,
        **({"org_id": org_id} if org_id else {}),
    )
    db.add(ep)
    db.flush()
    return ep


def test_emit_filters_by_mask_active_and_org(db, monkeypatch):
    from craftsman.core.models import Org

    enqueued: list[str] = []
    monkeypatch.setattr(events, "_enqueue_delivery", enqueued.append)

    subscribed = _endpoint(db, ["reply.received", "meeting.updated"])
    _endpoint(db, ["meeting.updated"])  # wrong mask — skipped
    _endpoint(db, ["reply.received"], active=False)  # inactive — skipped

    # an identically-subscribed endpoint in ANOTHER org must never receive
    with unscoped_context():
        other = Org(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        db.add(other)
        db.flush()
    with org_context(other.id):
        foreign = _endpoint(db, ["reply.received"], org_id=other.id)

    created = events.emit_event(db, "reply.received", {"k": "v"})
    assert created == 1
    rows = db.query(WebhookDelivery).all()  # org-scoped: default org only
    assert len(rows) == 1
    assert rows[0].endpoint_id == subscribed.id
    assert rows[0].status == "pending" and rows[0].attempts == 0
    assert rows[0].payload == {"k": "v"}
    assert enqueued == [str(rows[0].id)]

    with org_context(other.id):
        assert db.query(WebhookDelivery).filter_by(endpoint_id=foreign.id).count() == 0


def test_emit_with_no_endpoints_is_a_quiet_no_op(db, monkeypatch):
    monkeypatch.setattr(events, "_enqueue_delivery", lambda _id: None)
    assert events.emit_event(db, "autopilot.sent", {}) == 0


def test_safe_emit_swallows_every_failure(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("webhook infrastructure on fire")

    monkeypatch.setattr(events, "emit_event", boom)
    assert events.safe_emit(db, "reply.received", {}) == 0  # no raise — the contract


def test_enqueue_failure_does_not_lose_the_delivery_row(db, monkeypatch):
    def broker_down(_id):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(events, "_enqueue_delivery", lambda _id: broker_down(_id))
    ep = _endpoint(db, ["escalation.fired"])
    # safe_emit path: the enqueue explodes but the pending row survives as record
    assert events.safe_emit(db, "escalation.fired", {"rules": ["r"]}) == 0
    row = db.query(WebhookDelivery).filter_by(endpoint_id=ep.id).one()
    assert row.status == "pending"


# ---------------------------------------------------------------- signature


def test_signature_is_the_calcom_scheme_with_sha256_prefix():
    """Symmetry check: the value after 'sha256=' must satisfy the EXACT verifier
    we run against inbound Cal.com webhooks (meetings/providers.py)."""
    secret = "whsec_test_secret"
    body = json.dumps({"event": "reply.received", "payload": {"a": 1}}).encode()

    header = wd.sign_body(secret, body)
    assert header.startswith("sha256=")
    hex_part = header.removeprefix("sha256=")
    assert CalComProvider(secret).verify_webhook(body, hex_part) is True
    # tampered body must fail the same verifier
    assert CalComProvider(secret).verify_webhook(body + b" ", hex_part) is False
    # wrong secret must fail
    assert CalComProvider("other").verify_webhook(body, hex_part) is False


def test_build_body_is_deterministic_and_signable():
    class Row:
        id = uuid.UUID("00000000-0000-0000-0000-00000000abcd")
        event_type = "meeting.updated"
        created_at = None
        payload = {"b": 2, "a": 1}

    b1, b2 = wd.build_body(Row()), wd.build_body(Row())
    assert b1 == b2  # sorted keys, fixed separators — stable bytes to sign
    parsed = json.loads(b1)
    assert parsed["event"] == "meeting.updated"
    assert parsed["payload"] == {"a": 1, "b": 2}
    assert parsed["delivery_id"] == "00000000-0000-0000-0000-00000000abcd"


# ---------------------------------------------------------------- backoff


def test_backoff_schedule_doubles_from_30s_and_caps_at_one_hour():
    assert [wd.backoff_seconds(n) for n in range(1, 8)] == [
        30.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0
    ]
    assert wd.backoff_seconds(8) == 3600.0  # capped
    assert wd.backoff_seconds(20) == 3600.0  # stays capped


def test_default_max_attempts_knob_is_eight():
    from craftsman.core.config import Settings

    assert Settings(_env_file=None).webhook_max_attempts == 8
    assert Settings(_env_file=None).audit_retention_days == 0


def test_mask_schema_rejects_forged_event_types():
    from pydantic import ValidationError

    from craftsman.core.schemas import WebhookEndpointCreate

    with pytest.raises(ValidationError, match="unknown event type"):
        WebhookEndpointCreate(url="https://x.example/h", event_mask=["lead.deleted"])
    ok = WebhookEndpointCreate(
        url="https://x.example/h",
        event_mask=["reply.received", "reply.received", "autopilot.sent"],
    )
    assert ok.event_mask == ["reply.received", "autopilot.sent"]  # de-duped, order kept
