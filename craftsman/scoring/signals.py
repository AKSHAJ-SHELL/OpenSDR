"""Intent-signal decay math (M2.3): turn a company's raw signals into a single
`signal_boost` in [0, 1]. Pure and side-effect-free — the network/DB collectors live in
`collectors.py`, the scoring blend in `icp.py`.

boost = clamp( Σ_signals  type_weight[type] · 0.5 ** (age_days / half_life) , 0, 1 )

Recent, high-value signals dominate; old ones decay geometrically. A funding round today
is worth its full weight; the same round a half-life ago is worth half.
"""

from dataclasses import dataclass
from datetime import datetime

# Relative importance per signal type — documented product knobs. The aggregate cap is
# the score's `icp_signal_weight` (config); these set the *shape* within the signal term.
SIGNAL_TYPE_WEIGHTS = {
    "funding": 1.0,
    "leadership_hire": 0.8,
    "job_posting": 0.6,
    "tech_stack_change": 0.5,
}


@dataclass(frozen=True)
class SignalObservation:
    """The minimal view decay needs — decoupled from the ORM row so it's trivially tested."""

    type: str
    observed_at: datetime


def signal_boost(
    signals: list[SignalObservation],
    now: datetime,
    half_life_days: float,
    type_weights: dict[str, float] = SIGNAL_TYPE_WEIGHTS,
) -> float:
    """Decayed, weighted aggregate of a company's signals, clamped to [0, 1]. Returns 0.0
    for no signals — callers distinguish 'no signals' (2-way blend) from this by passing
    `None` to the scorer, not by inspecting the number."""
    if not signals:
        return 0.0
    if half_life_days <= 0:
        # Guard against a misconfigured knob: no decay, just weight-sum (still clamped).
        total = sum(type_weights.get(s.type, 0.0) for s in signals)
        return max(0.0, min(1.0, total))

    total = 0.0
    for s in signals:
        weight = type_weights.get(s.type, 0.0)
        if weight <= 0:
            continue
        age_days = max(0.0, (now - s.observed_at).total_seconds() / 86400.0)
        total += weight * (0.5 ** (age_days / half_life_days))
    return max(0.0, min(1.0, total))
