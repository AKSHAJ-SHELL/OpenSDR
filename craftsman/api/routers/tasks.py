"""Human-touch task queue (M3): list, complete, skip.

The assisted channels' whole product surface: a human sees the validated content,
performs the touch off-platform (LinkedIn, phone), and records the outcome here.
Completing or skipping advances the sequence through the normal state machine —
there is no other way a task step moves forward.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.channels import get_channel
from craftsman.compliance.suppression import is_suppressed
from craftsman.core.models import Campaign, Company, Enrollment, Lead, TouchTask
from craftsman.core.schemas import TaskCompleteRequest, TaskOut
from craftsman.sequencer.touch import cancel_open_tasks, resolve_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _brief_highlights(company: Company | None) -> list[str]:
    """Grounded talking points from the cached research brief: trigger events and
    pain points — the same facts the validator held the content to."""
    if company is None or not company.research_brief:
        return []
    brief = company.research_brief
    highlights: list[str] = []
    for ev in (brief.get("trigger_events") or [])[:3]:
        claim = ev.get("claim") if isinstance(ev, dict) else None
        if claim:
            highlights.append(claim)
    for pain in (brief.get("likely_pain_points") or [])[:3]:
        highlights.append(pain)
    return highlights[:5]


def _dialer_available() -> bool:
    from craftsman.core.config import get_settings
    from craftsman.sender.dialer import build_dialer

    return build_dialer(get_settings()) is not None


def _task_out(db: Session, task: TouchTask, now: datetime) -> TaskOut:
    enrollment = db.get(Enrollment, task.enrollment_id)
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None
    company = db.get(Company, lead.company_id) if lead and lead.company_id else None
    campaign = db.get(Campaign, enrollment.campaign_id) if enrollment else None
    return TaskOut(
        id=task.id,
        enrollment_id=task.enrollment_id,
        channel=task.channel,
        step_order=task.step_order,
        status=task.status,
        outcome=task.outcome,
        payload=task.payload,
        due_at=task.due_at,
        overdue=task.status == "open" and task.due_at is not None and task.due_at < now,
        created_at=task.created_at,
        resolved_at=task.resolved_at,
        lead_id=lead.id if lead else None,
        lead_email=lead.email if lead else None,
        lead_name=" ".join(x for x in [lead.first_name, lead.last_name] if x) or None
        if lead
        else None,
        lead_title=lead.title if lead else None,
        linkedin_url=lead.linkedin_url if lead else None,
        phone=lead.phone if lead else None,
        company_name=company.name if company else None,
        company_domain=company.domain if company else None,
        campaign_id=campaign.id if campaign else None,
        campaign_name=campaign.name if campaign else None,
        brief_highlights=_brief_highlights(company),
        dialer_available=task.channel == "call_task" and _dialer_available(),
    )


@router.get("", response_model=list[TaskOut], dependencies=[Depends(require_scope("read"))])
def list_tasks(
    status: str = "open",
    channel: str | None = None,
    campaign_id: uuid.UUID | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Open tasks by default, oldest due first. Suppression is re-checked at read
    time: a task whose lead unsubscribed since generation is cancelled here, not
    shown — the do-not-contact list gates every channel, not just email."""
    now = datetime.now(timezone.utc)
    stmt = select(TouchTask)
    if status != "all":
        stmt = stmt.where(TouchTask.status == status)
    if channel:
        stmt = stmt.where(TouchTask.channel == channel)
    stmt = stmt.order_by(TouchTask.due_at).limit(min(limit, 500))
    tasks = list(db.scalars(stmt).all())

    out: list[TaskOut] = []
    for task in tasks:
        enrollment = db.get(Enrollment, task.enrollment_id)
        if campaign_id and (enrollment is None or enrollment.campaign_id != campaign_id):
            continue
        lead = db.get(Lead, enrollment.lead_id) if enrollment else None
        if task.status == "open" and lead is not None and is_suppressed(db, lead.email):
            cancel_open_tasks(db, task.enrollment_id, reason="suppressed", now=now)
            continue
        out.append(_task_out(db, task, now))
    return out


