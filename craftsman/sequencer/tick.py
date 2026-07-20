"""The sequencer tick: runs every 60s from Celery beat.

SELECT ... FOR UPDATE SKIP LOCKED over due enrollments, apply events, enqueue work.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import AuditLog, Campaign, Enrollment, SequenceStep
from craftsman.sequencer.machine import Event, InvalidTransition, next_state
from craftsman.sequencer.scheduling import next_send_time

log = logging.getLogger(__name__)

SCANNABLE_STATES = ("queued", "ready", "waiting", "ooo_rescheduled")
TICK_BATCH = 200


def apply_event(
    db: Session,
    enrollment: Enrollment,
    event: Event,
    detail: dict | None = None,
) -> str:
    """Transition + audit log. Raises InvalidTransition on undefined pairs."""
    old = enrollment.state
    new = next_state(old, event)
    enrollment.state = new
    db.add(enrollment)
    db.add(
        AuditLog(
            enrollment_id=enrollment.id,
            from_state=old,
            to_state=new,
            event=event.value,
            detail=detail or {},
        )
    )
    return new


def due_enrollments(db: Session, now: datetime, limit: int = TICK_BATCH) -> list[Enrollment]:
    stmt = (
        select(Enrollment)
        .where(
            Enrollment.next_action_at <= now,
            Enrollment.state.in_(SCANNABLE_STATES),
        )
        .order_by(Enrollment.next_action_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(db.scalars(stmt).all())


def tick(db: Session, enqueue_research, enqueue_send, now: datetime | None = None) -> int:
    """One scheduler pass. enqueue_research/enqueue_send are callables
    (Celery task .delay in prod, plain lambdas in tests). Returns rows handled."""
    now = now or datetime.now(timezone.utc)
    handled = 0

    for enrollment in due_enrollments(db, now):
        try:
            if enrollment.state == "queued":
                enrollment.state = "researching"
                db.add(enrollment)
                enqueue_research(str(enrollment.id))

            elif enrollment.state in ("waiting", "ooo_rescheduled"):
                campaign = db.get(Campaign, enrollment.campaign_id)
                total_steps = db.scalar(
                    select(SequenceStep.step_order)
                    .where(SequenceStep.campaign_id == campaign.id)
                    .order_by(SequenceStep.step_order.desc())
                    .limit(1)
                ) or 0
                if enrollment.current_step >= total_steps:
                    old = enrollment.state
                    enrollment.state = "finished_no_reply"
                    enrollment.next_action_at = None
                    db.add(enrollment)
                    db.add(AuditLog(
                        enrollment_id=enrollment.id, from_state=old,
                        to_state="finished_no_reply", event="timer", detail={},
                    ))
                else:
                    apply_event(db, enrollment, Event.TIMER)
                    enrollment.current_step += 1
                    enqueue_send(str(enrollment.id))

            elif enrollment.state == "ready":
                enqueue_send(str(enrollment.id))
                enrollment.next_action_at = None  # send task owns the next schedule
                db.add(enrollment)

            handled += 1
        except InvalidTransition as e:
            log.warning("tick: %s", e)

    return handled


def schedule_next_step(db: Session, enrollment: Enrollment, wait_days: int, lead_tz: str) -> None:
    """After a successful send: waiting until wait_days business days out."""
    enrollment.next_action_at = next_send_time(
        after_utc=datetime.now(timezone.utc),
        wait_business_days=wait_days,
        lead_tz=lead_tz,
    )
    db.add(enrollment)
