import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.compliance.suppression import erase_lead
from craftsman.core.models import Lead
from craftsman.core.schemas import ImportResult, LeadOut

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
    stmt = select(Lead).limit(limit)
    if score_gte is not None:
        stmt = stmt.where(Lead.icp_score >= score_gte)
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    return db.scalars(stmt).all()


@router.delete("/{lead_id}/erase", status_code=204, dependencies=[Depends(require_scope("admin"))])
def erase(lead_id: uuid.UUID, db: Session = Depends(get_db)):
    """GDPR data-subject erasure: hard delete + permanent suppression."""
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    erase_lead(db, lead)
