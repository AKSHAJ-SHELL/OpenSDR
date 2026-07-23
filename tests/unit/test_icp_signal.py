"""ICP scoring blend (M2.3): the ⛔-gated renormalized formula. No-signal leads keep the
2-way blend (byte-identical to pre-M2.3); signal leads use the 3-way blend."""

from craftsman.core.config import get_settings
from craftsman.scoring.icp import score_breakdown

# two short vectors; exact cosine value doesn't matter, only that the blend is applied
A = [1.0, 0.0, 0.0]
B = [1.0, 1.0, 0.0]


def test_no_signal_uses_two_way_blend_unchanged():
    s = get_settings()
    bd = score_breakdown(A, B, "VP Operations", signal_boost=None)
    assert bd.signal is None
    expected = s.icp_cosine_weight * bd.cosine + s.icp_rule_weight * bd.rule
    assert abs(bd.score - round(expected, 4)) < 1e-4
    # the historical invariant the M1.3 test also asserts
    assert abs((0.7 * bd.cosine + 0.3 * bd.rule) - bd.score) < 1e-3


def test_with_signal_uses_three_way_blend():
    s = get_settings()
    bd = score_breakdown(A, B, "VP Operations", signal_boost=1.0)
    assert bd.signal == 1.0
    expected = (
        s.icp_signal_cosine_weight * bd.cosine
        + s.icp_signal_rule_weight * bd.rule
        + s.icp_signal_weight * 1.0
    )
    assert abs(bd.score - round(expected, 4)) < 1e-4


def test_zero_boost_still_selects_three_way():
    # a company WITH signals that have fully decayed still uses the 3-way path (signal=0.0,
    # not None) — the sentinel is None, never a number.
    bd = score_breakdown(A, B, "VP Operations", signal_boost=0.0)
    assert bd.signal == 0.0
    two_way = score_breakdown(A, B, "VP Operations", signal_boost=None)
    # 3-way with boost 0 gives a different (lower) score than the 2-way blend
    assert bd.score != two_way.score


def test_boost_is_clamped():
    bd = score_breakdown(A, B, "VP Operations", signal_boost=5.0)
    assert bd.signal == 1.0  # clamped to [0,1]


def test_higher_boost_never_lowers_the_score():
    lo = score_breakdown(A, B, "VP Operations", signal_boost=0.2).score
    hi = score_breakdown(A, B, "VP Operations", signal_boost=0.9).score
    assert hi > lo
