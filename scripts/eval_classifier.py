"""Live classifier eval against the adversarial fixture set.

Requires ANTHROPIC_API_KEY (or LLM_PROVIDER=ollama). Design-doc target: ≥ 95% accuracy,
with low-confidence routing exercised on adversarial cases.

Run: python scripts/eval_classifier.py
"""

import asyncio
import json
from pathlib import Path

from craftsman.inbox.classifier import classify_reply
from craftsman.llm.client import get_llm

FIXTURES = json.loads(
    (Path(__file__).parent.parent / "tests" / "fixtures" / "replies.json").read_text()
)


async def main() -> None:
    llm = get_llm()
    correct = 0
    low_conf_routed = 0
    for f in FIXTURES:
        result = await classify_reply(llm, f["body"])
        ok = result.label == f["expected_label"]
        routed = result.confidence < 0.7
        correct += ok
        low_conf_routed += routed
        flag = "✓" if ok else "✗"
        adv = " [adversarial]" if f.get("adversarial") else ""
        print(f"{flag} expected={f['expected_label']:<15} got={result.label:<15} "
              f"conf={result.confidence:.2f}{adv}")
    n = len(FIXTURES)
    print(f"\naccuracy: {correct}/{n} = {correct/n:.1%} (target ≥ 95%)")
    print(f"routed to human review (conf < 0.7): {low_conf_routed}")


if __name__ == "__main__":
    asyncio.run(main())
