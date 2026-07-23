"""M4.1: generate_reply_copy — skeleton selection, rendering, retry, refusal."""

import asyncio

from craftsman.core.schemas import ReplyDraftFill, ResearchBrief
from craftsman.copywriter.reply_fill import REPLY_SKELETONS, generate_reply_copy
from craftsman.llm.mock_impl import MockLLM

BRIEF = ResearchBrief(
    what_they_do="Acme Robotics builds warehouse automation robots.",
    industry="logistics",
    trigger_events=[],
    likely_pain_points=["manual picking costs"],
)
VALUE_PROP = "Flowbot cuts picking costs by a third."
PERSONA = {"name": "Sam", "title": "Founder", "company": "Flowbot"}
REPLY = "Sounds interesting. How does it work? We still pick orders by hand."

GOOD = ReplyDraftFill(
    objection_kind="other",
    acknowledgment="You said you still pick orders by hand.",
    answer_bridge="Flowbot cuts picking costs by a third.",
    cta_question="Worth a quick look?",
)


def _generate(llm, label="interested", **kw):
    return asyncio.run(generate_reply_copy(
        llm=llm,
        brief=BRIEF,
        value_prop=VALUE_PROP,
        persona=PERSONA,
        first_name="Dana",
        reply_text=REPLY,
        label=label,
        **kw,
    ))


def test_interested_uses_interested_skeleton():
    llm = MockLLM()
    llm.enqueue(GOOD)
    result = _generate(llm)
    assert result.ok
    assert result.skeleton_key == "reply_interested"
    assert result.body.startswith("Hi Dana,")
    assert "You said you still pick orders by hand." in result.body
    assert "Sam" in result.body  # signature
    assert result.attempts == 1


def test_objection_timing_selects_timing_skeleton():
    llm = MockLLM()
    llm.enqueue(GOOD.model_copy(update={"objection_kind": "timing"}))
    result = _generate(llm, label="objection")
    assert result.ok
    assert result.skeleton_key == "reply_objection_timing"
    # the follow-up offer is fixed skeleton text driven by the knob, not the LLM
    assert "checked back in 4 weeks" in result.body


def test_objection_info_selects_info_skeleton():
    llm = MockLLM()
    llm.enqueue(GOOD.model_copy(update={"objection_kind": "info"}))
    result = _generate(llm, label="objection", info_line="Here is the short version: https://x.test/one-pager")
    assert result.ok
    assert result.skeleton_key == "reply_objection_info"
    assert "https://x.test/one-pager" in result.body


def test_objection_other_is_deliberately_not_drafted():
    llm = MockLLM()
    llm.enqueue(GOOD)  # objection_kind == "other"
    result = _generate(llm, label="objection")
    assert not result.ok
    assert result.skipped_reason == "objection_needs_human"
    assert result.body == ""


def test_unhandled_label_is_not_drafted():
    llm = MockLLM()
    llm.enqueue(GOOD)
    result = _generate(llm, label="not_now")
    assert not result.ok
    assert result.skipped_reason == "no_draft_for_label:not_now"


def test_validator_reject_retries_once_with_errors():
    llm = MockLLM()
    llm.enqueue(GOOD.model_copy(update={"answer_bridge": "Our customer DataDog loves it."}))
    llm.enqueue(GOOD)
    result = _generate(llm)
    assert result.ok
    assert result.attempts == 2
    assert "REJECTED BY THE VALIDATOR" in llm.calls[1]["user"]
    assert "DataDog" in llm.calls[1]["user"]


def test_double_reject_gives_up():
    bad = GOOD.model_copy(update={"answer_bridge": "We can offer a discount."})
    llm = MockLLM()
    llm.enqueue(bad)
    llm.enqueue(bad)
    result = _generate(llm)
    assert not result.ok
    assert result.skipped_reason is None
    assert any("discount" in e for e in result.validation.errors)


def test_scheduling_line_is_static_and_rendered():
    llm = MockLLM()
    llm.enqueue(GOOD)
    result = _generate(llm, scheduling_line="If it is easier, grab any time here: https://cal.test/sam")
    assert result.ok
    assert "https://cal.test/sam" in result.body


def test_no_scheduling_line_leaves_no_blank_gap():
    llm = MockLLM()
    llm.enqueue(GOOD)
    result = _generate(llm)
    assert result.ok
    assert "\n\n\n" not in result.body


def test_prompt_contains_reply_but_skeletons_are_fixed():
    llm = MockLLM()
    llm.enqueue(GOOD)
    _generate(llm)
    assert REPLY in llm.calls[0]["user"]
    # the skeleton files are the only bodies a draft can ever take
    assert set(REPLY_SKELETONS) == {
        "reply_interested", "reply_objection_timing", "reply_objection_info"
    }
