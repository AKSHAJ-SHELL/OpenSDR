"""CSV lead import: parse → dedupe vs leads + suppression → persist."""

import io

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Company, Lead, SuppressionEntry
from craftsman.core.schemas import ImportResult
from craftsman.ingest.verify import domain_of, syntax_ok

# accepted column aliases → canonical
COLUMN_ALIASES = {
    "email": "email",
    "email_address": "email",
    "first_name": "first_name",
    "firstname": "first_name",
    "first": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "last": "last_name",
    "title": "title",
    "job_title": "title",
    "company": "company_name",
    "company_name": "company_name",
    "company_domain": "company_domain",
    "domain": "company_domain",
    "linkedin": "linkedin_url",
    "linkedin_url": "linkedin_url",
    "timezone": "timezone",
}


def import_csv(db: Session, raw: bytes) -> ImportResult:
    df = pd.read_csv(io.BytesIO(raw), dtype=str).fillna("")
    df.columns = [COLUMN_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in df.columns]
    if "email" not in df.columns:
        return ImportResult(imported=0, deduped=0, suppressed=0, errors=["no 'email' column found"])

    emails = [e.strip().lower() for e in df["email"].tolist()]
    existing = set(
        db.scalars(select(Lead.email).where(Lead.email.in_(emails))).all()
    )
    suppressed = set(
        db.scalars(
            select(SuppressionEntry.email).where(SuppressionEntry.email.in_(emails))
        ).all()
    )

    result = ImportResult(imported=0, deduped=0, suppressed=0)
    seen: set[str] = set()
    company_cache: dict[str, Company] = {}

    for _, row in df.iterrows():
        email = str(row.get("email", "")).strip().lower()
        if not syntax_ok(email):
            if email:
                result.errors.append(f"bad email syntax: {email}")
            continue
        if email in seen or email in existing:
            result.deduped += 1
            continue
        if email in suppressed:
            result.suppressed += 1
            continue
        seen.add(email)

        domain = str(row.get("company_domain", "")).strip().lower() or domain_of(email)
        company = company_cache.get(domain)
        if company is None:
            company = db.scalar(select(Company).where(Company.domain == domain))
            if company is None:
                company = Company(domain=domain, name=str(row.get("company_name", "")) or None)
                db.add(company)
                db.flush()
            company_cache[domain] = company

        db.add(
            Lead(
                email=email,
                company_id=company.id,
                first_name=str(row.get("first_name", "")) or None,
                last_name=str(row.get("last_name", "")) or None,
                title=str(row.get("title", "")) or None,
                linkedin_url=str(row.get("linkedin_url", "")) or None,
                timezone=str(row.get("timezone", "")) or "America/Los_Angeles",
                source="csv",
            )
        )
        result.imported += 1

    return result
