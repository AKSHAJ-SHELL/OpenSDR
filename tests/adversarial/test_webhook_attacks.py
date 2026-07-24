"""Outbound webhook attacks (M5.4), predict-then-run per TESTING.md §3.

Properties under attack:
- **SSRF, registration time:** the endpoint URL goes through the M0.5 guard —
  http schemes, cloud-metadata addresses, loopback, RFC1918, CGNAT, and IPv6
  loopback must all 422 and leave no endpoint row.
- **SSRF, delivery time:** DNS can change AFTER registration (rebinding). The
  delivery task re-runs the guard immediately before every POST — a URL whose
  resolution turned private is refused without any HTTP request.
- **Cross-tenant:** org B can neither see, edit, test, delete, nor read the
  delivery history of org A's webhooks (item endpoints 404, never 403; the
  list returns zero foreign rows). Emission in org A creates nothing for B.
- **Forged event types:** rejected at the registry (emit) and the API edge
  (mask validation) — a webhook can never announce an event that doesn't exist.
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from craftsman.api.auth import generate_token, hash_token, key_prefix
from craftsman.core.models import ApiKey, Org, WebhookDelivery, WebhookEndpoint
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.research.fetch import UnsafeURL
from craftsman.webhooks import delivery as wd
from craftsman.webhooks import events
from craftsman.workers import tasks

PUBLIC_URL = "https://93.184.216.34/hook"  # public IP literal — guard passes, no DNS


def _admin(make_key):
    return {"Authorization": f"Bearer {make_key('admin')}"}


# ---------------------------------------------------------------- SSRF: registration


# Predicted: every private/metadata/scheme-confused URL is a 422 at registration
# and creates NO endpoint row — the guard runs before the insert.
@pytest.mark.parametrize(
    "evil_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadata, and not even https
        "https://169.254.169.254/latest/meta-data/",  # metadata over https
        "https://127.0.0.1/hook",  # loopback
        "https://10.0.0.5/hook",  # RFC1918
        "https://192.168.1.1/hook",  # RFC1918
        "https://100.64.0.1/hook",  # CGNAT
        "https://[::1]/hook",  # IPv6 loopback
        "https://93.184.216.34:6379/hook",  # public IP, but a non-443 port (redis)
        "ftp://93.184.216.34/hook",  # scheme confusion
    ],
)
def test_registration_refuses_unsafe_urls(client, db, make_key, evil_url):
    r = client.post(
        "/webhooks",
        json={"url": evil_url, "event_mask": ["reply.received"]},
        headers=_admin(make_key),
    )
    assert r.status_code == 422
    assert db.scalar(select(WebhookEndpoint.id).limit(1)) is None


# Predicted: PATCHing a good endpoint's URL to a private target is refused the
# same way — the stored URL stays what it was.
def test_patch_cannot_smuggle_a_private_url(client, db, make_key):
    created = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received"]},
        headers=_admin(make_key),
    ).json()
    r = client.patch(
        f"/webhooks/{created['id']}",
        json={"url": "https://169.254.169.254/hook"},
        headers=_admin(make_key),
    )
    assert r.status_code == 422
    assert db.get(WebhookEndpoint, uuid.UUID(created["id"])).url == PUBLIC_URL


# ---------------------------------------------------------------- SSRF: delivery time


# Predicted: a URL that was safe at registration but whose DNS now resolves
# private (rebinding) is refused AT DELIVERY TIME — the HTTP seam is never
# called, the attempt is recorded, and the delivery retries (status pending)
# rather than silently probing the internal network.
def test_delivery_revalidates_url_after_dns_change(client, db, make_key, monkeypatch):
    monkeypatch.setattr(events, "_enqueue_delivery", lambda _id: None)
    created = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received"]},
        headers=_admin(make_key),
    ).json()
    delivery = WebhookDelivery(
        endpoint_id=uuid.UUID(created["id"]), event_type="reply.received", payload={}
    )
    db.add(delivery)
    db.flush()

    # the simulated rebind: between registration and delivery, resolution went private
    def now_private(url):
        raise UnsafeURL(f"host resolves to non-public address 169.254.169.254 ({url})")

    posts: list = []
    monkeypatch.setattr(wd, "validate_url", now_private)
    monkeypatch.setattr(wd, "post_delivery", lambda *a: posts.append(a))

    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr(tasks, "session_scope", scope)
    with pytest.raises(Exception):  # Retry — the failure is retryable, not swallowed
        tasks.deliver_webhook.run(str(delivery.id))

    assert posts == []  # the internal network was never touched
    db.refresh(delivery)
    assert delivery.attempts == 1
    assert delivery.status == "pending"
    assert "non-public" in delivery.last_error


# ---------------------------------------------------------------- cross-tenant


@pytest.fixture()
def foreign_org_key(db):
    """A fresh org with its own admin key (M5.1d two_orgs pattern).
    Returns (token, org_id)."""
    with unscoped_context():
        org_b = Org(name="Webhook B", slug=f"wh-b-{uuid.uuid4().hex[:6]}")
        db.add(org_b)
        db.flush()
    token = generate_token()
    with org_context(org_b.id):
        db.add(ApiKey(
            name="b-admin", key_prefix=key_prefix(token),
            key_hash=hash_token(token), scopes=["admin"],
        ))
        db.flush()
    return token, org_b.id


# Predicted: org A's endpoint is invisible and untouchable from org B — the
# list is empty, and every item route 404s (never 403: a 403 confirms
# existence, which is itself a leak).
def test_org_b_cannot_see_or_touch_org_a_webhooks(
    client, db, make_key, foreign_org_key
):
    created = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received"]},
        headers=_admin(make_key),
    ).json()
    b_token, _ = foreign_org_key
    db.expunge_all()  # production parity: fresh session per request (two_orgs pattern)
    h = {"Authorization": f"Bearer {b_token}"}

    assert client.get("/webhooks", headers=h).json() == []
    assert client.get(f"/webhooks/{created['id']}/deliveries", headers=h).status_code == 404
    assert (
        client.patch(
            f"/webhooks/{created['id']}", json={"active": False}, headers=h
        ).status_code
        == 404
    )
    assert client.post(f"/webhooks/{created['id']}/test", headers=h).status_code == 404
    assert client.delete(f"/webhooks/{created['id']}", headers=h).status_code == 404
    # and nothing about org A's endpoint changed
    survivor = db.get(WebhookEndpoint, uuid.UUID(created["id"]))
    assert survivor is not None and survivor.active is True


# Predicted: an event emitted inside org A creates deliveries ONLY for org A's
# endpoints — org B's identically-subscribed endpoint gets nothing.
def test_emission_never_creates_foreign_deliveries(db, monkeypatch, foreign_org_key):
    monkeypatch.setattr(events, "_enqueue_delivery", lambda _id: None)
    _, org_b_id = foreign_org_key
    with org_context(org_b_id):
        b_endpoint = WebhookEndpoint(
            url=PUBLIC_URL, secret_enc="enc", event_mask=["reply.received"]
        )
        db.add(b_endpoint)
        db.flush()

    # default-org context (the autouse fixture): emit the event org A-side
    a_endpoint = WebhookEndpoint(
        url=PUBLIC_URL, secret_enc="enc", event_mask=["reply.received"]
    )
    db.add(a_endpoint)
    db.flush()
    assert events.emit_event(db, "reply.received", {"x": 1}) == 1

    with org_context(org_b_id):
        assert (
            db.scalar(
                select(WebhookDelivery).where(
                    WebhookDelivery.endpoint_id == b_endpoint.id
                )
            )
            is None
        )


# ---------------------------------------------------------------- forged event types


# Predicted: emit_event refuses a type outside the registry before touching the
# database, and the API refuses it in a mask — there is no path by which a
# forged event name enters the system.
def test_forged_event_type_rejected_everywhere(client, db, make_key):
    with pytest.raises(ValueError, match="unknown webhook event type"):
        events.emit_event(db, "org.deleted; DROP TABLE leads;--", {})
    with pytest.raises(ValueError):
        events.emit_event(db, "ping", {})  # the synthetic test event is not emittable

    r = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received", "org.secrets_dump"]},
        headers=_admin(make_key),
    )
    assert r.status_code == 422
    assert db.scalar(select(WebhookEndpoint.id).limit(1)) is None

    created = client.post(
        "/webhooks",
        json={"url": PUBLIC_URL, "event_mask": ["reply.received"]},
        headers=_admin(make_key),
    ).json()
    r = client.patch(
        f"/webhooks/{created['id']}",
        json={"event_mask": ["totally.fake"]},
        headers=_admin(make_key),
    )
    assert r.status_code == 422
    assert db.get(WebhookEndpoint, uuid.UUID(created["id"])).event_mask == ["reply.received"]
