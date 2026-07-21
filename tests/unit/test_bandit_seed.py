"""Seedable bandit RNG (M0.6a, finding B2).

BANDIT_SEED (unset by default) makes pick_arm draw a reproducible stream for sims/CI.
"""

from craftsman.bandit import thompson
from craftsman.bandit.thompson import Arm, pick_arm
from craftsman.core.config import get_settings


def _reset_settings_and_rng():
    get_settings.cache_clear()
    thompson._SEEDED_RNG = None


def test_seeded_rng_is_a_reproducible_stream(monkeypatch):
    monkeypatch.setenv("BANDIT_SEED", "123")
    _reset_settings_and_rng()
    try:
        rng1 = thompson.get_bandit_rng()
        seq1 = [rng1.beta(2, 5) for _ in range(10)]
        # the same cached generator continues the stream (not a fresh identical draw)
        assert thompson.get_bandit_rng() is rng1
        # replaying from the seed reproduces the stream exactly
        thompson._SEEDED_RNG = None
        seq2 = [thompson.get_bandit_rng().beta(2, 5) for _ in range(10)]
        assert seq1 == seq2
    finally:
        monkeypatch.delenv("BANDIT_SEED", raising=False)
        _reset_settings_and_rng()


def test_unset_seed_gives_independent_generators(monkeypatch):
    monkeypatch.delenv("BANDIT_SEED", raising=False)
    _reset_settings_and_rng()
    try:
        assert thompson.get_bandit_rng() is not thompson.get_bandit_rng()
    finally:
        _reset_settings_and_rng()


def test_pick_arm_is_reproducible_with_a_seed(monkeypatch):
    monkeypatch.setenv("BANDIT_SEED", "7")
    _reset_settings_and_rng()
    try:
        spec = [("a", 5.0, 20.0), ("b", 3.0, 20.0), ("c", 8.0, 20.0)]
        picks1 = [pick_arm([Arm(*x) for x in spec]).id for _ in range(30)]
        thompson._SEEDED_RNG = None
        picks2 = [pick_arm([Arm(*x) for x in spec]).id for _ in range(30)]
        assert picks1 == picks2
    finally:
        monkeypatch.delenv("BANDIT_SEED", raising=False)
        _reset_settings_and_rng()
