"""Enrichment adapters. One interface: enrich(email|domain) -> LeadEnrichment."""

from typing import Protocol

import httpx

from craftsman.core.schemas import LeadEnrichment


class EnrichmentAdapter(Protocol):
    async def enrich(self, *, email: str | None = None, domain: str | None = None) -> LeadEnrichment: ...


class ApolloAdapter:
    BASE = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def enrich(self, *, email: str | None = None, domain: str | None = None) -> LeadEnrichment:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.BASE}/people/match",
                headers={"X-Api-Key": self.api_key},
                json={"email": email, "domain": domain},
            )
            resp.raise_for_status()
            p = resp.json().get("person") or {}
            org = p.get("organization") or {}
            return LeadEnrichment(
                email=p.get("email") or email,
                first_name=p.get("first_name"),
                last_name=p.get("last_name"),
                title=p.get("title"),
                linkedin_url=p.get("linkedin_url"),
                company_name=org.get("name"),
                company_domain=org.get("primary_domain") or domain,
                company_description=org.get("short_description"),
            )


class HunterAdapter:
    BASE = "https://api.hunter.io/v2"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def enrich(self, *, email: str | None = None, domain: str | None = None) -> LeadEnrichment:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self.BASE}/combined/find",
                params={"email": email, "api_key": self.api_key},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            person, company = data.get("person") or {}, data.get("company") or {}
            return LeadEnrichment(
                email=email,
                first_name=(person.get("name") or {}).get("givenName"),
                last_name=(person.get("name") or {}).get("familyName"),
                title=(person.get("employment") or {}).get("title"),
                linkedin_url=(person.get("linkedin") or {}).get("handle"),
                company_name=company.get("name"),
                company_domain=company.get("domain") or domain,
                company_description=company.get("description"),
            )
