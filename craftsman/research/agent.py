"""Research Agent: per-company, cached 30 days, grounded-by-construction."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from craftsman.core.models import Company
from craftsman.core.schemas import ResearchBrief
from craftsman.llm.client import LLMClient
from craftsman.research.fetch import fetch_company_text
from craftsman.research.prompts import RESEARCH_SYSTEM, research_user_prompt

CACHE_DAYS = 30


def brief_is_fresh(company: Company) -> bool:
    if company.research_brief is None or company.research_fetched_at is None:
        return False
    fetched = company.research_fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < timedelta(days=CACHE_DAYS)


async def research_company(db: Session, company: Company, llm: LLMClient) -> ResearchBrief:
    """Fetch sources → one structured Sonnet call → cache on the company row."""
    if brief_is_fresh(company):
        return ResearchBrief.model_validate(company.research_brief)

    sources = await fetch_company_text(company.domain)
    if not sources:
        raise ResearchError(f"no fetchable sources for {company.domain}")

    brief = await llm.structured(
        system=RESEARCH_SYSTEM,
        user=research_user_prompt(company.domain, sources),
        schema=ResearchBrief,
        max_tokens=1500,
        temperature=0.2,
    )

    company.research_brief = brief.model_dump()
    company.research_fetched_at = datetime.now(timezone.utc)
    db.add(company)
    return brief


class ResearchError(Exception):
    pass
