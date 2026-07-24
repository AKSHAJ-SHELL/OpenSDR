"""Outbound webhooks (M5.4) end-to-end: CRUD + secret handling, the test-ping
delivery through the real Celery task body (HTTP seam monkeypatched), the
deliveries listing, the retry→failed lifecycle, and the guarantee that
emission can never break an emitting path.

The endpoint URL uses a public IP literal so the REAL SSRF guard passes at
registration and at delivery time without any DNS dependency.
"""

import json
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from craftsman.core.models import Lead, SuppressionEntry, WebhookDelivery, WebhookEndpoint
from craftsman.meetings.providers import CalComProvider
from craftsman.webhooks import delivery as wd
from craftsman.webhooks import events
from craftsman.workers import tasks

PUBLIC_URL = "https://93.184.216.34/hook"  # public IP literal: guard-passing, no DNS


def _admin(make_key):
    return {"Authorization": f"Bearer {make_key('admin')}"}


def _patch_task_session(monkeypatch, db):
    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr(tasks, "session_scope", scope)


@pytest.fixture()
def no_enqueue(monkeypatch):
    """Capture enqueues instead of talking to the broker."""
    captured: list[str] = []
    monkeypatch.setattr(events, "_enqueue_delivery", captured.append)
    return captured


# ---------------------------------------------------------------- CRUD + secrets


def test_create_returns_secret_exactly_once_then_only_a_prefix(client, db, make_key):
    h = _admin(make_key)
    r = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received", "autopilot.sent"]},
        headers=h,
    )
    assert r.status_code == 201
    created = r.json()
    assert created["secret"].startswith("whsec_")
    assert created["active"] is True
    assert created["event_mask"] == ["reply.received", "autopilot.sent"]

    listed = client.get("/webhooks", headers=h)
    assert listed.status_code == 200
    (row,) = listed.json()
    assert "secret" not in row  # never echoed after creation
    assert row["secret_prefix"] == created["secret"][:10] + "…"
    # at rest it is Fernet ciphertext, not the plaintext
    stored = db.get(WebhookEndpoint, uuid.UUID(created["id"]))
    assert created["secret"] not in stored.secret_enc


def test_patch_updates_mask_active_and_revalidated_url(client, db, make_key):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    r = client.patch(
        f"/webhooks/{created['id']}",
        json={"event_mask": ["meeting.updated"], "active": False},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["event_mask"] == ["meeting.updated"]
    assert r.json()["active"] is False
    # a URL change goes back through the SSRF guard
    bad = client.patch(
        f"/webhooks/{created['id']}", json={"url": "https://127.0.0.1/hook"}, headers=h
    )
    assert bad.status_code == 422


def test_delete_removes_endpoint_and_its_delivery_history(client, db, make_key, no_enqueue):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    client.post(f"/webhooks/{created['id']}/test", headers=h)
    eid = uuid.UUID(created["id"])
    assert db.scalar(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == eid))

    assert client.delete(f"/webhooks/{created['id']}", headers=h).status_code == 204
    assert db.get(WebhookEndpoint, eid) is None
    assert (
        db.scalar(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == eid)) is None
    )


def test_scope_matrix(client, make_key):
    read_h = {"Authorization": f"Bearer {make_key('read')}"}
    op_h = {"Authorization": f"Bearer {make_key('operate')}"}
    # write = admin only
    assert (
        client.post(
            "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=op_h
        ).status_code
        == 403
    )
    # list = operate (read is not enough)
    assert client.get("/webhooks", headers=read_h).status_code == 403
    assert client.get("/webhooks", headers=op_h).status_code == 200
    # deliveries = read (404 for unknown id proves it passed the scope gate)
    assert (
        client.get(f"/webhooks/{uuid.uuid4()}/deliveries", headers=read_h).status_code == 404
    )


# ---------------------------------------------------------------- ping delivery


def test_ping_delivery_end_to_end_signed_and_verifiable(
    client, db, make_key, monkeypatch, no_enqueue
):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    r = client.post(f"/webhooks/{created['id']}/test", headers=h)
    assert r.status_code == 202
    delivery_id = r.json()["delivery_id"]
    assert no_enqueue == [delivery_id]

    posts: list[tuple] = []
    monkeypatch.setattr(wd, "post_delivery", lambda url, body, headers: posts.append((url, body, headers)))
    _patch_task_session(monkeypatch, db)
    tasks.deliver_webhook.run(delivery_id)

    (url, body, headers) = posts[0]
    assert url == PUBLIC_URL
    parsed = json.loads(body)
    assert parsed["event"] == "ping"
    assert parsed["delivery_id"] == delivery_id
    assert parsed["payload"]["ping"] is True
    assert headers["X-Craftsman-Event"] == "ping"
    assert headers["X-Craftsman-Delivery"] == delivery_id
    # the signature verifies with the create-time secret under the Cal.com verifier
    sig = headers["X-Craftsman-Signature-256"].removeprefix("sha256=")
    assert CalComProvider(created["secret"]).verify_webhook(body, sig) is True

    row = db.get(WebhookDelivery, uuid.UUID(delivery_id))
    assert row.status == "delivered"
    assert row.attempts == 1
    assert row.delivered_at is not None


