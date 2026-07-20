import pytest

from craftsman.copywriter.fill import generate_copy, render_skeleton, split_subject_body
from craftsman.core.schemas import ResearchBrief, SlotFill, TriggerEvent
from craftsman.llm.mock_impl import MockLLM

SKELETON = """Subject: {{subject_hook}}

Hi {{first_name}},

{{personalization_sentence}}

{{value_prop_bridge}} {{cta_question}}

{{signature}}"""

BRIEF = ResearchBrief(
    what_they_do="Acme Robotics builds warehouse automation robots.",
    industry="logistics",
    trigger_events=[
        TriggerEvent(
            claim="Acme Robotics opened a facility in Austin",
            source_url="https://acme.com/news",
            approx_date="2026-05",
        )
    ],
    likely_pain_points=["manual picking costs"],
    evidence_quotes=["opened our new Austin facility"],
)

PERSONA = {"name": "Sam Rivera", "title": "Founder", "company": "Flowbot"}
VALUE_PROP = "Flowbot cuts warehouse picking costs by a third."

GOOD_FILL = SlotFill(
    subject_hook="your new Austin facility",
    personalization_sentence="Saw that Acme Robotics opened a facility in Austin.",
    value_prop_bridge="Flowbot cuts warehouse picking costs by a third.",
    cta_question="Worth a look?",
)

BAD_FILL = SlotFill(
    subject_hook="congrats on the Sequoia round",
    personalization_sentence="Huge news about the $40M raise from Sequoia.",
    value_prop_bridge="Flowbot cuts warehouse picking costs by a third.",
    cta_question="Worth a look?",
)


def test_render_skeleton_and_split():
    rendered = render_skeleton(
        SKELETON, GOOD_FILL.model_dump(), {"first_name": "Dana", "signature": "Sam"}
    )
    subject, body = split_subject_body(rendered)
    assert subject == "your new Austin facility"
    assert "Hi Dana," in body
    assert "{{" not in body


def test_render_raises_on_unfilled_slot():
    with pytest.raises(ValueError, match="unfilled"):
        render_skeleton(SKELETON, {"subject_hook": "x"}, {"first_name": "Dana"})


async def test_good_fill_passes_first_try():
    llm = MockLLM()
    llm.enqueue(GOOD_FILL)
    result = await generate_copy(
        llm=llm, brief=BRIEF, skeleton=SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert result.ok
    assert result.attempts == 1
    assert "Austin" in result.body


async def test_hallucinated_fill_retries_with_errors_then_passes():
    llm = MockLLM()
    llm.enqueue(BAD_FILL)
    llm.enqueue(GOOD_FILL)
    result = await generate_copy(
        llm=llm, brief=BRIEF, skeleton=SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert result.ok
    assert result.attempts == 2
    # the retry prompt must carry the validator errors back to the model
    assert "REJECTED BY THE VALIDATOR" in llm.calls[1]["user"]
    assert "Sequoia" in llm.calls[1]["user"]


async def test_double_failure_gives_up_for_human_review():
    llm = MockLLM()
    llm.enqueue(BAD_FILL)
    llm.enqueue(BAD_FILL)
    result = await generate_copy(
        llm=llm, brief=BRIEF, skeleton=SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert not result.ok
    assert result.attempts == 2
    assert result.validation is not None and result.validation.errors
