"""Shared import gate (M2.2): the one path CSV and sourcing both take. These tests are
the regression guard that extracting the gate from `import_csv` changed no behavior."""

from sqlalchemy import select

from craftsman.core.models import Company, Lead, SuppressionEntry
from craftsman.ingest.csv_import import import_csv
from craftsman.ingest.gate import LeadRow, classify_row, ingest_leads

CSV = b"""email,first_name,last_name,title,company,domain
dana@acme.com,Dana,Lopez,VP Operations,Acme Robotics,acme.com
raj@bcorp.io,Raj,Patel,Head of Warehouse,BCorp,bcorp.io
dana@acme.com,Dana,Lopez,VP Operations,Acme Robotics,acme.com
bad-email,Nope,Nope,None,None,none.com
blocked@spam.com,Blocked,Person,CEO,Spam Inc,spam.com
"""


# ---------------------------------------------------------------- pure predicate


def test_classify_row_precedence():
    seen, existing, suppressed = set(), {"dup@x.com"}, {"stop@x.com"}
    assert classify_row("bad-email", seen, existing, suppressed) == "invalid"
    assert classify_row("stop@x.com", seen, existing, suppressed) == "suppressed"
    assert classify_row("dup@x.com", seen, existing, suppressed) == "duplicate"
    assert classify_row("fresh@x.com", seen, existing, suppressed) == "new"
    # suppression beats dedupe: an address both known AND suppressed reads suppressed
    assert classify_row("stop@x.com", set(), {"stop@x.com"}, {"stop@x.com"}) == "suppressed"


# ---------------------------------------------------------------- CSV behavior unchanged


def test_csv_import_unchanged(db):
    db.add(SuppressionEntry(email="blocked@spam.com", reason="manual"))
    db.flush()
    result = import_csv(db, CSV)
    assert (result.imported, result.deduped, result.suppressed) == (2, 1, 1)
    assert len(result.errors) == 1
    lead = db.scalar(select(Lead).where(Lead.email == "dana@acme.com"))
    assert lead is not None and lead.company.domain == "acme.com"


def test_no_email_column_is_a_clean_error(db):
    result = import_csv(db, b"name,company\nDana,Acme\n")
    assert result.imported == 0 and result.errors == ["no 'email' column found"]


# ---------------------------------------------------------------- gate directly


def test_ingest_leads_stamps_source_and_returns_ids(db):
    rows = [LeadRow(email="new@acme.io", title="VP", company_domain="acme.io")]
    result, new_ids = ingest_leads(db, rows, source="apollo")
    assert result.imported == 1 and len(new_ids) == 1
    lead = db.get(Lead, new_ids[0])
    assert lead.source == "apollo" and lead.company.domain == "acme.io"


def test_ingest_leads_in_batch_and_existing_dedupe(db):
    db.add(Lead(email="known@x.com", company_id=None))
    db.flush()
    rows = [
        LeadRow(email="known@x.com"),  # vs existing
        LeadRow(email="fresh@x.com"),
        LeadRow(email="fresh@x.com"),  # in-batch dup
    ]
    result, new_ids = ingest_leads(db, rows, source="webhook")
    assert result.imported == 1 and result.deduped == 2 and len(new_ids) == 1


def test_ingest_leads_suppression_and_syntax(db):
    db.add(SuppressionEntry(email="stop@x.com", reason="gdpr"))
    db.flush()
    rows = [
        LeadRow(email="stop@x.com"),
        LeadRow(email="not-an-email"),
        LeadRow(email="good@x.com"),
    ]
    result, _ = ingest_leads(db, rows, source="apollo")
    assert result.imported == 1 and result.suppressed == 1
    assert result.errors == ["bad email syntax: not-an-email"]


def test_company_get_or_create_by_domain(db):
    existing = Company(domain="shared.com", name="Existing")
    db.add(existing)
    db.flush()
    rows = [
        LeadRow(email="a@shared.com", company_domain="shared.com"),
        LeadRow(email="b@shared.com"),  # domain derived from email
    ]
    ingest_leads(db, rows, source="apollo")
    companies = db.scalars(select(Company).where(Company.domain == "shared.com")).all()
    assert len(companies) == 1  # reused, not duplicated
