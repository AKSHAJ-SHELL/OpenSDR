import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.compliance.suppression import erase_lead, suppress
from craftsman.core.config import get_settings
from craftsman.core.models import Campaign, Lead, LeadEnrichmentRecord, Signal
from craftsman.core.schemas import (
    ImportResult,
    LeadEnrichmentOut,
    LeadOut,
    ScoringWeights,
    SignalOut,
    SourcedCandidate,
    SourcedPreview,
    SourceImportRequest,
    SourceSearchRequest,
)

router = APIRouter(prefix="/leads", tags=["leads"])


def _enqueue_enrich(new_ids: list) -> None:
    """Verify+enrich exactly the leads created by this import (best-effort if no broker)."""
    try:
        from craftsman.workers.tasks import enrich_lead

        for lead_id in new_ids:
            enrich_lead.delay(str(lead_id))
    except Exception:
        pass


@router.post("/import", response_model=ImportResult, dependencies=[Depends(require_scope("operate"))])
async def import_leads(file: UploadFile, db: Session = Depends(get_db)):
    from craftsman.ingest.csv_import import rows_from_csv
    from craftsman.ingest.gate import ingest_leads

    raw = await file.read()
    try:
        rows = rows_from_csv(raw)
    except ValueError as e:
        return ImportResult(imported=0, deduped=0, suppressed=0, errors=[str(e)])
    result, new_ids = ingest_leads(db, rows, source="csv")
    db.flush()
    _enqueue_enrich(new_ids)
    return result


@router.get(
    "/source/providers",
    response_model=list[str],
    dependencies=[Depends(require_scope("read"))],
)
def list_source_providers():
    """The configured (enabled + keyed) lead sources. Empty ⇒ sourcing is off; the
    dashboard shows a configure-me state instead of fake data."""
    from craftsman.ingest.sourcing import enabled_providers

    return enabled_providers(get_settings())


@router.post(
    "/source", response_model=SourcedPreview, dependencies=[Depends(require_scope("operate"))]
)
async def source_leads(req: SourceSearchRequest, db: Session = Depends(get_db)):
    """Search a provider and preview candidates with per-row gate labels. Read-only —
    nothing is imported here (the operator picks what to import via /leads/source/import)."""
    from craftsman.ingest.gate import _load_dedupe_sets, classify_row
    from craftsman.ingest.sourcing import SourceQuery, build_source_provider

    provider = build_source_provider(get_settings(), req.provider)
    if provider is None:
        raise HTTPException(400, f"lead source {req.provider!r} not configured")

    query = SourceQuery(
        icp_query=req.icp_query,
        titles=req.filters.titles,
        seniorities=req.filters.seniorities,
        industries=req.filters.industries,
        locations=req.filters.locations,
        company_domains=req.filters.company_domains,
        employee_ranges=req.filters.employee_ranges,
        limit=req.limit,
    )
    try:
        rows = await provider.search(query)
    except Exception as e:  # noqa: BLE001 — surface the provider's message, never a 500
        raise HTTPException(502, f"{req.provider} search failed: {e}") from e

    emails = [r.normalized_email() for r in rows]
    existing, suppressed = _load_dedupe_sets(db, [e for e in emails if e])
    seen: set[str] = set()
    candidates, counts = [], {"new": 0, "duplicate": 0, "suppressed": 0, "invalid": 0}
    for row in rows:
        email = row.normalized_email()
        status = classify_row(email, seen, existing, suppressed)
        if status == "new":
            seen.add(email)  # so a provider returning the same person twice reads as dup
        counts[status] += 1
        candidates.append(
            SourcedCandidate(
                email=row.email,
                first_name=row.first_name,
                last_name=row.last_name,
                title=row.title,
                company_name=row.company_name,
                company_domain=row.company_domain,
                linkedin_url=row.linkedin_url,
                status=status,
            )
        )
    return SourcedPreview(provider=req.provider, candidates=candidates, **counts)


