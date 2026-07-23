import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.compliance.suppression import erase_lead, suppress
from craftsman.core.models import Campaign, Lead, LeadEnrichmentRecord
from craftsman.core.schemas import ImportResult, LeadEnrichmentOut, LeadOut

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("/import", response_model=ImportResult, dependencies=[Depends(require_scope("operate"))])
async def import_leads(file: UploadFile, db: Session = Depends(get_db)):
    from craftsman.ingest.csv_import import import_csv

    raw = await file.read()
    result = import_csv(db, raw)
    db.flush()

    # enqueue verification for the new leads (best-effort if no broker in dev)
    try:
        from craftsman.workers.tasks import enrich_lead

        for lead in db.scalars(select(Lead).where(Lead.status == "new")).all():
            enrich_lead.delay(str(lead.id))
    except Exception:
        pass
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
