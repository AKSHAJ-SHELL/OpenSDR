"""Lead sourcing connectors (M2.2): turn an ICP query into candidate people from
*your own* provider account. Results flow through the shared import gate (`gate.py`) —
sourced leads get zero shortcuts. No proprietary database; providers are labeled.

Every provider exposes one `_fetch` seam (the only network call) so tests drive them
with recorded fixtures and no network.
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from craftsman.ingest.gate import LeadRow

log = logging.getLogger(__name__)

# A hostile webhook feed must not OOM the worker: cap what we'll read/parse.
MAX_WEBHOOK_BYTES = 5_000_000
MAX_CANDIDATES = 50


@dataclass(frozen=True)
class SourceQuery:
    icp_query: str = ""
    titles: list[str] = field(default_factory=list)
    seniorities: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    company_domains: list[str] = field(default_factory=list)
    employee_ranges: list[str] = field(default_factory=list)
    limit: int = 25


class LeadSourceProvider(Protocol):
    name: str

    async def search(self, query: SourceQuery) -> list[LeadRow]: ...


def _str(v) -> str | None:
    return str(v).strip() or None if v is not None and str(v).strip() else None


# Apollo returns a *syntactically valid* placeholder when an email is locked behind
# credits — the gate can't tell it from a real address, so the provider drops it here.
# We never fabricate an address and never silently spend unlock credits.
APOLLO_LOCKED_EMAILS = {"email_not_unlocked@domain.com"}
APOLLO_UNUSABLE_STATUSES = {"locked", "unavailable"}


def _apollo_usable_email(person: dict) -> str | None:
    email = person.get("email")
    if not email or email in APOLLO_LOCKED_EMAILS:
        return None
    if (person.get("email_status") or "").lower() in APOLLO_UNUSABLE_STATUSES:
        return None
    return email


class ApolloSourceProvider:
    """Apollo people search (`mixed_people/search`). BYO key. People whose email is
    locked behind credits (Apollo returns the placeholder `email_not_unlocked@domain.com`,
    which is *valid syntax* so the gate can't catch it) are dropped here — not imported,
    not faked. We never silently spend unlock credits."""

    name = "apollo"
    BASE = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _fetch(self, query: SourceQuery) -> dict:
        """The only network call — tests patch this."""
        payload = {
            "page": 1,
            "per_page": min(query.limit, MAX_CANDIDATES),
            "person_titles": query.titles,
            "person_seniorities": query.seniorities,
            "person_locations": query.locations,
            "q_organization_domains": "\n".join(query.company_domains) or None,
            "organization_num_employees_ranges": query.employee_ranges,
            "q_keywords": " ".join(filter(None, [query.icp_query, *query.industries])) or None,
        }
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{self.BASE}/mixed_people/search",
                headers={"X-Api-Key": self.api_key},
                json={k: v for k, v in payload.items() if v},
            )
            resp.raise_for_status()
            return resp.json()

    async def search(self, query: SourceQuery) -> list[LeadRow]:
        data = await self._fetch(query)
        rows = []
        for p in (data.get("people") or [])[:MAX_CANDIDATES]:
            org = p.get("organization") or {}
            email = _apollo_usable_email(p)
            if not email:
                continue  # no usable email (missing or credit-locked) → skip honestly
            rows.append(
                LeadRow(
                    email=email,
                    first_name=_str(p.get("first_name")),
                    last_name=_str(p.get("last_name")),
                    title=_str(p.get("title")),
                    company_name=_str(org.get("name")),
                    company_domain=_str(org.get("primary_domain")),
                    linkedin_url=_str(p.get("linkedin_url")),
                )
            )
        return rows


class WebhookSourceProvider:
    """Generic feed: GET a configured https URL (SSRF-guarded, reusing the M0.5 fetcher),
    parse a JSON array of lead objects or a text/csv body → LeadRows. For operators whose
    lead source is their own system. Filters are passed as query params; the feed decides
    what to do with them."""

    name = "webhook"

    def __init__(self, url: str):
        self.url = url

    async def _fetch(self, query: SourceQuery) -> tuple[str, bytes]:
        """The only network call — SSRF-validated per request. Returns (content_type, body).
        Tests patch this."""
        from urllib.parse import urlencode, urlparse

        from craftsman.research.fetch import validate_url

        params = {
            "q": query.icp_query,
            "titles": ",".join(query.titles),
            "seniorities": ",".join(query.seniorities),
            "industries": ",".join(query.industries),
            "locations": ",".join(query.locations),
            "limit": str(query.limit),
        }
        sep = "&" if urlparse(self.url).query else "?"
        url = f"{self.url}{sep}{urlencode({k: v for k, v in params.items() if v})}"
        validate_url(url)  # https-only, no private IPs, no localhost:port — every request
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            body = resp.content[:MAX_WEBHOOK_BYTES]
            return resp.headers.get("content-type", ""), body

    async def search(self, query: SourceQuery) -> list[LeadRow]:
        content_type, body = await self._fetch(query)
        records: list[dict]
        if "csv" in content_type.lower():
            reader = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
            records = list(reader)
        else:
            import json

            parsed = json.loads(body.decode("utf-8", "replace"))
            records = parsed.get("leads", parsed) if isinstance(parsed, dict) else parsed
        rows = []
        for r in (records or [])[:MAX_CANDIDATES]:
            email = r.get("email") or r.get("email_address")
            if not email:
                continue
            rows.append(
                LeadRow(
                    email=str(email),
                    first_name=_str(r.get("first_name")),
                    last_name=_str(r.get("last_name")),
                    title=_str(r.get("title")),
                    company_name=_str(r.get("company_name") or r.get("company")),
                    company_domain=_str(r.get("company_domain") or r.get("domain")),
                    linkedin_url=_str(r.get("linkedin_url") or r.get("linkedin")),
                )
            )
        return rows


class NullSourceProvider:
    name = "null"

    async def search(self, query: SourceQuery) -> list[LeadRow]:
        return []


def build_source_provider(settings, name: str) -> LeadSourceProvider | None:
    """Return the named provider only if it's enabled in `lead_source_providers` AND its
    key/URL is present; else None (the caller returns a clear 'not configured' error)."""
    enabled = [n.strip().lower() for n in settings.lead_source_providers.split(",") if n.strip()]
    name = name.strip().lower()
    if name not in enabled:
        return None
    if name == "apollo" and settings.apollo_api_key:
        return ApolloSourceProvider(settings.apollo_api_key)
    if name == "webhook" and settings.lead_source_webhook_url:
        return WebhookSourceProvider(settings.lead_source_webhook_url)
    if name == "null":
        return NullSourceProvider()
    return None


def enabled_providers(settings) -> list[str]:
    """The provider names the dashboard should offer (enabled + configured)."""
    return [
        n.strip().lower()
        for n in settings.lead_source_providers.split(",")
        if n.strip() and build_source_provider(settings, n) is not None
    ]
