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

# awaiting_human_touch is scanned only for skip_on_expire steps: tasks that hold the
# sequence (the default) carry next_action_at = NULL and are never due (M3.1).
SCANNABLE_STATES = ("queued", "ready", "waiting", "ooo_rescheduled", "awaiting_human_touch")
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


def _current_step(db: Session, enrollment: Enrollment) -> SequenceStep | None:
    return db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == enrollment.campaign_id,
            SequenceStep.step_order == max(enrollment.current_step, 1),
        )
    )


def tick(
    db: Session,
    enqueue_research,
    enqueue_send,
    enqueue_task=None,
    now: datetime | None = None,
) -> int:
    """One scheduler pass. enqueue_research/enqueue_send/enqueue_task are callables
    (Celery task .delay in prod, plain lambdas in tests). Returns rows handled.

    enqueue_task (M3.1) routes assisted-channel steps to generate_touch_task; when a
    caller omits it (pre-M3 call sites, email-only tests) a due task step is left
    untouched and logged rather than mis-routed to the email sender."""
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
                    _dispatch_ready(db, enrollment, enqueue_send, enqueue_task)

            elif enrollment.state == "ready":
                _dispatch_ready(db, enrollment, enqueue_send, enqueue_task)

            elif enrollment.state == "awaiting_human_touch":
                # due only when the step opted into skip_on_expire (else next_action_at
                # is NULL): expire the open task and advance the sequence (M3.1)
                _expire_due_task(db, enrollment, now)

            handled += 1
        except InvalidTransition as e:
            log.warning("tick: %s", e)

    return handled


def _dispatch_ready(db: Session, enrollment: Enrollment, enqueue_send, enqueue_task) -> None:
    """Route a step that's ready to act by its channel (M3.1). The worker task owns
    the next schedule either way, so next_action_at is cleared on dispatch."""
    from craftsman.channels import is_assisted

    step = _current_step(db, enrollment)
    channel = step.channel if step else "email"
    if is_assisted(channel):
        if enqueue_task is None:
            log.error(
                "enrollment %s step is %s but no enqueue_task provided — leaving due",
                enrollment.id, channel,
            )
            return
        enqueue_task(str(enrollment.id))
    else:
        enqueue_send(str(enrollment.id))
    enrollment.next_action_at = None
    db.add(enrollment)


def _expire_due_task(db: Session, enrollment: Enrollment, now: datetime) -> None:
    from craftsman.core.models import TouchTask
    from craftsman.sequencer.touch import resolve_task

    step = _current_step(db, enrollment)
    if step is None or not step.skip_on_expire:
        # holds-the-sequence task somehow carried a due date; disarm rather than skip
        enrollment.next_action_at = None
        db.add(enrollment)
        log.warning("enrollment %s: due task on a non-skip step — disarmed", enrollment.id)
        return
    task = db.scalar(
        select(TouchTask).where(
            TouchTask.enrollment_id == enrollment.id,
            TouchTask.step_order == enrollment.current_step,
            TouchTask.status == "open",
        )
    )
    if task is None:
        enrollment.next_action_at = None
        db.add(enrollment)
        log.warning("enrollment %s: awaiting_human_touch with no open task — disarmed", enrollment.id)
        return
    resolve_task(db, task, "expired", now=now)


def schedule_next_step(db: Session, enrollment: Enrollment, wait_days: int, lead_tz: str) -> None:
    """After a successful send: waiting until wait_days business days out."""
    enrollment.next_action_at = next_send_time(
        after_utc=datetime.now(timezone.utc),
        wait_business_days=wait_days,
        lead_tz=lead_tz,
    )
    db.add(enrollment)