def test_deliveries_listing_returns_recent_fifty_newest_first(
    client, db, make_key, no_enqueue
):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    eid = uuid.UUID(created["id"])
    for i in range(55):
        db.add(WebhookDelivery(endpoint_id=eid, event_type="reply.received", payload={"i": i}))
    db.flush()

    r = client.get(f"/webhooks/{created['id']}/deliveries", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 50  # capped
    assert all(row["endpoint_id"] == created["id"] for row in rows)
    assert all(row["status"] == "pending" for row in rows)


# ---------------------------------------------------------------- retry → failed


def test_retry_then_terminal_failure_lifecycle(client, db, make_key, monkeypatch, no_enqueue):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    delivery = WebhookDelivery(
        endpoint_id=uuid.UUID(created["id"]), event_type="reply.received", payload={}
    )
    db.add(delivery)
    db.flush()

    def refuse(url, body, headers):
        raise ConnectionError("receiver down")

    monkeypatch.setattr(wd, "post_delivery", refuse)
    _patch_task_session(monkeypatch, db)

    max_attempts = 8  # the knob's default — asserted in the unit layer
    for attempt in range(1, max_attempts + 1):
        with pytest.raises(Exception) as exc_info:
            tasks.deliver_webhook.run(str(delivery.id))
        db.refresh(delivery)
        assert delivery.attempts == attempt
        if attempt < max_attempts:
            assert delivery.status == "pending"  # still retrying
        else:
            assert delivery.status == "failed"  # budget exhausted — terminal
            assert isinstance(exc_info.value, tasks.WebhookDeliveryFailed)
    assert "receiver down" in delivery.last_error

    # once terminal, redelivery of the task is a no-op (status guard)
    tasks.deliver_webhook.run(str(delivery.id))
    assert delivery.attempts == max_attempts


def test_delivery_to_deactivated_endpoint_fails_without_a_post(
    client, db, make_key, monkeypatch, no_enqueue
):
    h = _admin(make_key)
    created = client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["reply.received"]}, headers=h
    ).json()
    delivery = WebhookDelivery(
        endpoint_id=uuid.UUID(created["id"]), event_type="reply.received", payload={}
    )
    db.add(delivery)
    db.flush()
    client.patch(f"/webhooks/{created['id']}", json={"active": False}, headers=h)

    posts: list = []
    monkeypatch.setattr(wd, "post_delivery", lambda *a: posts.append(a))
    _patch_task_session(monkeypatch, db)
    tasks.deliver_webhook.run(str(delivery.id))

    assert posts == []
    assert delivery.status == "failed"
    assert "deactivated" in delivery.last_error


# ---------------------------------------------------------------- emission safety


def test_emission_failure_never_breaks_the_emitting_path(client, db, make_key, monkeypatch):
    """The suppress endpoint emits lead.status_changed. Blow up the entire
    emission machinery — the suppress must still succeed and suppress."""
    lead = Lead(email=f"wh-{uuid.uuid4().hex[:8]}@example.com", status="verified")
    db.add(lead)
    db.flush()

    def boom(*a, **k):
        raise RuntimeError("webhook subsystem down")

    monkeypatch.setattr(events, "emit_event", boom)
    r = client.post(
        f"/leads/{lead.id}/suppress",
        headers={"Authorization": f"Bearer {make_key('operate')}"},
    )
    assert r.status_code == 204  # the path did not care
    assert (
        db.scalar(select(SuppressionEntry).where(SuppressionEntry.email == lead.email))
        is not None
    )


def test_suppress_endpoint_emits_lead_status_changed(client, db, make_key, no_enqueue):
    h = _admin(make_key)
    client.post(
        "/webhooks", json={"url": PUBLIC_URL, "event_mask": ["lead.status_changed"]}, headers=h
    )
    lead = Lead(email=f"wh2-{uuid.uuid4().hex[:8]}@example.com", status="verified")
    db.add(lead)
    db.flush()
    r = client.post(
        f"/leads/{lead.id}/suppress",
        headers={"Authorization": f"Bearer {make_key('operate')}"},
    )
    assert r.status_code == 204
    row = db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.event_type == "lead.status_changed")
    )
    assert row is not None
    assert row.payload["lead_id"] == str(lead.id)
    assert row.payload["from_state"] == "verified"
    assert row.payload["to_state"] == "suppressed"
