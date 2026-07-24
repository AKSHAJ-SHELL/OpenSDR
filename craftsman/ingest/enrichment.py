"""Enrichment framework (M2.1): BYO-key providers chained with per-field precedence.

Promotes the orphaned adapters into a real pipeline stage. Two invariants:
- a dead/slow provider never blocks anything — every provider call is failure-isolated;
- enrichment never clobbers operator-supplied data — canonical columns are written
  only when currently empty, while every winning value is still recorded in the
  `lead_enrichments` provenance table (so you can always see what a provider said,
  even when the CSV already had an answer).

Results come from *your* provider accounts (bring your own keys). No provider keys
configured ⇒ the chain is empty and the pipeline is verify-only.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Company, Lead, LeadEnrichmentRecord

log = logging.getLogger(__name__)

# Canonical field keys → where they land. `company_domain` is special: it never
# overwrites, it only attaches a company to a lead that has none.
LEAD_FIELDS = ("first_name", "last_name", "title", "seniority", "phone", "linkedin_url")
COMPANY_FIELDS = {
    "company_name": "name",
    "company_industry": "industry",
    "company_size": "size",
    "company_description": "description",
}
CANONICAL_FIELDS = LEAD_FIELDS + tuple(COMPANY_FIELDS) + ("company_domain",)


@dataclass(frozen=True)
class EnrichmentInput:
    """Provider-facing view of a lead — providers never touch the ORM."""

    email: str
    company_domain: str | None = None


@dataclass(frozen=True)
class EnrichmentResult:
    source: str
    confidence: float
    fields: dict[str, str]  # only canonical keys with non-empty values


@dataclass(frozen=True)
class FieldProvenance:
    field: str
    value: str
    source: str
    confidence: float


class EnrichmentProvider(Protocol):
    name: str

    async def enrich(self, inp: EnrichmentInput) -> EnrichmentResult | None: ...


def _clean(fields: dict[str, object]) -> dict[str, str]:
    """Keep only canonical keys whose value is a non-empty string."""
    out = {}
    for k, v in fields.items():
        if k in CANONICAL_FIELDS and isinstance(v, (str, int)) and str(v).strip():
            out[k] = str(v).strip()
    return out


class ApolloProvider:
    """Apollo people-match (same endpoint the orphaned adapter called)."""

    name = "apollo"
    confidence = 0.9  # Apollo reports no per-field confidence; fixed, documented default
    BASE = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _fetch(self, inp: EnrichmentInput) -> dict:
        """The only network call — tests patch this with recorded fixtures."""
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.BASE}/people/match",
                headers={"X-Api-Key": self.api_key},
                json={"email": inp.email, "domain": inp.company_domain},
            )
            resp.raise_for_status()
            return resp.json()

    async def enrich(self, inp: EnrichmentInput) -> EnrichmentResult | None:
        data = await self._fetch(inp)
        p = data.get("person") or {}
        org = p.get("organization") or {}
        fields = _clean(
            {
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "title": p.get("title"),
                "seniority": p.get("seniority"),
                "phone": p.get("sanitized_phone"),
                "linkedin_url": p.get("linkedin_url"),
                "company_name": org.get("name"),
                "company_domain": org.get("primary_domain"),
                "company_industry": org.get("industry"),
                "company_size": org.get("estimated_num_employees"),
                "company_description": org.get("short_description"),
            }
        )
        return EnrichmentResult(self.name, self.confidence, fields) if fields else None


class HunterProvider:
    """Hunter combined-find (same endpoint the orphaned adapter called)."""

    name = "hunter"
    confidence = 0.85  # Hunter reports no per-field confidence; fixed, documented default
    BASE = "https://api.hunter.io/v2"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _fetch(self, inp: EnrichmentInput) -> dict:
        """The only network call — tests patch this with recorded fixtures."""
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self.BASE}/combined/find",
                params={"email": inp.email, "api_key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()

    async def enrich(self, inp: EnrichmentInput) -> EnrichmentResult | None:
        data = (await self._fetch(inp)).get("data") or {}
        person, company = data.get("person") or {}, data.get("company") or {}
        fields = _clean(
            {
                "first_name": (person.get("name") or {}).get("givenName"),
                "last_name": (person.get("name") or {}).get("familyName"),
                "title": (person.get("employment") or {}).get("title"),
                "seniority": (person.get("employment") or {}).get("seniority"),
                "phone": person.get("phone_number"),
                "linkedin_url": (person.get("linkedin") or {}).get("handle"),
                "company_name": company.get("name"),
                "company_domain": company.get("domain"),
                "company_industry": (company.get("category") or {}).get("industry"),
                "company_size": (company.get("metrics") or {}).get("employees"),
                "company_description": company.get("description"),
            }
        )
        return EnrichmentResult(self.name, self.confidence, fields) if fields else None


class NullProvider:
    """The honest 'enrichment disabled' provider: always returns nothing."""

    name = "null"

    async def enrich(self, inp: EnrichmentInput) -> EnrichmentResult | None:
        return None


# ------------------------------------------------------------------ the chain


async def chain_enrich(
    providers: list[EnrichmentProvider], inp: EnrichmentInput
) -> tuple[dict[str, str], list[FieldProvenance]]:
    """Run providers in precedence order; per-field first-writer-wins.

    Every provider call is failure-isolated: a raise/timeout/HTTP error/malformed
    body logs and skips that provider — it can never sink the chain.
    """
    merged: dict[str, str] = {}
    provenance: list[FieldProvenance] = []
    for provider in providers:
        try:
            result = await provider.enrich(inp)
        except Exception as e:  # noqa: BLE001 — one dead provider never blocks the rest
            log.warning("enrichment provider %s failed: %s", provider.name, e)
            continue
        if result is None:
            continue
        for key, value in result.fields.items():
            if key in merged:
                continue  # an earlier (higher-precedence) provider owns this field
            merged[key] = value
            provenance.append(FieldProvenance(key, value, result.source, result.confidence))
    return merged, provenance


def build_enrichment_chain(settings) -> list[EnrichmentProvider]:
    """Ordered providers from `enrichment_providers` config; keyless entries are
    skipped, unknown names logged and skipped. Empty ⇒ enrichment disabled."""
    chain: list[EnrichmentProvider] = []
    for name in [n.strip().lower() for n in settings.enrichment_providers.split(",") if n.strip()]:
        if name == "apollo" and settings.apollo_api_key:
            chain.append(ApolloProvider(settings.apollo_api_key))
        elif name == "hunter" and settings.hunter_api_key:
            chain.append(HunterProvider(settings.hunter_api_key))
        elif name == "null":
            chain.append(NullProvider())
        else:
            log.warning("enrichment provider %r skipped (unknown or no API key)", name)
    return chain


# ------------------------------------------------------------------ write-through


def apply_enrichment(
    db: Session,
    lead: Lead,
    merged: dict[str, str],
    provenance: list[FieldProvenance],
) -> None:
    """Record provenance for every winning field, then fill ONLY empty canonical
    columns. Operator-supplied data (CSV) is never overwritten — the provider's
    answer still lands in `lead_enrichments`, so disagreement stays visible."""
    now = datetime.now(timezone.utc)
    for p in provenance:
        db.add(
            LeadEnrichmentRecord(
                lead_id=lead.id,
                field=p.field,
                value=p.value,
                source=p.source,
                confidence=p.confidence,
                fetched_at=now,
            )
        )

    for key in LEAD_FIELDS:
        if key in merged and not getattr(lead, key):
            setattr(lead, key, merged[key])

    company = db.get(Company, lead.company_id) if lead.company_id else None
    if company is None and merged.get("company_domain"):
        domain = merged["company_domain"].lower()
        company = db.scalar(select(Company).where(Company.domain == domain))
        if company is None:
            company = Company(domain=domain)
            db.add(company)
            db.flush()
        lead.company_id = company.id
    if company is not None:
        for key, column in COMPANY_FIELDS.items():
            if key in merged and not getattr(company, column):
                setattr(company, column, merged[key])
        db.add(company)

    if not lead.source and provenance:
        lead.source = provenance[0].source
    db.add(lead)


def reserve_enrichment_calls(db, org_id, n: int) -> bool:
    """Atomically consume n provider calls from the org's daily enrichment
    budget (M5.1c). NULL budget = unlimited (self-hoster default). Refused ⇒
    the caller skips enrichment for now — verify-only, logged, never an error;
    the counter resets with the daily sweep."""
    from sqlalchemy import or_, update

    from craftsman.core.models import Org

    result = db.execute(
        update(Org)
        .where(
            Org.id == org_id,
            or_(
                Org.enrichment_daily_budget.is_(None),
                Org.enrichment_calls_today + n <= Org.enrichment_daily_budget,
            ),
        )
        .values(enrichment_calls_today=Org.enrichment_calls_today + n)
    )
    return result.rowcount == 1
