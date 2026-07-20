"""Bandit simulator: scripted personas with seeded reply rates.

Watch Thompson sampling converge on the better copy with zero real emails sent.
This is the bounty demo. Run: python -m craftsman.bandit.simulator
"""

from dataclasses import dataclass, field

import numpy as np

from craftsman.bandit.thompson import Arm, pick_arm, update_arm


@dataclass
class SimArm:
    name: str
    true_reply_rate: float
    arm: Arm = None  # type: ignore[assignment]

    def __post_init__(self):
        self.arm = Arm(id=self.name, alpha=1.0, beta=1.0)


@dataclass
class SimResult:
    sends: int
    traffic: dict[str, int]
    posteriors: dict[str, tuple[float, float]]
    history: list[dict] = field(default_factory=list)

    @property
    def best_arm_share(self) -> float:
        best = max(self.traffic, key=self.traffic.get)
        return self.traffic[best] / max(1, self.sends)


def simulate(
    arms: list[SimArm],
    n_sends: int = 500,
    seed: int = 0,
    snapshot_every: int = 25,
) -> SimResult:
    """Simulate n_sends through the bandit against fixed true reply rates."""
    rng = np.random.default_rng(seed)
    traffic = {a.name: 0 for a in arms}
    history: list[dict] = []

    for i in range(1, n_sends + 1):
        chosen = pick_arm([a.arm for a in arms], rng)
        sim_arm = next(a for a in arms if a.name == chosen.id)
        traffic[sim_arm.name] += 1
        replied = rng.random() < sim_arm.true_reply_rate
        update_arm(sim_arm.arm, success=replied)

        if i % snapshot_every == 0 or i == n_sends:
            history.append(
                {
                    "send": i,
                    **{
                        a.name: {"alpha": a.arm.alpha, "beta": a.arm.beta, "traffic": traffic[a.name]}
                        for a in arms
                    },
                }
            )

    return SimResult(
        sends=n_sends,
        traffic=traffic,
        posteriors={a.name: (a.arm.alpha, a.arm.beta) for a in arms},
        history=history,
    )


DEFAULT_SCENARIO = [
    SimArm("pain_led", 0.06),
    SimArm("trigger_led", 0.02),
    SimArm("question_led", 0.035),
]


def main() -> None:  # pragma: no cover
    result = simulate(DEFAULT_SCENARIO, n_sends=500, seed=42)
    print(f"simulated {result.sends} sends\n")
    print(f"{'arm':<14}{'true rate':<11}{'traffic':<9}{'posterior mean':<15}")
    for a in DEFAULT_SCENARIO:
        alpha, beta = result.posteriors[a.name]
        print(
            f"{a.name:<14}{a.true_reply_rate:<11.3f}{result.traffic[a.name]:<9}"
            f"{alpha / (alpha + beta):<15.4f}"
        )
    print(f"\nbest arm captured {result.best_arm_share:.0%} of traffic")


if __name__ == "__main__":
    main()
