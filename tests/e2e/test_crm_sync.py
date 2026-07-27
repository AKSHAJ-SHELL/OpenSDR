"""CRM sync (M5.2) end-to-end: connection lifecycle with write-only
credentials, dry-run preview vs committed import through the real ingest gate
(dedupe/suppression/CRM-owned updates/links), campaign enrollment for the
verified subset, outbound activity push + watermark, and sync-run bookkeeping.

The CRM itself is a stub CRMProvider — the adapters have their own
MockTransport units; here everything from the router down is real.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from craftsman.api.routers import crm as crm_router
from craftsman.compliance.suppression import suppress
from craftsman.core.models import (
    Campaign,
    Company,
    CRMConnection,
    CRMLink,
    Enrollment,
    Lead,
    Message,
    SequenceStep,
)
from craftsman.crm import sync as crm_sync
from craftsman.crm.provider import CRMContact, CRMListRef

HS_CREDS = {"access_token": "pat-x"}


def _admin(make_key):
    return {"Authorization": f"Bearer {make_key('admin', 'operate', 'read')}"}


class StubCRM:
    provider = "hubspot"

    def __init__(self, contacts=None, fail_kinds=()):
        self._contacts = contacts or []
        self._fail_kinds = set(fail_kinds)
        self.logged: list = []

    async def test(self):
        return "HubSpot portal 42 (stub)"

    async def lists(self):
        return [CRMListRef(remote_id="7", name="Closed-Lost Q2", size=len(self._contacts))]

    async def contacts(self, list_id):
        assert list_id == "7"
        return self._contacts

    async def log_activity(self, activity):
        if activity.kind in self._fail_kinds:
            raise RuntimeError("CRM rejected it")
        self.logged.append(activity)

    async def log_meeting(self, activity):
        self.logged.append(activity)


@pytest.fixture()
def stub_crm(monkeypatch):
    stub = StubCRM()

    def build(connection):
        return stub

    # the router imported the symbol; sync.py's push path uses its own module
    monkeypatch.setattr(crm_router, "build_crm_client", build)
    monkeypatch.setattr(crm_sync, "build_crm_client", build)
    return stub


def _connection(client, h, field_map=None) -> str:
    r = client.post(
        "/crm/connections",
        json={
            "provider": "hubspot",
            "name": "prod hubspot",
            "credentials": HS_CREDS,
            "field_map": field_map or {},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _contact(email, **fields):
    return CRMContact(
        remote_id=fields.pop("remote_id", uuid.uuid4().hex[:12]),
        remote_type="contact",
        fields={"email": email, **fields},
    )


# ---------------------------------------------------------------- connections


def test_credentials_are_write_only_everywhere(client, db, make_key):
    h = _admin(make_key)
    cid = _connection(client, h)
    for payload in (
        client.get("/crm/connections", headers=h).json(),
        client.patch(f"/crm/connections/{cid}", json={"name": "renamed"}, headers=h).json(),
    ):
        text = str(payload)
        assert "pat-x" not in text
        assert "credentials" not in text
    # stored encrypted, not plaintext
    row = db.get(CRMConnection, uuid.UUID(cid))
    assert "pat-x" not in row.credentials_enc


def test_connection_validation_rejects_bad_input(client, make_key):
    h = _admin(make_key)
    r = client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "x", "credentials": {}},
        headers=h,
    )
    assert r.status_code == 422
    assert "access_token" in r.text
    # salesforce instance_url goes through the SSRF guard at write time
    r = client.post(
        "/crm/connections",
        json={
            "provider": "salesforce",
            "name": "sf",
            "credentials": {
                "instance_url": "http://10.0.0.1",
                "client_id": "a",
                "client_secret": "b",
            },
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "instance_url refused" in r.text
    # field-map overlay is validated on the same path as the mapping module
    r = client.post(
        "/crm/connections",
        json={
            "provider": "hubspot",
            "name": "x",
            "credentials": HS_CREDS,
            "field_map": {"anything": "icp_score"},
        },
        headers=h,
    )
    assert r.status_code == 422


def test_test_endpoint_reports_instead_of_500(client, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    r = client.post(f"/crm/connections/{cid}/test", headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": "HubSpot portal 42 (stub)"}


def test_lists_come_from_the_provider(client, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    r = client.get(f"/crm/connections/{cid}/lists", headers=h)
    assert r.status_code == 200
    assert r.json()[0]["name"] == "Closed-Lost Q2"


# ---------------------------------------------------------------- inbound import


def test_dry_run_previews_without_writing(client, db, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    # an existing lead the CRM knows a newer title for
    company = Company(domain="acme.test", name="Acme")
    db.add(company)
    db.flush()
    db.add(Lead(email="jane@acme.test", company_id=company.id, title="Manager",
                status="verified", email_verified=True))
    suppress(db, "optout@x.test", reason="unsubscribe")
    db.flush()

    stub_crm._contacts = [
        _contact("jane@acme.test", jobtitle="VP Engineering"),
        _contact("new@fresh.test", firstname="New"),
        _contact("optout@x.test"),
        _contact(""),  # no email
    ]
    before = db.query(Lead).count()
    r = client.post(
        f"/crm/connections/{cid}/import", json={"list_id": "7"}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["stats"] == {
        "create": 1, "update": 1, "unchanged": 0, "suppressed": 1, "no_email": 1
    }
    update = next(p for p in body["preview"] if p["action"] == "update")
    assert update["changes"]["title"] == {"from": "Manager", "to": "VP Engineering"}
    # nothing was written: no lead, no link, no run
    assert db.query(Lead).count() == before
    assert db.query(CRMLink).count() == 0
    assert client.get(f"/crm/connections/{cid}/runs", headers=h).json() == []


def test_commit_imports_updates_links_and_records_a_run(client, db, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    company = Company(domain="acme.test", name="Acme")
    db.add(company)
    db.flush()
    existing = Lead(email="jane@acme.test", company_id=company.id, title="Manager",
                    status="verified", email_verified=True)
    db.add(existing)
    db.flush()

    stub_crm._contacts = [
        _contact("jane@acme.test", jobtitle="VP Engineering", remote_id="hs-1"),
        _contact("new@fresh.test", firstname="New", lastname="Lead", remote_id="hs-2"),
    ]
    r = client.post(
        f"/crm/connections/{cid}/import",
        json={"list_id": "7", "dry_run": False},
        headers=h,
    )
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    assert stats["imported"] == 1 and stats["updated"] == 1 and stats["linked"] == 2

    db.expire_all()
    assert existing.title == "VP Engineering"  # CRM wins contact fields
    assert existing.status == "verified"  # Craftsman keeps engagement/status
    created = db.query(Lead).filter(Lead.email == "new@fresh.test").one()
    assert created.source == "crm:hubspot"
    links = {link.remote_id for link in db.query(CRMLink).all()}
    assert links == {"hs-1", "hs-2"}

    runs = client.get(f"/crm/connections/{cid}/runs", headers=h).json()
    assert len(runs) == 1
    assert runs[0]["direction"] == "inbound"
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["stats"]["imported"] == 1

    # re-import is idempotent: dedupe, no duplicate links
    r = client.post(
        f"/crm/connections/{cid}/import",
        json={"list_id": "7", "dry_run": False},
        headers=h,
    )
    assert r.json()["stats"]["imported"] == 0
    assert db.query(CRMLink).count() == 2


def test_commit_enrolls_verified_leads_into_given_campaign(client, db, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    company = Company(domain="acme.test", name="Acme")
    db.add(company)
    db.flush()
    # verified + enrollable; hash embedder and default threshold let a
    # plausible title through (same seeding style as campaign tests)
    db.add(Lead(email="jane@acme.test", company_id=company.id, title="VP Engineering",
                status="verified", email_verified=True))
    campaign = Campaign(name="re-engage", icp_description="engineering leaders",
                        value_prop="x", status="active")
    db.add(campaign)
    db.flush()

    stub_crm._contacts = [
        _contact("jane@acme.test", remote_id="hs-1"),
        _contact("fresh@new.test", remote_id="hs-2"),  # unverified — not enrolled
    ]
    r = client.post(
        f"/crm/connections/{cid}/import",
        json={"list_id": "7", "dry_run": False, "campaign_id": str(campaign.id)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    assert stats["campaign_id"] == str(campaign.id)

    enrollments = db.query(Enrollment).filter(Enrollment.campaign_id == campaign.id).all()
    enrolled_leads = {e.lead_id for e in enrollments}
    jane = db.query(Lead).filter(Lead.email == "jane@acme.test").one()
    fresh = db.query(Lead).filter(Lead.email == "fresh@new.test").one()
    assert fresh.id not in enrolled_leads  # unverified: waits for verify + activate
    # jane enrolls iff she cleared the ICP gate — either way she was scored
    assert jane.icp_scored_campaign_id == campaign.id
    assert stats["enrolled"] == len(enrollments)


def test_import_into_foreign_or_missing_campaign_404s(client, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    r = client.post(
        f"/crm/connections/{cid}/import",
        json={"list_id": "7", "dry_run": False, "campaign_id": str(uuid.uuid4())},
        headers=h,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------- outbound push


def _thread(db, *, with_meeting=False):
    """Campaign + lead + enrollment + one sent outbound + one classified reply."""
    company = Company(domain=f"t-{uuid.uuid4().hex[:6]}.test", name="T")
    db.add(company)
    db.flush()
    campaign = Campaign(name="t", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    db.add(SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=1))
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@t.test", company_id=company.id,
                status="verified", email_verified=True)
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="replied_interested")
    db.add(enr)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(Message(enrollment_id=enr.id, direction="outbound", step_order=1,
                   subject="hi", body="outbound body", sent_at=now - timedelta(hours=2)))
    db.add(Message(enrollment_id=enr.id, direction="inbound", subject="Re: hi",
                   body="interested!", classification="interested",
                   classification_confidence=0.93))
    db.flush()
    if with_meeting:
        from craftsman.core.models import Meeting

        db.add(Meeting(enrollment_id=enr.id, provider="calcom",
                       provider_event_id=uuid.uuid4().hex, status="booked",
                       start_at=now + timedelta(days=1)))
        db.flush()
    return lead


def test_sync_pushes_linked_activity_and_advances_watermark(client, db, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    lead = _thread(db, with_meeting=True)
    connection = db.get(CRMConnection, uuid.UUID(cid))
    db.add(CRMLink(connection_id=connection.id, lead_id=lead.id,
                   remote_id="hs-9", remote_type="contact"))
    db.flush()

    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "succeeded"
    assert run["stats"] == {"activities": 3, "pushed": 3, "failed": 0}
    kinds = sorted(a.kind for a in stub_crm.logged)
    assert kinds == ["meeting", "reply", "send"]
    assert all(a.remote_id == "hs-9" for a in stub_crm.logged)

    # watermark advanced: a second sync pushes nothing
    stub_crm.logged.clear()
    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    assert r.json()["stats"]["activities"] == 0
    assert stub_crm.logged == []


def test_unlinked_leads_never_leak_to_the_crm(client, db, make_key, stub_crm):
    """Only explicitly linked contacts get activity — a lead the CRM has never
    seen is not written to it."""
    h = _admin(make_key)
    cid = _connection(client, h)
    _thread(db)  # activity exists, but no CRMLink
    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    assert r.json()["stats"] == {"activities": 0, "pushed": 0, "failed": 0}
    assert stub_crm.logged == []


def test_partial_push_failure_is_tallied_not_fatal(client, db, make_key, stub_crm):
    h = _admin(make_key)
    cid = _connection(client, h)
    lead = _thread(db)
    connection = db.get(CRMConnection, uuid.UUID(cid))
    db.add(CRMLink(connection_id=connection.id, lead_id=lead.id,
                   remote_id="hs-9", remote_type="contact"))
    db.flush()
    stub_crm._fail_kinds = {"reply"}

    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    run = r.json()
    assert run["status"] == "succeeded"  # the run ran; failures are in the tally
    assert run["stats"] == {"activities": 2, "pushed": 1, "failed": 1}
    # at-most-once: the failed activity is NOT retried on the next run
    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    assert r.json()["stats"]["activities"] == 0


def test_provider_outage_records_a_failed_run(client, db, make_key, monkeypatch):
    h = _admin(make_key)
    cid = _connection(client, h)

    def boom(connection):
        raise RuntimeError("token expired")

    monkeypatch.setattr(crm_router, "build_crm_client", boom)
    monkeypatch.setattr(crm_sync, "build_crm_client", boom)
    r = client.post(f"/crm/connections/{cid}/sync", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert "token expired" in r.json()["error"]


# ---------------------------------------------------------------- scopes


def test_scope_enforcement(client, make_key):
    read_only = {"Authorization": f"Bearer {make_key('read')}"}
    operate = {"Authorization": f"Bearer {make_key('operate', 'read')}"}
    assert client.post(
        "/crm/connections",
        json={"provider": "hubspot", "name": "x", "credentials": HS_CREDS},
        headers=operate,
    ).status_code == 403  # credentials are admin-only
    assert client.get("/crm/connections", headers=read_only).status_code == 200
    fake = str(uuid.uuid4())
    assert client.post(
        f"/crm/connections/{fake}/import", json={"list_id": "7"}, headers=read_only
    ).status_code == 403
