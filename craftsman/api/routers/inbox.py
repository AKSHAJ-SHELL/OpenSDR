import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.models import Enrollment, Message
from craftsman.core.schemas import MessageOut, ReplyClassification
from craftsman.inbox.pipeline import apply_classification

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_model=list[MessageOut])
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
    return db.scalars(stmt).all()


class Reclassify(BaseModel):
    label: str


@router.post("/{msg_id}/reclassify", response_model=MessageOut)
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
    return msg
