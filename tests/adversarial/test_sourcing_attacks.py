"""Adversarial (M2.2): sourced leads must get ZERO shortcuts, and the webhook source
sits on a hostile network boundary. Predict-then-run (TESTING.md §3). The load-bearing
guarantee: preview labels are cosmetic — the gate at import is what enforces safety, so a
forged request can't smuggle a suppressed or malformed address into the lead table.
"""

import pytest

from craftsman.core.models import Lead, SuppressionEntry
from craftsman.ingest.gate import LeadRow, ingest_leads
from craftsman.ingest.sourcing import SourceQuery, WebhookSourceProvider
from craftsman.research.fetch import UnsafeURL
from sqlalchemy import select


def test_gate_drops_suppressed_dup_and_malformed_from_a_source(db):
    # Predicted: suppressed + duplicate + malformed all fall out; only the clean, new
    # address is created. (Apollo's credit-locked placeholder is valid syntax and is
    # dropped upstream by the provider, not here — see test_sourcing_providers.py.)
    db.add(SuppressionEntry(email="stop@x.com", reason="gdpr"))
    db.add(Lead(email="dup@x.com", company_id=None))
    db.flush()
    rows = [
        LeadRow(email="stop@x.com"),  # suppressed
        LeadRow(email="dup@x.com"),  # duplicate
        LeadRow(email="not-an-email"),  # malformed
        LeadRow(email="good@x.com"),  # the only real one
    ]
    result, new_ids = ingest_leads(db, rows, source="apollo")
    assert result.imported == 1 and len(new_ids) == 1
    assert result.suppressed == 1 and result.deduped == 1
    assert result.errors == ["bad email syntax: not-an-email"]
    assert db.scalar(select(Lead).where(Lead.email == "stop@x.com")) is None


def test_import_endpoint_rejects_forged_suppressed_address(client, db, make_key):
    # Predicted: even though the client hand-crafts an import request (bypassing preview),
    # the gate re-checks suppression server-side and refuses it.
    db.add(SuppressionEntry(email="stop@x.com", reason="gdpr"))
    db.flush()
    h = {"Authorization": f"Bearer {make_key('operate')}"}
    resp = client.post(
        "/leads/source/import",
        headers=h,
        json={"source": "apollo", "leads": [{"email": "stop@x.com"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"imported": 0, "deduped": 0, "suppressed": 1, "errors": []}
    assert db.scalar(select(Lead).where(Lead.email == "stop@x.com")) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/leads",
        "https://localhost:6379/leads",
        "http://feed.example.com/leads",  # not https
        "https://169.254.169.254/leads",  # cloud metadata
    ],
)
async def test_webhook_source_blocks_ssrf(url, monkeypatch):
    # Predicted: validate_url raises UnsafeURL before any GET — the provider never fetches
    # a private/loopback/metadata host or a non-https scheme. Patch DNS so hostnames that
    # would resolve publicly still resolve to the loopback we're asserting against.
    from craftsman.research import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_resolve_ips", lambda host: ["127.0.0.1"])
    provider = WebhookSourceProvider(url)
    with pytest.raises(UnsafeURL):
        await provider._fetch(SourceQuery(limit=5))


async def test_webhook_public_url_passes_guard_then_fetches(monkeypatch):
    # Control: a genuinely public URL clears the guard (so the SSRF test isn't just
    # rejecting everything). We stop at the network call by asserting validate_url passed.
    from craftsman.research import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_resolve_ips", lambda host: ["93.184.216.34"])
    reached = {}

    async def fake_get(url, headers):  # never really hit the network
        reached["url"] = url

        class R:
            headers = {"content-type": "application/json"}
            content = b"[]"

            def raise_for_status(self):
                pass

        return R()

    import httpx

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, headers=None):
            return await fake_get(url, headers)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    provider = WebhookSourceProvider("https://feed.example.com/leads")
    ct, body = await provider._fetch(SourceQuery(icp_query="ops", limit=5))
    assert "feed.example.com" in reached["url"] and body == b"[]"
