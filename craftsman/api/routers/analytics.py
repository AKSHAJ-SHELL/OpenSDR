from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.models import Enrollment, Lead, Message, ReviewQueueItem

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    sent = db.scalar(
        select(func.count(Message.id)).where(Message.direction == "outbound")
    ) or 0
    replies = db.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "inbound",
            Message.classification.in_(["interested", "objection", "not_now"]),
        )
    ) or 0
    interested = db.scalar(
        select(func.count(Message.id)).where(Message.classification == "interested")
    ) or 0
    copy_rejections = db.scalar(
        select(func.count(ReviewQueueItem.id)).where(ReviewQueueItem.kind == "copywriter")
    ) or 0

    states = dict(
        db.execute(
            select(Enrollment.state, func.count(Enrollment.id)).group_by(Enrollment.state)
        ).all()
    )
    lead_statuses = dict(
        db.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all()
    )

    return {
        "sent": sent,
        "replies": replies,
        "interested": interested,
        "reply_rate": round(replies / sent, 4) if sent else 0.0,
        "copywriter_rejections": copy_rejections,  # public proof of the anti-hallucination claim
        "enrollment_states": states,
        "lead_statuses": lead_statuses,
    }
