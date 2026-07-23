"""End-to-end enrichment pipeline (M2.1): verify → enrich → provenance → read surface.

The guarantees under test:
- `enrich_lead` verifies first and enriches only verified leads, filling empty
  canonical columns and writing `lead_enrichments` provenance;
- a lead with no company gets one attached by enriched domain;
- with `enrichment_providers=""` the task is verify-only (the keyless default);
- `GET /leads/{id}/enrichments` exposes provenance under `read` scope;
- GDPR erasure removes the provenance rows (they are provider-sourced PII).
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from craftsman.core.models import Company, Lead, LeadEnrichmentRecord
from craftsman.ingest.enrichment import EnrichmentResult
from craftsman.workers import tasks as task_mod


class FakeApollo:
    name = "apollo"

    async def enrich(self, inp):
        return EnrichmentResult(
            "apollo",
            0.9,
            {
                "title": "VP of Operations",
                "seniority": "vp",
                "phone": "+14155550123",
                "company_domain": "acme-enrich.test",
                "company_name": "Acme Corp",
                "company_industry": "logistics",
            },
        )


@pytest.fixture()
def wired(db, monkeypatch):
    """Route the task at the test transaction, pass verification, fake the chain."""

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    monkeypatch.setattr("craftsman.ingest.verify.verify_email", lambda email: True)
    monkeypatch.setattr(
        "craftsman.ingest.enrichment.build_enrichment_chain", lambda s: [FakeApollo()]
    )
    return db


def _lead(db, **kw):
    lead = Lead(email=kw.pop("email", f"l-{uuid.uuid4().hex[:8]}@acme-enrich.test"), **kw)
    db.add(lead)
    db.flush()
    return lead


def test_verify_then_enrich_fills_and_records(wired):
    db = wired
    lead = _lead(db, status="new", first_name="Dana")
    task_mod.enrich_lead(str(lead.id))
    db.flush()

    # verify happened
    assert lead.email_verified is True and lead.status == "verified"
    # empty canonical columns filled
    assert lead.title == "VP of Operations"
    assert lead.seniority == "vp" and lead.phone == "+14155550123"
    # company attached by enriched domain and filled
    assert lead.company_id is not None
    company = db.get(Company, lead.company_id)
    assert company.domain == "acme-enrich.test"
    assert company.name == "Acme Corp" and company.industry == "logistics"
    # sourced label: lead had no source → credited to the winning provider
    assert lead.source == "apollo"
    # provenance rows for every winning field
    rows = db.scalars(
        select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead.id)
    ).all()
    assert {r.field for r in rows} == {
        "title",
        "seniority",
        "phone",
        "company_domain",
        "company_name",
        "company_industry",
    }
    assert all(r.source == "apollo" and r.confidence == 0.9 for r in rows)


def test_unverified_lead_is_never_enriched(db, monkeypatch):
    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    monkeypatch.setattr("craftsman.ingest.verify.verify_email", lambda email: False)

    called = []
    monkeypatch.setattr(
        "craftsman.ingest.enrichment.build_enrichment_chain",
        lambda s: called.append(1) or [],
    )
    lead = _lead(db, status="new")
    task_mod.enrich_lead(str(lead.id))
    db.flush()
    assert lead.email_verified is False
    assert called == []  # provider budget untouched for dead addresses
    assert (
        db.scalars(
            select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead.id)
        ).all()
        == []
    )


def test_empty_provider_config_is_verify_only(db, monkeypatch):
    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    monkeypatch.setattr("craftsman.ingest.verify.verify_email", lambda email: True)
    # real build_enrichment_chain + a settings object with no providers configured
    from craftsman.core.config import Settings

    monkeypatch.setattr(
        "craftsman.core.config.get_settings", lambda: Settings(_env_file=None)
    )
    lead = _lead(db, status="new")
    task_mod.enrich_lead(str(lead.id))
    db.flush()
    assert lead.email_verified is True and lead.status == "verified"
    assert (
        db.scalars(
            select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead.id)
        ).all()
        == []
    )


# ---------------------------------------------------------------- read surface


def _headers(make_key, scope="read"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def test_enrichments_endpoint_returns_provenance(client, wired, make_key):
    db = wired
    lead = _lead(db)
    task_mod.enrich_lead(str(lead.id))
    db.flush()

    resp = client.get(f"/leads/{lead.id}/enrichments", headers=_headers(make_key))
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["field"] for r in rows} >= {"title", "phone"}
    sample = rows[0]
    assert set(sample) == {"field", "value", "source", "confidence", "fetched_at"}


def test_enrichments_endpoint_requires_auth_and_404s(client, db, make_key):
    lead = _lead(db)
    assert client.get(f"/leads/{lead.id}/enrichments").status_code == 401
    assert (
        client.get(f"/leads/{uuid.uuid4()}/enrichments", headers=_headers(make_key)).status_code
        == 404
    )


# ---------------------------------------------------------------- erasure


def test_erasure_removes_enrichment_provenance(wired):
    db = wired
    from craftsman.compliance.suppression import erase_lead

    lead = _lead(db)
    task_mod.enrich_lead(str(lead.id))
    db.flush()
    lead_id = lead.id
    assert (
        db.scalars(
            select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead_id)
        ).all()
        != []
    )

    erase_lead(db, lead)
    db.flush()
    assert (
        db.scalars(
            select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead_id)
        ).all()
        == []
    )
