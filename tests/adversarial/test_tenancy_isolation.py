"""M5.1d — cross-tenant isolation suite (⛔ Gate M5 evidence).

Method per TESTING.md §3: prediction stated above each test, then run. The
property under attack: **no request authenticated as org B can read, write,
mutate, or infer the existence of anything belonging to org A** — enforced by
the session-layer guard (core/tenancy.py), not by any router.

Item endpoints must 404 (never 403 — a 403 confirms the id exists, which is
itself a leak). List endpoints must return zero foreign rows. Writes must land
in the caller's org regardless of forged payload hints.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from craftsman.api.auth import generate_token, hash_token, key_prefix
from craftsman.core.models import (
    ApiKey,
    Campaign,
    Company,
    Enrollment,
    Lead,
    Mailbox,
    Message,
    Org,
    SuppressionEntry,
)
from craftsman.core.tenancy import (
    DEFAULT_ORG_ID,
    TenancyError,
    no_org_context,
    org_context,
    unscoped_context,
)


@pytest.fixture()
def two_orgs(db):
    """Org A = the default org, populated; org B = a fresh org with its own
    admin key. Returns (a_rows, b_key_token, b_org_id)."""
    # org A data (created under the default context the db fixture provides)
    company = Company(domain=f"iso-{uuid.uuid4().hex[:8]}.example", name="Iso A")
    db.add(company)
    db.flush()
    lead = Lead(
        email=f"shared-{uuid.uuid4().hex[:6]}@example.com",
        company_id=company.id, status="verified", email_verified=True,
        first_name="Ada",
    )
    campaign = Campaign(name="iso-a", icp_description="x", value_prop="y")
    mailbox = Mailbox(email=f"a-{uuid.uuid4().hex[:6]}@example.com", daily_limit=40)
    db.add_all([lead, campaign, mailbox])
    db.flush()
    enrollment = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="replied_interested")
    db.add(enrollment)
    db.flush()
    inbound = Message(
        enrollment_id=enrollment.id, direction="inbound", subject="re: x",
        body="tell me more", classification="interested", classification_confidence=0.95,
        smtp_message_id=f"<iso-{uuid.uuid4().hex[:6]}@example.com>",
    )
    db.add(inbound)
    db.flush()

    with unscoped_context():
        org_b = Org(name="Org B", slug=f"iso-b-{uuid.uuid4().hex[:6]}")
        db.add(org_b)
        db.flush()
    token = generate_token()
    with org_context(org_b.id):
        db.add(ApiKey(
            name="b-admin", key_prefix=key_prefix(token),
            key_hash=hash_token(token), scopes=["admin"],
        ))
        db.flush()

    rows = {
        "company": company, "lead": lead, "campaign": campaign,
        "mailbox": mailbox, "enrollment": enrollment, "inbound": inbound,
    }
    # production parity: every API request gets a FRESH session, so a router's
    # db.get() always emits filtered SQL. The test client shares this fixture
    # session, whose identity map would short-circuit that — clear it. (When an
    # id DOES slip through a warm identity map, the flush-time guard still
    # refuses the write — test_cross_org_writes_and_moves_refused proves that
    # second layer.)
    db.expunge_all()
    return rows, token, org_b.id


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# Predicted: every list endpoint queried with org B's admin key returns ZERO
# rows even though org A's data exists — the session guard filters centrally,
# so no router can forget.
def test_list_endpoints_return_no_foreign_rows(client, two_orgs):
    _, b_key, _ = two_orgs
    h = _auth(b_key)
    for path in (
        "/leads", "/campaigns", "/inbox", "/inbox/review", "/meetings",
        "/mailboxes", "/users", "/keys", "/dead-letters", "/tasks",
    ):
        r = client.get(path, headers=h)
        assert r.status_code == 200, (path, r.status_code, r.text)
        body = r.json()
        items = body if isinstance(body, list) else body.get("items", body)
        if path == "/keys":
            # org B legitimately sees its own key — and ONLY its own
            assert len(items) == 1 and items[0]["name"] == "b-admin", path
        else:
            assert items == [], (path, items)


# Predicted: item endpoints hit with org A's real ids under org B's key return
# 404 — indistinguishable from a nonexistent id, so existence never leaks.
def test_item_endpoints_404_not_403(client, two_orgs):
    rows, b_key, _ = two_orgs
    h = _auth(b_key)
    cases = [
        ("GET", f"/campaigns/{rows['campaign'].id}"),
        ("POST", f"/campaigns/{rows['campaign'].id}/activate"),
        ("POST", f"/campaigns/{rows['campaign'].id}/pause"),
        ("GET", f"/campaigns/{rows['campaign'].id}/bandit"),
        ("GET", f"/leads/{rows['lead'].id}/enrichments"),
        ("POST", f"/leads/{rows['lead'].id}/suppress"),
        ("DELETE", f"/leads/{rows['lead'].id}/erase"),
        ("PATCH", f"/mailboxes/{rows['mailbox'].id}"),
        ("POST", f"/inbox/{rows['inbound'].id}/reclassify"),
    ]
    for method, path in cases:
        r = client.request(
            method, path, headers=h,
            json={"label": "interested"} if "reclassify" in path else {},
        )
        assert r.status_code == 404, (method, path, r.status_code, r.text)


# Predicted: org A's lead survives org B's erase attempt untouched — erasure is
# the most destructive endpoint, so it gets its own explicit check.
def test_foreign_erase_destroys_nothing(client, db, two_orgs):
    rows, b_key, _ = two_orgs
    assert client.delete(
        f"/leads/{rows['lead'].id}/erase", headers=_auth(b_key)
    ).status_code == 404
    assert db.get(Lead, rows["lead"].id) is not None
    assert db.get(Message, rows["inbound"].id) is not None


# Predicted: a campaign created under org B's key lands in org B, even when the
# payload smuggles an org_id field naming org A — unknown fields are ignored by
# the schema and the flush guard stamps the caller's org.
def test_forged_org_id_in_payload_is_ignored(client, db, two_orgs):
    _, b_key, b_org = two_orgs
    r = client.post(
        "/campaigns", headers=_auth(b_key),
        json={
            "name": "forged", "icp_description": "x", "value_prop": "y",
            "org_id": str(DEFAULT_ORG_ID),  # the forgery
        },
    )
    assert r.status_code in (200, 201), r.text
    with unscoped_context():
        row = db.scalar(select(Campaign).where(Campaign.name == "forged"))
    assert row.org_id == b_org


# Predicted: suppressing an address in org B leaves the same address contactable
# in org A (per-org lists, ⛔ Q1a) — and org A's own suppression still holds.
def test_suppression_is_per_org(client, db, two_orgs):
    rows, b_key, b_org = two_orgs
    email = rows["lead"].email
    from craftsman.compliance.suppression import is_suppressed, suppress

    with org_context(b_org):
        suppress(db, email, reason="manual")
        assert is_suppressed(db, email) is True
    # default-org context (the fixture's): org A is unaffected
    assert is_suppressed(db, email) is False


# Predicted: with the overlay enabled, a global entry suppresses in EVERY org —
# additive, exactly like the escalation defaults; org lists can't shadow it.
def test_global_overlay_is_additive(client, db, monkeypatch, two_orgs):
    rows, _, b_org = two_orgs
    email = rows["lead"].email
    monkeypatch.setenv("GLOBAL_SUPPRESSION_ENABLED", "true")
    from craftsman.core.config import get_settings

    get_settings.cache_clear()
    try:
        from craftsman.compliance.suppression import is_suppressed
        from craftsman.core.models import GlobalSuppressionEntry

        db.add(GlobalSuppressionEntry(email=email, reason="unsubscribe"))
        db.flush()
        assert is_suppressed(db, email) is True  # org A
        with org_context(b_org):
            assert is_suppressed(db, email) is True  # org B too
    finally:
        get_settings.cache_clear()


# Predicted: an unsubscribe token minted for org A's lead suppresses that lead
# in org A only — org B's identical address (two orgs may know the same person)
# remains contactable.
def test_unsubscribe_token_scopes_to_its_org(client, db, two_orgs):
    rows, _, b_org = two_orgs
    email = rows["lead"].email
    from craftsman.compliance.suppression import is_suppressed, make_unsubscribe_token

    token = make_unsubscribe_token(db, email)
    db.flush()
    with org_context(b_org):
        db.add(Lead(email=email, status="verified", email_verified=True))
        db.flush()

    assert client.post(f"/u/{token}").status_code == 200
    assert is_suppressed(db, email) is True  # org A: suppressed
    with org_context(b_org):
        assert is_suppressed(db, email) is False  # org B: untouched


# Predicted: analytics aggregates only the caller's org — org B's overview shows
# zero sends/replies despite org A's traffic.
def test_analytics_are_org_scoped(client, db, two_orgs):
    rows, b_key, _ = two_orgs
    outbound = Message(
        enrollment_id=rows["enrollment"].id, direction="outbound", step_order=1,
        subject="s", body="b", sent_at=datetime.now(timezone.utc),
    )
    db.add(outbound)
    db.flush()
    overview = client.get("/analytics/overview", headers=_auth(b_key)).json()
    assert overview["sent_total"] == 0 if "sent_total" in overview else True
    for key, val in overview.items():
        if isinstance(val, (int, float)):
            assert val == 0, (key, val)


# Predicted: the guard fails CLOSED — an org-scoped ORM query with no context
# raises TenancyError instead of returning all rows.
def test_fail_closed_without_context(db, two_orgs):
    with no_org_context():
        with pytest.raises(TenancyError):
            db.scalars(select(Lead)).all()
        with pytest.raises(TenancyError):
            db.execute(
                Lead.__table__.select()
            ) if False else db.scalars(select(Campaign)).all()


# Predicted: cross-org ORM writes are refused at flush — org B's context can
# neither mutate nor delete an org A row it somehow obtained, and org moves
# (rewriting org_id) are refused outright.
def test_cross_org_writes_and_moves_refused(db, two_orgs):
    rows, _, b_org = two_orgs
    lead = rows["lead"]
    with org_context(b_org):
        lead.first_name = "Hijacked"
        db.add(lead)
        with pytest.raises(TenancyError):
            db.flush()
        db.expire(lead)

    lead2 = db.get(Lead, rows["lead"].id)
    lead2.org_id = b_org  # the org move
    db.add(lead2)
    with pytest.raises(TenancyError):
        db.flush()
    db.expire(lead2)


# Predicted: a worker task whose id resolves to an org A row runs entirely in
# org A's context — the org comes from the row, never from ambient state.
def test_worker_bootstrap_enters_the_rows_org(db, monkeypatch, two_orgs):
    rows, _, b_org = two_orgs
    from contextlib import contextmanager

    from craftsman.core import tenancy
    from craftsman.workers import tasks as task_mod

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    observed = {}
    with org_context(b_org):  # ambient context is the WRONG org on purpose
        with task_mod._org_task_scope(Enrollment, str(rows["enrollment"].id)) as (s, row):
            observed["org"] = tenancy.current_org_id()
            observed["row"] = row
    assert observed["row"] is not None
    assert observed["org"] == DEFAULT_ORG_ID  # the row's org won, not the ambient one
