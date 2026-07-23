"""CSV lead import: parse + column-normalize → the shared ingest gate.

The gate (dedupe vs leads + suppression, syntax, persist) lives in `gate.py` so CSV
upload and provider sourcing (M2.2) take the identical path. This module owns only the
CSV-specific concern: turning a spreadsheet into normalized `LeadRow`s.
"""

import io

import pandas as pd
from sqlalchemy.orm import Session

from craftsman.core.schemas import ImportResult
from craftsman.ingest.gate import LeadRow, ingest_leads

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


def rows_from_csv(raw: bytes) -> list[LeadRow]:
    """Parse + column-normalize a CSV into LeadRows. Raises ValueError if no email column."""
    df = pd.read_csv(io.BytesIO(raw), dtype=str).fillna("")
    df.columns = [COLUMN_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in df.columns]
    if "email" not in df.columns:
        raise ValueError("no 'email' column found")

    def _val(row, key: str) -> str | None:
        return str(row.get(key, "")).strip() or None

    rows = []
    for _, row in df.iterrows():
        rows.append(
            LeadRow(
                email=str(row.get("email", "")),
                first_name=_val(row, "first_name"),
                last_name=_val(row, "last_name"),
                title=_val(row, "title"),
                company_name=_val(row, "company_name"),
                company_domain=_val(row, "company_domain"),
                linkedin_url=_val(row, "linkedin_url"),
                timezone=_val(row, "timezone"),
            )
        )
    return rows


def import_csv(db: Session, raw: bytes) -> ImportResult:
    try:
        rows = rows_from_csv(raw)
    except ValueError as e:
        return ImportResult(imported=0, deduped=0, suppressed=0, errors=[str(e)])
    result, _ = ingest_leads(db, rows, source="csv")
    return result
