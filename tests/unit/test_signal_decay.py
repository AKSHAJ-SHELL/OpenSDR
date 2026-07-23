"""Signal decay math (M2.3) — pure, no DB/network."""

from datetime import datetime, timedelta, timezone

from craftsman.scoring.signals import SIGNAL_TYPE_WEIGHTS, SignalObservation, signal_boost

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)
HL = 30.0


def _sig(type_, age_days):
    return SignalObservation(type=type_, observed_at=NOW - timedelta(days=age_days))


def test_empty_is_zero():
    assert signal_boost([], NOW, HL) == 0.0


def test_fresh_signal_is_full_weight():
    assert signal_boost([_sig("funding", 0)], NOW, HL) == 1.0  # funding weight 1.0
    assert abs(signal_boost([_sig("job_posting", 0)], NOW, HL) - 0.6) < 1e-9


def test_half_life_halves_weight():
    assert abs(signal_boost([_sig("funding", HL)], NOW, HL) - 0.5) < 1e-9
    assert abs(signal_boost([_sig("funding", 2 * HL)], NOW, HL) - 0.25) < 1e-9


def test_multiple_signals_sum_then_clamp():
    # two fresh funding rounds would sum to 2.0 → clamped to 1.0
    assert signal_boost([_sig("funding", 0), _sig("funding", 0)], NOW, HL) == 1.0
    # a fresh job_posting (0.6) + a half-life-old tech change (0.5·0.5=0.25) = 0.85
    boost = signal_boost([_sig("job_posting", 0), _sig("tech_stack_change", HL)], NOW, HL)
    assert abs(boost - 0.85) < 1e-9


def test_unknown_type_contributes_nothing():
    assert signal_boost([_sig("mystery", 0)], NOW, HL) == 0.0


def test_future_timestamp_never_exceeds_full_weight():
    # clock skew: an observed_at slightly in the future clamps age to 0, not negative age
    assert signal_boost([_sig("funding", -5)], NOW, HL) == 1.0


def test_type_weights_are_ordered_as_documented():
    assert (
        SIGNAL_TYPE_WEIGHTS["funding"]
        > SIGNAL_TYPE_WEIGHTS["leadership_hire"]
        > SIGNAL_TYPE_WEIGHTS["job_posting"]
        > SIGNAL_TYPE_WEIGHTS["tech_stack_change"]
    )
