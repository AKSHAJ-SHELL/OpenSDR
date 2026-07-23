from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import Enrollment, Lead, Message, ReplyDraft, ReviewQueueItem

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", dependencies=[Depends(require_scope("read"))])
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

    # Copilot drafts (M4.1): acceptance rate over human-resolved drafts — the reply
    # reward process is deliberately separate from the bandit (drafts never move α/β)
    draft_states = dict(
        db.execute(
            select(ReplyDraft.status, func.count(ReplyDraft.id)).group_by(ReplyDraft.status)
        ).all()
    )
    accepted = draft_states.get("sent", 0) + draft_states.get("edited_sent", 0)
    decided = accepted + draft_states.get("discarded", 0)

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
        "reply_drafts": draft_states,
        "draft_acceptance_rate": round(accepted / decided, 4) if decided else 0.0,
        "enrollment_states": states,
        "lead_statuses": lead_statuses,
    }
