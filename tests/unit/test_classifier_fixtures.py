"""Classifier plumbing tests with MockLLM + the adversarial fixture set.

With a real key (LLM_PROVIDER=anthropic) the fixture file doubles as a live eval:
run `python scripts/eval_classifier.py`.
"""

import json
from datetime import date
from pathlib import Path

from craftsman.core.schemas import ReplyClassification
from craftsman.inbox.classifier import classify_reply
from craftsman.inbox.reply_parser import strip_quoted
from craftsman.llm.mock_impl import MockLLM

FIXTURES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "replies.json").read_text()
)


def test_fixture_file_is_well_formed():
    assert len(FIXTURES) >= 30
    labels = {f["expected_label"] for f in FIXTURES}
    assert labels == {"interested", "objection", "not_now", "ooo", "unsubscribe", "bounce_or_auto"}
    # adversarial cases present
    assert any(f.get("adversarial") for f in FIXTURES)


async def test_classify_reply_passes_text_through():
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.95))
    result = await classify_reply(llm, "Sure, send me more details.")
    assert result.label == "interested"
    assert "send me more details" in llm.calls[0]["user"]


async def test_ooo_return_date_survives_schema():
    llm = MockLLM()
    llm.enqueue(
        ReplyClassification(label="ooo", ooo_return_date=date(2026, 8, 3), confidence=0.9)
    )
    result = await classify_reply(llm, "I am out of office until August 3rd.")
    assert result.ooo_return_date == date(2026, 8, 3)


def test_strip_quoted_removes_history():
    body = (
        "Sounds interesting, send details.\n\n"
        "On Mon, Jul 20, 2026 at 9:00 AM Sam Rivera <sam@flowbot.io> wrote:\n"
        "> Hi Dana,\n> Saw that Acme opened a facility in Austin.\n"
    )
    fresh = strip_quoted(body)
    assert "send details" in fresh
    assert "Austin" not in fresh
