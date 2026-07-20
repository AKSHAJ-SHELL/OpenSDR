from craftsman.bandit.simulator import SimArm, simulate


def test_bandit_converges_on_seeded_difference():
    """Design-doc scenario: arm A 6%, arm B 2%, 500 sends → A dominates."""
    result = simulate([SimArm("A", 0.06), SimArm("B", 0.02)], n_sends=500, seed=42)
    assert result.traffic["A"] > result.traffic["B"] * 2
    assert result.best_arm_share > 0.70

    a_alpha, a_beta = result.posteriors["A"]
    b_alpha, b_beta = result.posteriors["B"]
    assert a_alpha / (a_alpha + a_beta) > b_alpha / (b_alpha + b_beta)


def test_convergence_is_robust_across_seeds():
    wins = 0
    for seed in range(10):
        result = simulate([SimArm("A", 0.06), SimArm("B", 0.02)], n_sends=500, seed=seed)
        if result.traffic["A"] > result.traffic["B"]:
            wins += 1
    assert wins >= 9  # Thompson may explore a losing arm on rare seeds, but not often


def test_history_snapshots_for_dashboard():
    result = simulate([SimArm("A", 0.06), SimArm("B", 0.02)], n_sends=100, seed=1, snapshot_every=25)
    assert [h["send"] for h in result.history] == [25, 50, 75, 100]
    assert "alpha" in result.history[0]["A"]
