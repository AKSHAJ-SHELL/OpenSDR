"""Sourcing provider contract tests (M2.2): recorded fixtures through the real parsers,
`_fetch` patched, no network."""

import json

from craftsman.core.config import Settings
from craftsman.ingest.sourcing import (
    ApolloSourceProvider,
    NullSourceProvider,
    SourceQuery,
    WebhookSourceProvider,
    build_source_provider,
    enabled_providers,
)

Q = SourceQuery(icp_query="ops leaders", titles=["VP Operations"], limit=25)

# Recorded shape of POST /api/v1/mixed_people/search
APOLLO_FIXTURE = {
    "people": [
        {
            "first_name": "Dana",
            "last_name": "Reed",
            "title": "VP of Operations",
            "email": "dana@acme.com",
            "linkedin_url": "https://linkedin.com/in/danareed",
            "organization": {"name": "Acme Corp", "primary_domain": "acme.com"},
        },
        {
            "first_name": "Locked",
            "last_name": "Person",
            "title": "COO",
            # Apollo's placeholder for a credit-locked email — VALID syntax, so only the
            # provider (not the gate) can recognize and drop it.
            "email": "email_not_unlocked@domain.com",
            "organization": {"name": "BCorp", "primary_domain": "bcorp.io"},
        },
        {
            "first_name": "Status",
            "last_name": "Locked",
            "email": "real.looking@ccorp.com",
            "email_status": "locked",  # unusable per status flag → also dropped
            "organization": {"name": "CCorp"},
        },
        {
            "first_name": "NoEmail",
            "last_name": "Person",
            "email": None,  # dropped entirely — not actionable
            "organization": {"name": "DCorp"},
        },
    ]
}


def _patch(provider, monkeypatch, payload):
    async def fake_fetch(query):
        return payload

    monkeypatch.setattr(provider, "_fetch", fake_fetch)
    return provider


async def test_apollo_drops_unusable_emails(monkeypatch):
    p = _patch(ApolloSourceProvider("key"), monkeypatch, APOLLO_FIXTURE)
    rows = await p.search(Q)
    # 4 people in; only the one real, usable address survives. The credit-locked
    # placeholder (valid syntax!), the status-locked one, and the null-email one are all
    # dropped by the provider — a known-unusable address must never reach the gate.
    assert [r.email for r in rows] == ["dana@acme.com"]
    assert rows[0].company_domain == "acme.com"


async def test_webhook_parses_json_array(monkeypatch):
    body = json.dumps(
        [
            {"email": "a@x.com", "first_name": "A", "company": "X Inc", "domain": "x.com"},
            {"email_address": "b@y.com", "title": "CTO"},  # alias key
            {"name": "no email here"},  # dropped
        ]
    ).encode()
    p = WebhookSourceProvider("https://feed.example.com/leads")

    async def fake_fetch(query):
        return "application/json", body

    monkeypatch.setattr(p, "_fetch", fake_fetch)
    rows = await p.search(Q)
    assert [r.email for r in rows] == ["a@x.com", "b@y.com"]
    assert rows[0].company_name == "X Inc" and rows[0].company_domain == "x.com"


async def test_webhook_parses_json_object_with_leads_key(monkeypatch):
    body = json.dumps({"leads": [{"email": "a@x.com"}]}).encode()
    p = WebhookSourceProvider("https://feed.example.com")

    async def fake_fetch(query):
        return "application/json", body

    monkeypatch.setattr(p, "_fetch", fake_fetch)
    rows = await p.search(Q)
    assert [r.email for r in rows] == ["a@x.com"]


async def test_webhook_parses_csv_body(monkeypatch):
    body = b"email,first_name,title\nc@z.com,Cara,VP\n"
    p = WebhookSourceProvider("https://feed.example.com")

    async def fake_fetch(query):
        return "text/csv; charset=utf-8", body

    monkeypatch.setattr(p, "_fetch", fake_fetch)
    rows = await p.search(Q)
    assert rows[0].email == "c@z.com" and rows[0].title == "VP"


async def test_null_provider_returns_empty():
    assert await NullSourceProvider().search(Q) == []


# ---------------------------------------------------------------- factory gating


def _settings(**kw):
    return Settings(_env_file=None, **kw)


def test_factory_requires_enabled_and_configured():
    s = _settings(
        lead_source_providers="apollo,webhook",
        apollo_api_key="k",
        lead_source_webhook_url="https://feed.example.com",
    )
    assert isinstance(build_source_provider(s, "apollo"), ApolloSourceProvider)
    assert isinstance(build_source_provider(s, "webhook"), WebhookSourceProvider)
    assert set(enabled_providers(s)) == {"apollo", "webhook"}


def test_factory_skips_unlisted_or_unconfigured():
    # apollo listed but keyless; webhook not listed at all
    s = _settings(lead_source_providers="apollo", apollo_api_key="")
    assert build_source_provider(s, "apollo") is None
    assert build_source_provider(s, "webhook") is None
    assert enabled_providers(s) == []


def test_factory_empty_disables_sourcing():
    assert build_source_provider(_settings(), "apollo") is None
