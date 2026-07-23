"""Touch-task lifecycle (M3.1): resolving and cancelling human-touch tasks.

Resolution always advances the enrollment through the normal state machine
(`awaiting_human_touch` → `waiting`) and schedules the next step exactly like a
successful email send. Nothing here touches the bandit: task completion is not a
reply, so copy posteriors never move (roadmap M3.3 decision, revisit in M6).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Enrollment, Lead, SequenceStep, TouchTask
from craftsman.sequencer.machine import Event
from craftsman.sequencer.tick import apply_event, schedule_next_step

log = logging.getLogger(__name__)

RESOLVE_EVENTS = {
    "done": Event.TASK_DONE,
    "skipped": Event.TASK_SKIPPED,
    "expired": Event.TASK_EXPIRED,
}


def resolve_task(
    db: Session,
    task: TouchTask,
    resolution: str,
    *,
    outcome: str | None = None,
    now: datetime | None = None,
) -> str:
    """Resolve an open task (done | skipped | expired) and advance the sequence.

    Returns the enrollment's new state. Raises ValueError on a non-open task —
    callers translate to 409; a resolved task can never advance a sequence twice.
    """
    if task.status != "open":
        raise ValueError(f"task is {task.status}, not open")
    if resolution not in RESOLVE_EVENTS:
        raise ValueError(f"unknown resolution: {resolution!r}")
    now = now or datetime.now(timezone.utc)

    enrollment = db.get(Enrollment, task.enrollment_id)
    lead = db.get(Lead, enrollment.lead_id)
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == enrollment.campaign_id,
            SequenceStep.step_order == task.step_order,
        )
    )

    task.status = resolution
    task.outcome = outcome
    task.resolved_at = now
    db.add(task)

    new_state = apply_event(
        db,
        enrollment,
        RESOLVE_EVENTS[resolution],
        detail={"task_id": str(task.id), "channel": task.channel, "outcome": outcome},
    )
    wait_days = step.wait_days if step else 3
    schedule_next_step(db, enrollment, wait_days, lead.timezone)
    return new_state


def cancel_open_tasks(db: Session, enrollment_id, reason: str, *, now: datetime | None = None) -> int:
    """Cancel any open tasks for an enrollment that left `awaiting_human_touch` by
    another door (reply, bounce, unsubscribe, erase). Cancelled tasks never advance
    the sequence — the state machine already moved it. Returns tasks cancelled."""
    now = now or datetime.now(timezone.utc)
    open_tasks = db.scalars(
        select(TouchTask).where(
            TouchTask.enrollment_id == enrollment_id, TouchTask.status == "open"
        )
    ).all()
    for task in open_tasks:
        task.status = "cancelled"
        task.outcome = reason
        task.resolved_at = now
        db.add(task)
        log.info("cancelled open %s task %s (%s)", task.channel, task.id, reason)
    return len(open_tasks)
