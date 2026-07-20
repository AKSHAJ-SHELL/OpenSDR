import numpy as np

from craftsman.bandit.thompson import (
    Arm,
    outcome_for_label,
    pick_arm,
    posterior_pdf,
    should_deactivate,
    update_arm,
)


def test_update_math():
    arm = Arm(id="a", alpha=1.0, beta=1.0)
    update_arm(arm, success=True)
    assert (arm.alpha, arm.beta) == (2.0, 1.0)
    update_arm(arm, success=False)
    assert (arm.alpha, arm.beta) == (2.0, 2.0)
    assert arm.trials == 2
    assert arm.mean == 0.5


def test_outcome_mapping():
    assert outcome_for_label("interested") is True
    assert outcome_for_label("objection") is True
    assert outcome_for_label("not_now") is True
    assert outcome_for_label("unsubscribe") is False
    assert outcome_for_label("ooo") is None       # not the copy's fault
    assert outcome_for_label("bounce_or_auto") is None
    assert outcome_for_label(None) is None


def test_pick_prefers_clearly_better_arm():
    rng = np.random.default_rng(42)
    good = Arm(id="good", alpha=20, beta=200)   # ~9%
    bad = Arm(id="bad", alpha=3, beta=300)      # ~1%
    picks = [pick_arm([good, bad], rng).id for _ in range(200)]
    assert picks.count("good") > 180


def test_deactivation_guardrail():
    best_mean = 0.08
    weak = Arm(id="w", alpha=2, beta=98)  # mean 0.02 < half of 0.08, 98 trials
    assert should_deactivate(weak, best_mean)
    young = Arm(id="y", alpha=1, beta=11)  # only 10 trials: protected
    assert not should_deactivate(young, best_mean)
    decent = Arm(id="d", alpha=5, beta=95)  # mean 0.05 >= 0.04: safe
    assert not should_deactivate(decent, best_mean)


def test_posterior_pdf_shape():
    xs, ys = posterior_pdf(3, 50)
    assert len(xs) == len(ys) == 100
    assert all(y >= 0 for y in ys)
    # mode should sit near alpha-1/(alpha+beta-2) ≈ 0.039
    assert abs(xs[int(np.argmax(ys))] - 0.039) < 0.02


def test_bandit_sanity_best_arm_dominates_by_n300():
    """Design-doc acceptance test: with true rates 6% vs 2%, the better arm
    receives > 70% of traffic by n=300 simulated sends."""
    rng = np.random.default_rng(7)
    true_rates = {"A": 0.06, "B": 0.02}
    arms = {k: Arm(id=k, alpha=1.0, beta=1.0) for k in true_rates}
    traffic = {k: 0 for k in true_rates}

    for _ in range(300):
        arm = pick_arm(list(arms.values()), rng)
        traffic[arm.id] += 1
        replied = rng.random() < true_rates[arm.id]
        update_arm(arm, success=replied)

    assert traffic["A"] / 300 > 0.70, traffic