@router.get("/{task_id}", response_model=TaskOut, dependencies=[Depends(require_scope("read"))])
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    task = db.get(TouchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    # Same do-not-contact re-check as the list endpoint: an open task for a lead
    # suppressed since generation is cancelled on read, so the caller sees the
    # cancelled status instead of an actionable touch.
    if task.status == "open":
        enrollment = db.get(Enrollment, task.enrollment_id)
        lead = db.get(Lead, enrollment.lead_id) if enrollment else None
        if lead is not None and is_suppressed(db, lead.email):
            cancel_open_tasks(db, task.enrollment_id, reason="suppressed")
    return _task_out(db, task, datetime.now(timezone.utc))


@router.post(
    "/{task_id}/complete", response_model=TaskOut, dependencies=[Depends(require_scope("operate"))]
)
def complete_task(task_id: uuid.UUID, body: TaskCompleteRequest, db: Session = Depends(get_db)):
    """Record that the human performed the touch. Advances the sequence
    (awaiting_human_touch → waiting → next step on schedule). 409 on a task that is
    not open — a resolved task can never advance a sequence twice."""
    task = db.get(TouchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.status != "open":
        raise HTTPException(409, f"task is {task.status}, not open")

    spec = get_channel(task.channel)
    outcome = body.outcome or (spec.outcomes[0] if spec.outcomes else None)
    if spec.outcomes and outcome not in spec.outcomes:
        raise HTTPException(
            422, f"invalid outcome {outcome!r} for {task.channel}; expected one of {list(spec.outcomes)}"
        )

    enrollment = db.get(Enrollment, task.enrollment_id)
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None
    if lead is not None and is_suppressed(db, lead.email):
        cancel_open_tasks(db, task.enrollment_id, reason="suppressed")
        # get_db rolls back on the 409 — the cancellation must outlive the error
        db.commit()
        raise HTTPException(409, "lead is suppressed; task cancelled — do not contact")

    try:
        resolve_task(db, task, "done", outcome=outcome)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _task_out(db, task, datetime.now(timezone.utc))


@router.post(
    "/{task_id}/dial", dependencies=[Depends(require_scope("operate"))]
)
async def dial_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """Click-to-dial (M3.3, optional): rings the OPERATOR's phone first, then
    connects the lead — Craftsman never robocalls a prospect. 400 unless a
    complete BYO Twilio config is present; the tel: link always works without it.
    Dialing does not resolve the task — the human records the outcome via
    /complete after the call."""
    from craftsman.core.config import get_settings
    from craftsman.sender.dialer import DialerError, build_dialer

    task = db.get(TouchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.channel != "call_task":
        raise HTTPException(422, "only call tasks can dial")
    if task.status != "open":
        raise HTTPException(409, f"task is {task.status}, not open")

    enrollment = db.get(Enrollment, task.enrollment_id)
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None
    if lead is None or not lead.phone:
        raise HTTPException(422, "lead has no phone number")
    if is_suppressed(db, lead.email):
        cancel_open_tasks(db, task.enrollment_id, reason="suppressed")
        # get_db rolls back on the 409 — the cancellation must outlive the error
        db.commit()
        raise HTTPException(409, "lead is suppressed; task cancelled — do not contact")

    dialer = build_dialer(get_settings())
    if dialer is None:
        raise HTTPException(
            400,
            "no dialer configured — set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "TWILIO_FROM_NUMBER and TWILIO_OPERATOR_NUMBER, or use the tel: link",
        )
    try:
        sid = await dialer.dial(lead.phone)
    except DialerError as e:
        raise HTTPException(502, str(e))
    return {"call_sid": sid, "to_operator": dialer.operator_number}


@router.post(
    "/{task_id}/skip", response_model=TaskOut, dependencies=[Depends(require_scope("operate"))]
)
def skip_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """Human declined this touch; the sequence advances without it (audited as
    task_skipped — distinct from done and from expiry)."""
    task = db.get(TouchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.status != "open":
        raise HTTPException(409, f"task is {task.status}, not open")
    enrollment = db.get(Enrollment, task.enrollment_id)
    lead = db.get(Lead, enrollment.lead_id) if enrollment else None
    if lead is not None and is_suppressed(db, lead.email):
        cancel_open_tasks(db, task.enrollment_id, reason="suppressed")
        # get_db rolls back on the 409 — the cancellation must outlive the error
        db.commit()
        raise HTTPException(409, "lead is suppressed; task cancelled — do not contact")
    try:
        resolve_task(db, task, "skipped")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _task_out(db, task, datetime.now(timezone.utc))
