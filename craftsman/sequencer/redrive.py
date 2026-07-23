"""Recovery for stuck enrollments and unsent claims (M0.6b Phase 4).

`error` is a terminal state and an outbound claim can be left `sent_at IS NULL` by a hard
crash (see M0.6a). These give a human, or a periodic sweep, a way to unstick them — always
through the normal pipeline, always audited.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.core.models import AuditLog, Company, Enrollment, Lead, Message, SequenceStep

REDRIVE_ACTIONS = ("retry", "skip", "kill")


def redrive_enrollment(db: Session, enrollment: Enrollment, action: str,
                       *, now: datetime | None = None) -> str:
    """Apply a human recovery action to an enrollment. Returns the new state.

    retry — re-enter the pipeline: `queued` (re-research) if there's no brief yet,
            else `ready` to re-attempt the current step.
    skip  — advance past the failed step and re-`ready`; past the last step → finished.
    kill  — give up; leave it terminal (`error`).
    """
    if action not in REDRIVE_ACTIONS:
        raise ValueError(f"unknown redrive action: {action!r}")
    now = now or datetime.now(timezone.utc)
    old = enrollment.state

    if action == "retry":
        lead = db.get(Lead, enrollment.lead_id)
        company = db.get(Company, lead.company_id) if lead and lead.company_id else None
        has_brief = bool(company and company.research_brief)
        if has_brief:
            new = "ready"
            enrollment.current_step = max(enrollment.current_step, 1)
        else:
            new = "queued"
            enrollment.current_step = 0
        enrollment.next_action_at = now

    elif action == "skip":
        last_step = db.scalar(
            select(func.max(SequenceStep.step_order)).where(
                SequenceStep.campaign_id == enrollment.campaign_id
            )
        ) or 0
        if enrollment.current_step >= last_step:
            new = "finished_no_reply"
            enrollment.next_action_at = None
        else:
            new = "ready"
            enrollment.current_step += 1
            enrollment.next_action_at = now

    else:  # kill
        new = "error"
        enrollment.next_action_at = None

    enrollment.state = new
    db.add(enrollment)
    db.add(AuditLog(
        enrollment_id=enrollment.id, from_state=old, to_state=new,
        event=f"redrive_{action}", detail={"action": action},
    ))
    return new


def redrive_unsent_claims(db: Session, *, after_minutes: int, now: datetime | None = None) -> int:
    """Sweep outbound claims stuck with sent_at IS NULL past the age cutoff (a crash left
    them). Delete each claim, free the campaign slot, and re-ready the lead so the next
    tick re-sends. Returns the number of claims re-driven."""
    from datetime import timedelta

    from craftsman.sender.smtp import release_campaign_slot

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=after_minutes)
    stuck = db.scalars(
        select(Message).where(
            Message.direction == "outbound",
            Message.sent_at.is_(None),
            Message.created_at < cutoff,
        )
    ).all()

    count = 0
    for msg in stuck:
        enrollment = db.get(Enrollment, msg.enrollment_id) if msg.enrollment_id else None
        db.delete(msg)  # remove the claim so the re-send doesn't hit the unique index
        if enrollment is not None:
            release_campaign_slot(db, enrollment.campaign_id)
            if enrollment.state == "ready":
                enrollment.next_action_at = now  # tick set it to NULL on dispatch; re-arm it
                db.add(enrollment)
                db.add(AuditLog(
                    enrollment_id=enrollment.id, from_state="ready", to_state="ready",
                    event="redrive_unsent", detail={"reason": "unsent_claim_swept"},
                ))
        count += 1
    return count
