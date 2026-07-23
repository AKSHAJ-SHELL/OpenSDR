"""Provider contract tests (M2.1): recorded fixtures through the real parsers.

The `_fetch` seam is the only network call each provider makes; these tests patch it
with recorded response shapes and assert the canonical-field mapping. No network.
"""

from craftsman.ingest.enrichment import (
    ApolloProvider,
    EnrichmentInput,
    HunterProvider,
    NullProvider,
)

INP = EnrichmentInput(email="dana@acme.com", company_domain="acme.com")

# Recorded shape of POST /api/v1/people/match (fields Craftsman reads)
APOLLO_FIXTURE = {
    "person": {
        "first_name": "Dana",
        "last_name": "Reed",
        "title": "VP of Operations",
        "seniority": "vp",
        "linkedin_url": "https://linkedin.com/in/danareed",
        "sanitized_phone": "+14155550123",
        "organization": {
            "name": "Acme Corp",
            "primary_domain": "acme.com",
            "industry": "logistics",
            "estimated_num_employees": 250,
            "short_description": "Acme ships warehouse robotics.",
        },
    }
}

# Recorded shape of GET /v2/combined/find (fields Craftsman reads)
HUNTER_FIXTURE = {
    "data": {
        "person": {
            "name": {"givenName": "Dana", "familyName": "Reed"},
            "employment": {"title": "VP Ops", "seniority": "executive"},
            "linkedin": {"handle": "danareed"},
            "phone_number": "+14155550199",
        },
        "company": {
            "name": "Acme Corporation",
            "domain": "acme.com",
            "description": "Warehouse robotics.",
            "category": {"industry": "Logistics"},
            "metrics": {"employees": "201-500"},
        },
    }
}


def _patched(provider, monkeypatch, payload):
    async def fake_fetch(inp):
        return payload

    monkeypatch.setattr(provider, "_fetch", fake_fetch)
    return provider


async def test_apollo_maps_canonical_fields(monkeypatch):
    p = _patched(ApolloProvider("key"), monkeypatch, APOLLO_FIXTURE)
    result = await p.enrich(INP)
    assert result.source == "apollo" and result.confidence == 0.9
    assert result.fields == {
        "first_name": "Dana",
        "last_name": "Reed",
        "title": "VP of Operations",
        "seniority": "vp",
        "phone": "+14155550123",
        "linkedin_url": "https://linkedin.com/in/danareed",
        "company_name": "Acme Corp",
        "company_domain": "acme.com",
        "company_industry": "logistics",
        "company_size": "250",  # ints normalize to strings
        "company_description": "Acme ships warehouse robotics.",
    }


async def test_hunter_maps_canonical_fields(monkeypatch):
    p = _patched(HunterProvider("key"), monkeypatch, HUNTER_FIXTURE)
    result = await p.enrich(INP)
    assert result.source == "hunter" and result.confidence == 0.85
    assert result.fields["title"] == "VP Ops"
    assert result.fields["seniority"] == "executive"
    assert result.fields["company_size"] == "201-500"  # ranges survive as text
    assert result.fields["company_industry"] == "Logistics"
    assert result.fields["phone"] == "+14155550199"


async def test_empty_and_missing_values_are_dropped(monkeypatch):
    payload = {
        "person": {
            "first_name": "Dana",
            "last_name": "",  # empty string → dropped
            "title": None,  # null → dropped
            "organization": {"name": "  "},  # whitespace-only → dropped
        }
    }
    p = _patched(ApolloProvider("key"), monkeypatch, payload)
    result = await p.enrich(INP)
    assert result.fields == {"first_name": "Dana"}


async def test_no_person_at_all_returns_none(monkeypatch):
    p = _patched(ApolloProvider("key"), monkeypatch, {"person": None})
    assert await p.enrich(INP) is None
    h = _patched(HunterProvider("key"), monkeypatch, {"data": {}})
    assert await h.enrich(INP) is None


async def test_null_provider_always_returns_none():
    assert await NullProvider().enrich(INP) is None
