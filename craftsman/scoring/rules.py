"""Signal → score-boost lookup and signal_rules evaluation (M2.3).

`company_signal_boost` is the read side used by scoring (`activate`). `evaluate_rules`
is the write side run after collection: it fires per-campaign rules for a new signal —
`boost_score` (informational; the boost is applied by scoring on the next activate),
`notify` (Slack, never mutates state), and `enroll` (the only autonomy-bearing action).

`enroll` is guarded so it can never do more than a human clicking Activate would:
verified + above-threshold + not-already-enrolled leads only, landing in `queued` so the
normal state machine still runs research → fill → validate → send. Nothing is skipped.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Enrollment, Lead, Signal, SignalRule
from craftsman.scoring.signals import SignalObservation, signal_boost

log = logging.getLogger(__name__)


def company_signal_boost(db: Session, company_id, now: datetime, half_life_days: float) -> float | None:
    """Decayed boost for a company, or None if it has no signals at all. None is the
    sentinel that tells the scorer to use the 2-way (no-signal) blend."""
    if company_id is None:
        return None
    pairs = db.execute(
        select(Signal.type, Signal.observed_at).where(Signal.company_id == company_id)
    ).all()
    if not pairs:
        return None
    obs = [SignalObservation(type=t, observed_at=o) for t, o in pairs]
    return signal_boost(obs, now, half_life_days)


def _enroll_on_signal(db: Session, campaign_id, company_id, threshold: float) -> int:
    """Auto-enroll verified, above-threshold, not-already-enrolled leads at this company
    into `queued`. Returns count enrolled. Identical shape to activate's enrollment —
    the state machine does the rest (research/validation never skipped)."""
    leads = db.scalars(
        select(Lead).where(
            Lead.company_id == company_id,
            Lead.email_verified.is_(True),
            Lead.status == "verified",
        )
    ).all()
    enrolled = 0
    for lead in leads:
        if lead.icp_score is None or lead.icp_score < threshold:
            continue  # below-threshold or never-scored → no auto-enroll (logged upstream)
        exists = db.scalar(
            select(Enrollment.id).where(
                Enrollment.lead_id == lead.id, Enrollment.campaign_id == campaign_id
            )
        )
        if exists is not None:
            continue
        db.add(
            Enrollment(
                lead_id=lead.id,
                campaign_id=campaign_id,
                state="queued",
                current_step=0,
                next_action_at=datetime.now(timezone.utc),
            )
        )
        enrolled += 1
    return enrolled


def evaluate_rules(db: Session, signal: Signal, threshold: float, notify=None) -> dict:
    """Fire active rules matching this signal's type. Returns a small tally for logging.
    `notify` is an optional callable(text) (Slack); absent = notifications are skipped."""
    rules = db.scalars(
        select(SignalRule).where(
            SignalRule.signal_type == signal.type, SignalRule.active.is_(True)
        )
    ).all()
    tally = {"boost_score": 0, "notify": 0, "enroll": 0}
    for rule in rules:
        if rule.action == "enroll":
            tally["enroll"] += _enroll_on_signal(db, rule.campaign_id, signal.company_id, threshold)
        elif rule.action == "notify":
            if notify is not None:
                notify(f"intent signal {signal.type} for company {signal.company_id}")
            tally["notify"] += 1
        elif rule.action == "boost_score":
            # No action at fire time — the boost is applied by scoring on the next
            # activate. Recorded so the tally reflects that a rule matched.
            tally["boost_score"] += 1
    return tally
