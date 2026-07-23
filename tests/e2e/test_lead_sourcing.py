"""End-to-end lead sourcing (M2.2): search → preview → import → enrich enqueue.

Guarantees under test:
- POST /leads/source previews candidates with per-row gate labels, writing nothing;
- provider not configured → clear 400; auth gated to `operate`;
- POST /leads/source/import persists only new+valid, stamps the source, enqueues
  enrich_lead for exactly this batch, and the lead then shows up in GET /leads.
"""

import pytest
from sqlalchemy import select

from craftsman.core.config import Settings
from craftsman.core.models import Lead, SuppressionEntry
from craftsman.ingest.gate import LeadRow


class FakeProvider:
    name = "apollo"

    def __init__(self, rows):
        self._rows = rows

    async def search(self, query):
        return self._rows


@pytest.fixture()
def configured(monkeypatch):
    """Enable the apollo source and hand the router a fake provider (no network)."""
    monkeypatch.setattr(
        "craftsman.api.routers.leads.get_settings",
        lambda: Settings(lead_source_providers="apollo", apollo_api_key="k", _env_file=None),
    )

    def _install(rows):
        monkeypatch.setattr(
            "craftsman.ingest.sourcing.build_source_provider",
            lambda settings, name: FakeProvider(rows) if name == "apollo" else None,
        )

    return _install


def _h(make_key, scope="operate"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def test_preview_labels_candidates_and_writes_nothing(client, db, make_key, configured):
    db.add(SuppressionEntry(email="stop@acme.com", reason="gdpr"))
    db.add(Lead(email="known@acme.com", company_id=None))
    db.flush()
    configured(
        [
            LeadRow(email="new@acme.com", title="VP", company_domain="acme.com"),
            LeadRow(email="known@acme.com"),
            LeadRow(email="stop@acme.com"),
            LeadRow(email="bad"),
        ]
    )
    resp = client.post(
        "/leads/source",
        headers=_h(make_key),
        json={"provider": "apollo", "icp_query": "ops", "limit": 25},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "apollo"
    assert (body["new"], body["duplicate"], body["suppressed"], body["invalid"]) == (1, 1, 1, 1)
    statuses = {c["email"]: c["status"] for c in body["candidates"]}
    assert statuses == {
        "new@acme.com": "new",
        "known@acme.com": "duplicate",
        "stop@acme.com": "suppressed",
        "bad": "invalid",
    }
    # nothing was written by preview
    assert db.scalar(select(Lead).where(Lead.email == "new@acme.com")) is None


def test_import_persists_and_enqueues(client, db, make_key, configured, monkeypatch):
    configured([])  # provider unused by the import endpoint
    enqueued = []
    monkeypatch.setattr(
        "craftsman.workers.tasks.enrich_lead.delay", lambda lead_id: enqueued.append(lead_id)
    )
    resp = client.post(
        "/leads/source/import",
        headers=_h(make_key),
        json={
            "source": "apollo",
            "leads": [
                {"email": "sourced@acme.com", "title": "VP", "company_domain": "acme.com"},
                {"email": "bad"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1
    lead = db.scalar(select(Lead).where(Lead.email == "sourced@acme.com"))
    assert lead is not None and lead.source == "apollo" and lead.status == "new"
    assert enqueued == [str(lead.id)]  # exactly this batch, not every `new` lead

    # and it surfaces in the leads list with its provider source
    listed = client.get("/leads", headers=_h(make_key, "read")).json()
    assert any(item["email"] == "sourced@acme.com" and item["source"] == "apollo" for item in listed)


def test_unconfigured_provider_is_a_clean_400(client, db, make_key, monkeypatch):
    monkeypatch.setattr(
        "craftsman.api.routers.leads.get_settings", lambda: Settings(_env_file=None)
    )
    resp = client.post("/leads/source", headers=_h(make_key), json={"provider": "apollo"})
    assert resp.status_code == 400 and "not configured" in resp.json()["detail"]


def test_source_endpoints_require_operate_scope(client, db, make_key):
    assert client.post("/leads/source", json={"provider": "apollo"}).status_code == 401
    # read-scope key is insufficient for the operate-gated source endpoints
    r = client.post(
        "/leads/source", headers=_h(make_key, "read"), json={"provider": "apollo"}
    )
    assert r.status_code == 403


def test_provider_failure_surfaces_as_502(client, db, make_key, monkeypatch):
    class Boom:
        name = "apollo"

        async def search(self, query):
            raise RuntimeError("apollo rate limit")

    monkeypatch.setattr(
        "craftsman.api.routers.leads.get_settings",
        lambda: Settings(lead_source_providers="apollo", apollo_api_key="k", _env_file=None),
    )
    monkeypatch.setattr(
        "craftsman.ingest.sourcing.build_source_provider", lambda s, n: Boom()
    )
    resp = client.post("/leads/source", headers=_h(make_key), json={"provider": "apollo"})
    assert resp.status_code == 502 and "apollo rate limit" in resp.json()["detail"]
