"""Meetings (M4.3, G8): the webhook receiver + a read view of the funnel's wins.

`POST /meetings/webhooks/calcom` is deliberately on the unauthenticated allowlist —
Cal.com cannot hold a Craftsman API key. It is gated instead by an HMAC-SHA256
signature over the raw body (`X-Cal-Signature-256`, constant-time compare) and
answers 503 until `CALCOM_WEBHOOK_SECRET` is configured (keyless-off). A valid
signature proves the payload came from the operator's own Cal.com instance.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.config import get_settings
from craftsman.core.models import Campaign, Enrollment, Lead, Meeting
from craftsman.core.schemas import MeetingOut
from craftsman.meetings.providers import MeetingEvent, build_provider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["meetings"])

# States a booking can arrive from, in preference order when a lead has several
# enrollments — the interested thread is almost always the one that booked.
_MATCH_ORDER = (
    "replied_interested", "replied_objection", "replied_not_now",
    "awaiting_human_touch", "waiting", "ooo_rescheduled", "ready", "meeting_booked",
)


def _match_enrollment(db: Session, attendee_emails: tuple[str, ...]) -> Enrollment | None:
    for email in attendee_emails:
        lead = db.scalar(select(Lead).where(Lead.email == email.lower()))
        if lead is None:
            continue
        enrollments = db.scalars(
            select(Enrollment).where(Enrollment.lead_id == lead.id)
        ).all()
        for state in _MATCH_ORDER:
            for enr in enrollments:
                if enr.state == state:
                    return enr
    return None


def _apply_event_row(db: Session, event: MeetingEvent) -> Meeting:
    from craftsman.sequencer.machine import Event, InvalidTransition
    from craftsman.sequencer.tick import apply_event
    from craftsman.sequencer.touch import cancel_open_tasks

    now = datetime.now(timezone.utc)
    meeting = db.scalar(
        select(Meeting).where(Meeting.provider_event_id == event.provider_event_id)
    )
    if meeting is None:
        meeting = Meeting(
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            status=event.status,
            start_at=event.start_at,
        )
        enrollment = _match_enrollment(db, event.attendee_emails)
        meeting.enrollment_id = enrollment.id if enrollment else None
        db.add(meeting)
        db.flush()
    else:
        meeting.status = event.status
        if event.start_at is not None:
            meeting.start_at = event.start_at
        db.add(meeting)

    if event.status == "booked" and meeting.booked_at is None:
        meeting.booked_at = now
        enrollment = (
            db.get(Enrollment, meeting.enrollment_id) if meeting.enrollment_id else None
        )
        if enrollment is not None and enrollment.state != "meeting_booked":
            was_awaiting = enrollment.state == "awaiting_human_touch"
            try:
                apply_event(
                    db, enrollment, Event.MEETING_BOOKED,
                    detail={"provider": event.provider, "event_id": event.provider_event_id},
                )
                enrollment.next_action_at = None
                db.add(enrollment)
                if was_awaiting:
                    cancel_open_tasks(db, enrollment.id, reason="meeting_booked")
            except InvalidTransition as e:
                log.warning("meeting booked but transition skipped: %s", e)
    db.flush()  # state + meeting row durable in-transaction before the response
    return meeting


@router.post("/webhooks/calcom", status_code=200)
async def calcom_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_cal_signature_256: str | None = Header(default=None),
):
    provider = build_provider(get_settings())
    if provider is None:
        raise HTTPException(503, "calendar webhooks not configured (set CALCOM_WEBHOOK_SECRET)")
    raw = await request.body()
    if not provider.verify_webhook(raw, x_cal_signature_256):
        raise HTTPException(401, "invalid webhook signature")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    event = provider.parse_webhook(payload)
    if event is None:
        # unknown trigger or missing uid — acknowledge so Cal.com stops retrying
        return {"handled": False}
    meeting = _apply_event_row(db, event)
    return {
        "handled": True,
        "meeting_id": str(meeting.id),
        "status": meeting.status,
        "matched_enrollment": meeting.enrollment_id is not None,
    }


def _meeting_out(db: Session, meeting: Meeting) -> MeetingOut:
    out = MeetingOut.model_validate(meeting)
    if meeting.enrollment_id:
        row = db.execute(
            select(Lead, Campaign.name)
            .join(Enrollment, Enrollment.lead_id == Lead.id)
            .outerjoin(Campaign, Enrollment.campaign_id == Campaign.id)
            .where(Enrollment.id == meeting.enrollment_id)
        ).first()
        if row:
            lead, campaign_name = row
            out.lead_email = lead.email
            out.lead_name = " ".join(
                p for p in (lead.first_name, lead.last_name) if p
            ) or None
            out.campaign_name = campaign_name
    return out


@router.get("", response_model=list[MeetingOut], dependencies=[Depends(require_scope("read"))])
def list_meetings(
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    stmt = select(Meeting).order_by(Meeting.created_at.desc()).limit(min(limit, 500))
    if status is not None:
        stmt = stmt.where(Meeting.status == status)
    return [_meeting_out(db, m) for m in db.scalars(stmt).all()]
