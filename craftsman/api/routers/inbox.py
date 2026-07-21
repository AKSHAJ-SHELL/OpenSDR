import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import Company, Enrollment, Lead, Message, ReviewQueueItem
from craftsman.core.schemas import MessageOut, ReplyClassification
from craftsman.inbox.pipeline import apply_classification

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _enrich(db: Session, msg: Message) -> MessageOut:
    lead_email = lead_name = company_domain = None
    if msg.enrollment_id:
        row = db.execute(
            select(Lead, Company)
            .join(Enrollment, Enrollment.lead_id == Lead.id)
            .outerjoin(Company, Lead.company_id == Company.id)
            .where(Enrollment.id == msg.enrollment_id)
        ).first()
        if row:
            lead, company = row
            lead_email = lead.email
            parts = [p for p in (lead.first_name, lead.last_name) if p]
            lead_name = " ".join(parts) or None
            company_domain = company.domain if company else None
    return MessageOut(
        id=msg.id,
        direction=msg.direction,
        subject=msg.subject,
        body=msg.body,
        classification=msg.classification,
        classification_confidence=msg.classification_confidence,
        sent_at=msg.sent_at,
        lead_email=lead_email,
        lead_name=lead_name,
        company_domain=company_domain,
    )


@router.get("", response_model=list[MessageOut], dependencies=[Depends(require_scope("read"))])
def unified_inbox(
    label: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    stmt = (
        select(Message)
        .where(Message.direction == "inbound")
        .order_by(Message.id.desc())
        .limit(limit)
    )
    if label is not None:
        stmt = stmt.where(Message.classification == label)
    return [_enrich(db, m) for m in db.scalars(stmt).all()]


@router.get("/review", response_model=list[dict], dependencies=[Depends(require_scope("read"))])
def review_queue(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ReviewQueueItem)
        .where(ReviewQueueItem.resolved.is_(False))
        .order_by(ReviewQueueItem.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "payload": r.payload,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


class Reclassify(BaseModel):
    label: str


@router.post(
    "/{msg_id}/reclassify",
    response_model=MessageOut,
    dependencies=[Depends(require_scope("operate"))],
)
def reclassify(msg_id: uuid.UUID, payload: Reclassify, db: Session = Depends(get_db)):
    """Human override from the review queue / dashboard."""
    msg = db.get(Message, msg_id)
    if msg is None or msg.direction != "inbound":
        raise HTTPException(404, "inbound message not found")

    classification = ReplyClassification(label=payload.label, confidence=1.0)
    msg.classification = payload.label
    msg.classification_confidence = 1.0
    db.add(msg)

    # re-apply downstream effects with full confidence
    outbound = db.scalar(
        select(Message)
        .where(
            Message.enrollment_id == msg.enrollment_id,
            Message.direction == "outbound",
        )
        .order_by(Message.sent_at.desc())
        .limit(1)
    )
    enrollment = db.get(Enrollment, msg.enrollment_id) if msg.enrollment_id else None
    if outbound is not None:
        apply_classification(db, enrollment, outbound, classification)
    return _enrich(db, msg)
