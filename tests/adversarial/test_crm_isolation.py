"""M5.2 — CRM cross-tenant isolation (extends the ⛔ Gate M5 suite to the new
surface). Method per TESTING.md §3: prediction stated above each test, then
run. CRM connections carry CREDENTIALS and the import path writes customer
data — the two things a tenancy hole here would leak or corrupt.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from craftsman.api.auth import generate_token, hash_token, key_prefix
from craftsman.api.routers import crm as crm_router
from craftsman.compliance.suppression import suppress
from craftsman.core.models import (
    ApiKey,
    Campaign,
    Company,
    CRMConnection,
    CRMLink,
    Enrollment,
    Lead,
    Message,
    Org,
)
from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context, unscoped_context
from craftsman.crm import sync as crm_sync
from craftsman.crm.provider import CRMContact
from craftsman.crm.sync import encrypt_credentials


class StubCRM:
    provider = "hubspot"

    def __init__(self):
        self.contacts_list: list = []
        self.logged: list = []

    async def test(self):
        return "stub"

    async def lists(self):
        return []

    async def contacts(self, list_id):
        return self.contacts_list

    async def log_activity(self, activity):
        self.logged.append(activity)

    async def log_meeting(self, activity):
        self.logged.append(activity)


@pytest.fixture()
def stub_crm(monkeypatch):
    stub = StubCRM()
    monkeypatch.setattr(crm_router, "build_crm_client", lambda c: stub)
    monkeypatch.setattr(crm_sync, "build_crm_client", lambda c: stub)
    return stub


def _mk_connection(db, name="a-conn") -> CRMConnection:
    connection = CRMConnection(
        provider="hubspot", name=name,
        credentials_enc=encrypt_credentials({"access_token": "sekrit-a"}),
        field_map={},
    )
    db.add(connection)
    db.flush()
    return connection


@pytest.fixture()
def crm_two_orgs(db):
    """Org A (default ctx): a connection + a linked lead with activity.
    Org B: fresh org + admin key. Returns (a_rows, b_token, b_org_id)."""
    company = Company(domain=f"crm-{uuid.uuid4().hex[:8]}.example", name="A Co")
    db.add(company)
    db.flush()
    lead = Lead(email=f"a-{uuid.uuid4().hex[:6]}@example.com",
                company_id=company.id, status="verified", email_verified=True)
    campaign = Campaign(name="a-camp", icp_description="x", value_prop="y")
    db.add_all([lead, campaign])
    db.flush()
    enrollment = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="active")
    db.add(enrollment)
    db.flush()
    db.add(Message(enrollment_id=enrollment.id, direction="outbound", step_order=1,
                   subject="a-mail", body="x",
                   sent_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    connection = _mk_connection(db)
    db.add(CRMLink(connection_id=connection.id, lead_id=lead.id,
                   remote_id="hs-a-1", remote_type="contact"))
    db.flush()

    with unscoped_context():
        org_b = Org(name="Org B", slug=f"crm-b-{uuid.uuid4().hex[:6]}")
        db.add(org_b)
        db.flush()
    token = generate_token()
    with org_context(org_b.id):
        db.add(ApiKey(name="b-admin", key_prefix=key_prefix(token),
                      key_hash=hash_token(token),
                      scopes=["admin", "operate", "read"]))
        db.flush()

    rows = {"connection": connection, "campaign": campaign, "lead": lead}
    db.expunge_all()  # production parity (see test_tenancy_isolation.py)
    return rows, token, org_b.id


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# Predicted: org B's key sees ZERO connections although org A has one — the
# listing is filtered by the session guard, and no credential material exists
# in any response body regardless of org.
def test_connection_listing_is_isolated(client, crm_two_orgs):
    _, b_key, _ = crm_two_orgs
    r = client.get("/crm/connections", headers=_auth(b_key))
    assert r.status_code == 200
    assert r.json() == []
    assert "sekrit" not in r.text


# Predicted: every item route on org A's connection id under org B's key
# returns 404 — never 403, never a provider call. The stub records calls, so
# an empty `logged`/no contacts fetch proves the adapter was never built for
# a foreign row.
def test_foreign_connection_id_is_indistinguishable_from_nonexistent(
    client, crm_two_orgs, stub_crm
):
    rows, b_key, _ = crm_two_orgs
    cid = rows["connection"].id
    h = _auth(b_key)
    cases = [
        ("PATCH", f"/crm/connections/{cid}", {"name": "stolen"}),
        ("POST", f"/crm/connections/{cid}/test", None),
        ("GET", f"/crm/connections/{cid}/lists", None),
        ("POST", f"/crm/connections/{cid}/import", {"list_id": "7"}),
        ("POST", f"/crm/connections/{cid}/sync", None),
        ("GET", f"/crm/connections/{cid}/runs", None),
    ]
    for method, path, body in cases:
        r = client.request(method, path, json=body, headers=h)
        assert r.status_code == 404, (method, path, r.status_code)
    assert stub_crm.logged == []


# Predicted: a forged org_id in the connection-create payload is ignored
# (schema doesn't even carry it; construction-time stamping wins): the row
# lands in org B and org A cannot see it.
def test_forged_org_id_in_create_lands_in_callers_org(client, db, crm_two_orgs):
    _, b_key, b_org_id = crm_two_orgs
    r = client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "b-conn",
              "credentials": {"access_token": "b-tok"},
              "org_id": str(DEFAULT_ORG_ID)},  # forged
        headers=_auth(b_key),
    )
    assert r.status_code == 201
    with unscoped_context():
        row = db.scalar(select(CRMConnection).where(CRMConnection.name == "b-conn"))
        assert row.org_id == b_org_id


# Predicted: org B importing into org A's campaign 404s at the campaign check
# — before any contact is fetched or written.
def test_import_cannot_target_a_foreign_campaign(client, db, crm_two_orgs, stub_crm):
    rows, b_key, b_org_id = crm_two_orgs
    h = _auth(b_key)
    r = client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "b-conn2",
              "credentials": {"access_token": "b-tok"}},
        headers=h,
    )
    b_cid = r.json()["id"]
    stub_crm.contacts_list = [
        CRMContact(remote_id="hs-x", remote_type="contact",
                   fields={"email": "x@b.example"})
    ]
    r = client.post(
        f"/crm/connections/{b_cid}/import",
        json={"list_id": "7", "dry_run": False,
              "campaign_id": str(rows["campaign"].id)},
        headers=h,
    )
    assert r.status_code == 404
    # nothing was imported on the failed path
    with org_context(b_org_id):
        assert db.scalar(select(Lead).where(Lead.email == "x@b.example")) is None


# Predicted: org B's outbound sync pushes NOTHING even though org A has
# pushable linked activity — links, messages, and connections are all
# org-scoped, so the collector finds zero rows.
def test_outbound_sync_cannot_carry_foreign_activity(client, crm_two_orgs, stub_crm):
    _, b_key, _ = crm_two_orgs
    h = _auth(b_key)
    r = client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "b-conn3",
              "credentials": {"access_token": "b-tok"}},
        headers=h,
    )
    b_cid = r.json()["id"]
    r = client.post(f"/crm/connections/{b_cid}/sync", headers=h)
    assert r.status_code == 200
    assert r.json()["stats"] == {"activities": 0, "pushed": 0, "failed": 0}
    assert stub_crm.logged == []


# Predicted: suppression stays per-org through the CRM path (⛔ Q1a): the same
# address suppressed in org B does not block org A's import, and org A's
# suppression DOES block it in org A.
def test_crm_import_respects_per_org_suppression(client, db, crm_two_orgs, stub_crm, make_key):
    _, _, b_org_id = crm_two_orgs
    email = f"both-{uuid.uuid4().hex[:6]}@example.com"
    with org_context(b_org_id):
        suppress(db, email, reason="unsubscribe")
        db.flush()

    a_key = make_key("admin", "operate", "read")
    h = _auth(a_key)
    r = client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "a-conn2",
              "credentials": {"access_token": "a-tok"}},
        headers=h,
    )
    a_cid = r.json()["id"]
    stub_crm.contacts_list = [
        CRMContact(remote_id="hs-s", remote_type="contact", fields={"email": email})
    ]
    r = client.post(
        f"/crm/connections/{a_cid}/import",
        json={"list_id": "7", "dry_run": False},
        headers=h,
    )
    assert r.json()["stats"]["imported"] == 1  # B's suppression is invisible to A

    # now suppress in A and re-import a fresh address: blocked in A
    email2 = f"a-only-{uuid.uuid4().hex[:6]}@example.com"
    suppress(db, email2, reason="unsubscribe")
    db.flush()
    stub_crm.contacts_list = [
        CRMContact(remote_id="hs-s2", remote_type="contact", fields={"email": email2})
    ]
    r = client.post(
        f"/crm/connections/{a_cid}/import",
        json={"list_id": "7", "dry_run": False},
        headers=h,
    )
    assert r.json()["stats"]["imported"] == 0
    assert r.json()["stats"]["suppressed"] == 1


# Predicted: the beat sweep derives each connection's org from per-org
# iteration — two orgs with connections each push exactly their own linked
# activity, never the other's, in one tick.
def test_beat_sweep_keeps_orgs_apart(db, crm_two_orgs, stub_crm, monkeypatch):
    from craftsman.workers import tasks

    rows, _, b_org_id = crm_two_orgs
    # org B: its own lead + connection + link + activity
    with org_context(b_org_id):
        company = Company(domain=f"b-{uuid.uuid4().hex[:6]}.example", name="B Co")
        db.add(company)
        db.flush()
        lead = Lead(email=f"b-{uuid.uuid4().hex[:6]}@example.com",
                    company_id=company.id, status="verified", email_verified=True)
        campaign = Campaign(name="b-camp", icp_description="x", value_prop="y")
        db.add_all([lead, campaign])
        db.flush()
        enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="active")
        db.add(enr)
        db.flush()
        db.add(Message(enrollment_id=enr.id, direction="outbound", step_order=1,
                       subject="b-mail", body="x",
                       sent_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
        b_conn = _mk_connection(db, name="b-conn")
        db.add(CRMLink(connection_id=b_conn.id, lead_id=lead.id,
                       remote_id="hs-b-1", remote_type="contact"))
        db.flush()

    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr(tasks, "session_scope", scope)
    pushed = tasks.crm_sync_tick.run()
    assert pushed == 2  # one send per org

    by_remote = {a.remote_id for a in stub_crm.logged}
    assert by_remote == {"hs-a-1", "hs-b-1"}
    # each org's run rows exist and never mention the other org's connection
    with org_context(b_org_id):
        b_runs = db.scalars(select(CRMConnection)).all()
        assert {c.name for c in b_runs} == {"b-conn"}
