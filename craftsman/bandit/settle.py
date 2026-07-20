"""Settle expired pending bandit outcomes. Runs hourly from beat.

A message whose outcome_deadline passed with no classified reply counts as a
failure for its variant.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.bandit.thompson import Arm, should_deactivate, update_arm
from craftsman.core.models import Message, Variant

log = logging.getLogger(__name__)


def settle_expired(db: Session, now: datetime | None = None) -> int:
    """Mark expired pending outcomes as failures; update posteriors. Returns count."""
    now = now or datetime.now(timezone.utc)
    stmt = (
        select(Message)
        .where(
            Message.bandit_outcome == "pending",
            Message.outcome_deadline <= now,
        )
        .with_for_update(skip_locked=True)
    )
    settled = 0
    for msg in db.scalars(stmt).all():
        msg.bandit_outcome = "failure"
        variant = db.get(Variant, msg.variant_id)
        if variant is not None:
            arm = Arm(id=str(variant.id), alpha=variant.alpha, beta=variant.beta)
            update_arm(arm, success=False)
            variant.alpha, variant.beta = arm.alpha, arm.beta
            db.add(variant)
        db.add(msg)
        settled += 1

    if settled:
        apply_deactivation_guardrail(db)
    return settled


def record_reply_outcome(db: Session, message: Message, success: bool) -> None:
    """Called by the inbox pipeline when a reply is classified before the deadline."""
    if message.bandit_outcome != "pending":
        return  # already settled
    message.bandit_outcome = "success" if success else "failure"
    variant = db.get(Variant, message.variant_id)
    if variant is not None:
        arm = Arm(id=str(variant.id), alpha=variant.alpha, beta=variant.beta)
        update_arm(arm, success=success)
        variant.alpha, variant.beta = arm.alpha, arm.beta
        db.add(variant)
    db.add(message)


def apply_deactivation_guardrail(db: Session) -> list[str]:
    """Deactivate arms far behind the best arm within each step. Returns deactivated ids."""
    deactivated: list[str] = []
    step_ids = db.scalars(select(Variant.step_id).distinct()).all()
    for step_id in step_ids:
        variants = list(
            db.scalars(select(Variant).where(Variant.step_id == step_id, Variant.active)).all()
        )
        if len(variants) < 2:
            continue
        arms = {v.id: Arm(id=str(v.id), alpha=v.alpha, beta=v.beta) for v in variants}
        best_mean = max(a.mean for a in arms.values())
        for v in variants:
            if should_deactivate(arms[v.id], best_mean):
                v.active = False
                db.add(v)
                deactivated.append(str(v.id))
                log.info("bandit guardrail deactivated variant %s (%s)", v.id, v.name)
    return deactivated
