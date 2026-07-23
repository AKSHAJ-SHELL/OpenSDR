import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import Campaign, Company, Enrollment, Lead, Message, ReviewQueueItem
from craftsman.core.schemas import MessageOut, ReplyClassification, ReviewItemOut
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


@router.get(
    "/review",
    response_model=list[ReviewItemOut],
    dependencies=[Depends(require_scope("read"))],
)
def review_queue(limit: int = 50, db: Session = Depends(get_db)):
    """Unresolved review items with the context and IDs needed to act on them.

    `message_id` is required for the reclassify action on `classification` items;
    without it the queue is read-only (it was missing until M1.3).
    """
    rows = db.scalars(
        select(ReviewQueueItem)
        .where(ReviewQueueItem.resolved.is_(False))
        .order_by(ReviewQueueItem.created_at.desc())
        .limit(limit)
    ).all()

    out = []
    for r in rows:
        item = ReviewItemOut(
            id=r.id,
            kind=r.kind,
            message_id=r.message_id,
            enrollment_id=r.enrollment_id,
            payload=r.payload,
            created_at=r.created_at,
        )
        if r.enrollment_id is not None:
            row = db.execute(
                select(Lead, Campaign.name, Enrollment.state)
                .join(Enrollment, Enrollment.lead_id == Lead.id)
                .outerjoin(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(Enrollment.id == r.enrollment_id)
            ).first()
            if row:
                lead, campaign_name, state = row
                item.lead_email = lead.email
                item.lead_name = " ".join(
                    p for p in (lead.first_name, lead.last_name) if p
                ) or None
                item.campaign_name = campaign_name
                item.enrollment_state = state
        if r.message_id is not None:
            msg = db.get(Message, r.message_id)
            if msg is not None:
                item.message_subject = msg.subject
                item.message_body = msg.body
        out.append(item)
    return out


class RedriveAction(BaseModel):
    action: str  # retry | skip | kill | resolve


@router.post(
    "/review/{item_id}/action",
    response_model=dict,
    dependencies=[Depends(require_scope("operate"))],
)
def review_action(item_id: uuid.UUID, payload: RedriveAction, db: Session = Depends(get_db)):
    """Resolve a review item, optionally re-driving its enrollment.

    retry / skip / kill re-drive the enrollment (see sequencer/redrive.py). `resolve`
    clears the item without touching the enrollment — used when approving a
    low-confidence classification, where the state change is applied by reclassify
    instead and a re-drive would be wrong.
    """
    from craftsman.sequencer.redrive import REDRIVE_ACTIONS, redrive_enrollment

    allowed = (*REDRIVE_ACTIONS, "resolve")
    if payload.action not in allowed:
        raise HTTPException(400, f"action must be one of {allowed}")
    item = db.get(ReviewQueueItem, item_id)
    if item is None:
        raise HTTPException(404, "review item not found")

    new_state = None
    if payload.action != "resolve" and item.enrollment_id is not None:
        enrollment = db.get(Enrollment, item.enrollment_id)
        if enrollment is not None:
            new_state = redrive_enrollment(db, enrollment, payload.action)
    item.resolved = True
    db.add(item)
    return {"resolved": True, "action": payload.action, "new_state": new_state}


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