@router.post(
    "/source/import",
    response_model=ImportResult,
    dependencies=[Depends(require_scope("operate"))],
)
def import_sourced(req: SourceImportRequest, db: Session = Depends(get_db)):
    """Persist selected candidates through the SAME gate a CSV faces. The client is
    untrusted, so every check re-runs here — a forged suppressed/invalid row is rejected."""
    from craftsman.ingest.gate import LeadRow, ingest_leads

    rows = [
        LeadRow(
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            title=lead.title,
            company_name=lead.company_name,
            company_domain=lead.company_domain,
            linkedin_url=lead.linkedin_url,
        )
        for lead in req.leads
    ]
    result, new_ids = ingest_leads(db, rows, source=req.source or "sourced")
    db.flush()
    _enqueue_enrich(new_ids)
    return result


@router.get("", response_model=list[LeadOut], dependencies=[Depends(require_scope("read"))])
def list_leads(
    score_gte: float | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    from craftsman.scoring.icp import matched_seniority_keyword

    stmt = (
        select(Lead, Campaign.name)
        .outerjoin(Campaign, Lead.icp_scored_campaign_id == Campaign.id)
        .limit(limit)
    )
    if score_gte is not None:
        stmt = stmt.where(Lead.icp_score >= score_gte)
    if status is not None:
        stmt = stmt.where(Lead.status == status)

    out = []
    for lead, campaign_name in db.execute(stmt).all():
        item = LeadOut.model_validate(lead)
        item.icp_scored_campaign_name = campaign_name
        item.icp_matched_keyword = matched_seniority_keyword(lead.title)
        out.append(item)
    return out


@router.get(
    "/scoring-weights", response_model=ScoringWeights, dependencies=[Depends(require_scope("read"))]
)
def scoring_weights():
    """Active ICP-score weights so the dashboard explains a score truthfully — no-signal
    leads use cosine/rule; signal leads use the 3-way split (M2.3)."""
    s = get_settings()
    return ScoringWeights(
        cosine=s.icp_cosine_weight,
        rule=s.icp_rule_weight,
        signal_cosine=s.icp_signal_cosine_weight,
        signal_rule=s.icp_signal_rule_weight,
        signal=s.icp_signal_weight,
    )


@router.get(
    "/{lead_id}/signals",
    response_model=list[SignalOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_lead_signals(lead_id: uuid.UUID, db: Session = Depends(get_db)):
    """Intent signals for the lead's company — the observations behind its signal boost."""
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    if lead.company_id is None:
        return []
    return db.scalars(
        select(Signal)
        .where(Signal.company_id == lead.company_id)
        .order_by(Signal.observed_at.desc())
    ).all()


@router.get(
    "/{lead_id}/enrichments",
    response_model=list[LeadEnrichmentOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_enrichments(lead_id: uuid.UUID, db: Session = Depends(get_db)):
    """Provenance for every enriched field: who said what, and when (M2.1).

    Rows exist even when the canonical column kept operator-supplied data —
    disagreement between your CSV and your provider stays inspectable."""
    if db.get(Lead, lead_id) is None:
        raise HTTPException(404, "lead not found")
    return db.scalars(
        select(LeadEnrichmentRecord)
        .where(LeadEnrichmentRecord.lead_id == lead_id)
        .order_by(LeadEnrichmentRecord.fetched_at.desc(), LeadEnrichmentRecord.field)
    ).all()


@router.post("/{lead_id}/suppress", status_code=204, dependencies=[Depends(require_scope("operate"))])
def suppress_lead(lead_id: uuid.UUID, db: Session = Depends(get_db)):
    """Manual do-not-contact. Keeps the row (unlike erase) and is idempotent —
    suppression is checked at generation AND send time, so this stops mail either way."""
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    suppress(db, lead.email, reason="manual")


@router.delete("/{lead_id}/erase", status_code=204, dependencies=[Depends(require_scope("admin"))])
def erase(lead_id: uuid.UUID, db: Session = Depends(get_db)):
    """GDPR data-subject erasure: hard delete + permanent suppression."""
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    erase_lead(db, lead)
