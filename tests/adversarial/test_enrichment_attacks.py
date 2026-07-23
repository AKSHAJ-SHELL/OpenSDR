"""Adversarial (M2.1): enrichment sits on a hostile network boundary AND writes into
operator data. Predict-then-run (TESTING.md §3). The two invariants under attack:

1. a dead/slow/lying provider never blocks the chain — and never costs a lead its
   verification;
2. a provider value never overwrites operator-supplied (CSV) data — but the
   disagreement is still recorded in `lead_enrichments`.
"""

import uuid

import httpx
import pytest

from craftsman.core.models import Company, Lead, LeadEnrichmentRecord
from craftsman.ingest.enrichment import (
    ApolloProvider,
    EnrichmentInput,
    EnrichmentResult,
    HunterProvider,
    apply_enrichment,
    chain_enrich,
)
from sqlalchemy import select

INP = EnrichmentInput(email="dana@acme.com", company_domain="acme.com")


class Healthy:
    name = "healthy"

    async def enrich(self, inp):
        return EnrichmentResult("healthy", 0.9, {"title": "VP Ops"})


def _raising_provider(cls, exc):
    p = cls("key")

    async def boom(inp):
        raise exc

    p._fetch = boom
    return p


# ------------------------------------------------- invariant 1: failure isolation


@pytest.mark.parametrize(
    "exc",
    [
        httpx.TimeoutException("timed out"),
        httpx.ConnectError("refused"),
        httpx.HTTPStatusError(
            "500", request=httpx.Request("GET", "https://x"), response=httpx.Response(500)
        ),
        ValueError("malformed json"),
        RuntimeError("provider bug"),
    ],
)
async def test_dead_provider_never_blocks_the_chain(exc):
    # Predicted: the failing provider is logged + skipped; the healthy one still lands.
    dead = _raising_provider(ApolloProvider, exc)
    merged, prov = await chain_enrich([dead, Healthy()], INP)
    assert merged == {"title": "VP Ops"}
    assert [p.source for p in prov] == ["healthy"]


async def test_malformed_body_is_a_skip_not_a_crash():
    # Predicted: a payload of the wrong shape raises inside the provider's parser and
    # is contained by the chain — no exception escapes, no garbage fields.
    p = HunterProvider("key")

    async def garbage(inp):
        return {"data": "not-a-dict"}

    p._fetch = garbage
    merged, prov = await chain_enrich([p, Healthy()], INP)
    assert merged == {"title": "VP Ops"} and len(prov) == 1


async def test_all_providers_dead_yields_empty_not_error():
    # Predicted: total provider outage ⇒ ({}, []) — the caller (enrich_lead) then has
    # nothing to write, and the lead's verification is untouched by construction.
    a = _raising_provider(ApolloProvider, httpx.TimeoutException("t"))
    b = _raising_provider(HunterProvider, httpx.ConnectError("c"))
    assert await chain_enrich([a, b], INP) == ({}, [])


# ------------------------------------------------- invariant 2: anti-clobber


def _lead(db, **kw):
    lead = Lead(email=kw.pop("email", f"l-{uuid.uuid4().hex[:8]}@acme.test"), **kw)
    db.add(lead)
    db.flush()
    return lead


def test_provider_value_never_overwrites_csv_data(db):
    # Predicted: the CSV title stays; the provider's dissenting answer is still
    # recorded in provenance so the disagreement is inspectable.
    lead = _lead(db, title="Operator-Supplied Title", source="csv")
    apply_enrichment(
        db,
        lead,
        {"title": "Provider Title", "phone": "+1999"},
        [
            _prov("title", "Provider Title"),
            _prov("phone", "+1999"),
        ],
    )
    db.flush()
    assert lead.title == "Operator-Supplied Title"  # kept
    assert lead.phone == "+1999"  # empty column filled
    assert lead.source == "csv"  # a real source is never relabeled
    rows = db.scalars(
        select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead.id)
    ).all()
    assert {(r.field, r.value) for r in rows} == {
        ("title", "Provider Title"),
        ("phone", "+1999"),
    }


def test_company_fields_fill_only_when_empty(db):
    company = Company(domain="acme.test", name="Operator Name")
    db.add(company)
    db.flush()
    lead = _lead(db, company_id=company.id)
    apply_enrichment(
        db,
        lead,
        {"company_name": "Provider Name", "company_industry": "logistics"},
        [_prov("company_name", "Provider Name"), _prov("company_industry", "logistics")],
    )
    db.flush()
    assert company.name == "Operator Name"  # kept
    assert company.industry == "logistics"  # filled


def test_enrichment_failure_never_unverifies_a_lead(db, monkeypatch):
    # Predicted: with verify passing and the enrichment step exploding, the task exits
    # with the lead verified and zero enrichment rows — invariant 1 at the task level.
    from contextlib import contextmanager

    from craftsman.workers import tasks as task_mod

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    monkeypatch.setattr("craftsman.ingest.verify.verify_email", lambda email: True)

    def explode(settings):
        raise RuntimeError("chain construction bug")

    monkeypatch.setattr("craftsman.ingest.enrichment.build_enrichment_chain", explode)

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


def _prov(field, value, source="apollo", confidence=0.9):
    from craftsman.ingest.enrichment import FieldProvenance

    return FieldProvenance(field, value, source, confidence)
