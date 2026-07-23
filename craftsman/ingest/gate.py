"""The one import gate (M2.2): syntax → dedupe → suppression → persist.

Extracted from `csv_import.py` so CSV upload and provider sourcing take the *identical*
path — a sourced lead gets zero shortcuts past the same checks a CSV row faces. The
classification predicate is pure (no writes), so preview and commit stay consistent.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Company, Lead, SuppressionEntry
from craftsman.core.schemas import ImportResult
from craftsman.ingest.verify import domain_of, syntax_ok

RowStatus = Literal["new", "duplicate", "suppressed", "invalid"]


@dataclass
class LeadRow:
    """Normalized, source-agnostic candidate. What every source produces and the gate
    consumes — CSV columns, Apollo results, and webhook feeds all reduce to this."""

    email: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None

    def normalized_email(self) -> str:
        return (self.email or "").strip().lower()


def _load_dedupe_sets(db: Session, emails: list[str]) -> tuple[set[str], set[str]]:
    """One batched read of existing leads + suppression for a candidate batch."""
    existing = set(db.scalars(select(Lead.email).where(Lead.email.in_(emails))).all())
    suppressed = set(
        db.scalars(
            select(SuppressionEntry.email).where(SuppressionEntry.email.in_(emails))
        ).all()
    )
    return existing, suppressed


def classify_row(
    email: str, seen: set[str], existing: set[str], suppressed: set[str]
) -> RowStatus:
    """Pure predicate — the same verdict preview shows and commit enforces. Order
    matters: syntax first (a placeholder/locked email is `invalid`, never imported),
    then suppression (do-not-contact wins over dedupe), then dedupe."""
    if not syntax_ok(email):
        return "invalid"
    if email in suppressed:
        return "suppressed"
    if email in seen or email in existing:
        return "duplicate"
    return "new"


def ingest_leads(
    db: Session, rows: list[LeadRow], source: str
) -> tuple[ImportResult, list]:
    """Run rows through the gate and persist the `new` ones. Returns the tally plus
    the ids of leads created here (so the caller enqueues verify/enrich for exactly
    this batch — not every `new` lead in the table)."""
    emails = [r.normalized_email() for r in rows]
    existing, suppressed = _load_dedupe_sets(db, [e for e in emails if e])

    result = ImportResult(imported=0, deduped=0, suppressed=0)
    seen: set[str] = set()
    company_cache: dict[str, Company] = {}
    new_ids: list = []

    for row in rows:
        email = row.normalized_email()
        status = classify_row(email, seen, existing, suppressed)
        if status == "invalid":
            if email:
                result.errors.append(f"bad email syntax: {email}")
            continue
        if status == "suppressed":
            result.suppressed += 1
            continue
        if status == "duplicate":
            result.deduped += 1
            continue

        seen.add(email)
        domain = (row.company_domain or "").strip().lower() or domain_of(email)
        company = company_cache.get(domain)
        if company is None:
            company = db.scalar(select(Company).where(Company.domain == domain))
            if company is None:
                company = Company(domain=domain, name=(row.company_name or None))
                db.add(company)
                db.flush()
            company_cache[domain] = company

        lead = Lead(
            email=email,
            company_id=company.id,
            first_name=row.first_name or None,
            last_name=row.last_name or None,
            title=row.title or None,
            linkedin_url=row.linkedin_url or None,
            timezone=(row.timezone or "America/Los_Angeles"),
            source=source,
        )
        db.add(lead)
        db.flush()
        new_ids.append(lead.id)
        result.imported += 1

    return result, new_ids
