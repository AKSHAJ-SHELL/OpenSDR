"""Thompson-sampling bandit over copy variants — the learning loop.

Each (sequence_step, variant) is an arm with a Beta(alpha, beta) posterior over
reply rate. Pick by sampling each posterior and taking the argmax; update from
classified reply outcomes. Reply rates are low (2-8%), so posteriors stay honest
about uncertainty — exactly what Thompson sampling is for.
"""

from dataclasses import dataclass

import numpy as np

REWARD_LABELS = {"interested", "objection", "not_now"}  # any human reply = copy worked
NEUTRAL_LABELS = {"ooo", "bounce_or_auto"}  # not the copy's fault: no update
DEACTIVATE_RATIO = 0.5  # arm dies if mean < half the best arm's mean...
DEACTIVATE_MIN_TRIALS = 30  # ...after at least this many trials


@dataclass
class Arm:
    id: str
    alpha: float
    beta: float

    @property
    def trials(self) -> int:
        return int(self.alpha + self.beta - 2)  # priors are Beta(1,1)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


_SEEDED_RNG: np.random.Generator | None = None


def get_bandit_rng() -> np.random.Generator:
    """The generator pick_arm should draw from.

    When BANDIT_SEED is set, return one cached generator seeded once — a deterministic
    *stream* across calls, for reproducible simulations and CI. When unset, return a
    fresh default generator each call (production behavior). Do NOT set a seed in a
    multi-worker deployment: every process would seed identically and lose independence.
    """
    global _SEEDED_RNG
    from craftsman.core.config import get_settings

    seed = get_settings().bandit_seed
    if seed is None:
        return np.random.default_rng()
    if _SEEDED_RNG is None:
        _SEEDED_RNG = np.random.default_rng(seed)
    return _SEEDED_RNG


def pick_arm(arms: list[Arm], rng: np.random.Generator | None = None) -> Arm:
    """Sample each posterior once; return the argmax arm."""
    if not arms:
        raise ValueError("no active arms to pick from")
    rng = rng or get_bandit_rng()
    samples = [rng.beta(a.alpha, a.beta) for a in arms]
    return arms[int(np.argmax(samples))]


def update_arm(arm: Arm, *, success: bool) -> Arm:
    """Reward: classified human reply before deadline → alpha += 1.
    Failure: deadline passed with no reply, or unsubscribe → beta += 1."""
    if success:
        arm.alpha += 1.0
    else:
        arm.beta += 1.0
    return arm


def outcome_for_label(label: str | None) -> bool | None:
    """Map a reply classification to a bandit outcome.
    True = success, False = failure, None = no update (not the copy's fault)."""
    if label in REWARD_LABELS:
        return True
    if label == "unsubscribe":
        return False
    if label in NEUTRAL_LABELS or label is None:
        return None
    return None


def should_deactivate(arm: Arm, best_mean: float, min_trials: int = DEACTIVATE_MIN_TRIALS) -> bool:
    """Guardrail: kill an arm whose posterior mean fell below half the best arm's
    mean after enough trials. Keeps the pool honest without human babysitting."""
    return arm.trials >= min_trials and arm.mean < DEACTIVATE_RATIO * best_mean


def posterior_pdf(alpha: float, beta: float, points: int = 100) -> tuple[list[float], list[float]]:
    """Beta PDF sampled on [0, 0.25] for dashboard plotting (reply rates live there)."""
    from math import exp, lgamma, log

    xs = np.linspace(1e-6, 0.25, points)
    log_b = lgamma(alpha) + lgamma(beta) - lgamma(alpha + beta)
    ys = [exp((alpha - 1) * log(x) + (beta - 1) * log(1 - x) - log_b) for x in xs]
    return list(xs), ys
