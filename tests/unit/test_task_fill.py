"""M3.2/M3.3: assisted-channel copywriter + validator — the same four gates, with
channel caps (⛔ Gate M3 approved: LinkedIn ≤ 280 chars rendered; call brief
25/20/40 word caps, no grade check on fragments)."""

from craftsman.copywriter.task_fill import (
    DEFAULT_LINKEDIN_SKELETON,
    generate_call_brief,
    generate_linkedin_copy,
)
from craftsman.copywriter.validator import validate_task_fill
from craftsman.core.schemas import CallBrief, LinkedInSlotFill, ResearchBrief, TriggerEvent
from craftsman.llm.mock_impl import MockLLM

BRIEF = ResearchBrief(
    what_they_do="Acme Robotics builds warehouse automation robots.",
    industry="logistics",
    trigger_events=[
        TriggerEvent(
            claim="Acme Robotics raised $4M and opened a facility in Austin",
            source_url="https://acme.com/news",
            approx_date="2026-05",
        )
    ],
    likely_pain_points=["manual picking costs"],
    evidence_quotes=["opened our new Austin facility"],
)
PERSONA = {"name": "Sam Rivera", "title": "Founder", "company": "Flowbot"}
VALUE_PROP = "Flowbot cuts warehouse picking costs by a third."
GROUNDING = [BRIEF.model_dump(), {"value_prop": VALUE_PROP}, PERSONA]

GOOD_LI = LinkedInSlotFill(
    personalization_hook="saw Acme Robotics opened a new site in Austin.",
    value_bridge="Flowbot cuts picking costs by a third.",
    cta_question="Worth connecting?",
)

BAD_LI = LinkedInSlotFill(
    personalization_hook="congrats on the $40M raise from Sequoia.",
    value_bridge="Flowbot cuts warehouse picking costs by a third.",
    cta_question="Worth connecting?",
)


# ---------------------------------------------------------------- validator gates


def test_char_cap_boundary():
    slots = {"a": "grounded text"}
    at_cap = validate_task_fill(
        slots=slots, rendered_text="x" * 280, grounding_sources=GROUNDING,
        max_chars=280, check_grade=False,
    )
    assert at_cap.ok
    over = validate_task_fill(
        slots=slots, rendered_text="x" * 281, grounding_sources=GROUNDING,
        max_chars=280, check_grade=False,
    )
    assert not over.ok
    assert any("281 chars" in e for e in over.errors)


def test_magnitude_attack_rejected_in_note():
    """$4M in the brief, $40M in the note — numbers are never fuzzy-matched."""
    r = validate_task_fill(
        slots={"hook": "congrats on the $40M raise"},
        rendered_text="congrats on the $40M raise",
        grounding_sources=GROUNDING,
        max_chars=280, check_grade=False,
    )
    assert not r.ok
    assert any("$40M" in e for e in r.errors)


def test_exact_number_after_normalization_passes():
    r = validate_task_fill(
        slots={"hook": "congrats on the $4,000,000 raise for Acme Robotics"},
        rendered_text="x",
        grounding_sources=GROUNDING,
        check_grade=False,
    )
    assert r.ok, r.errors


def test_ungrounded_entity_rejected():
    r = validate_task_fill(
        slots={"hook": "loved your talk at Dreamforce"},
        rendered_text="x",
        grounding_sources=GROUNDING,
        check_grade=False,
    )
    assert not r.ok
    assert any("Dreamforce" in e for e in r.errors)


def test_banned_phrase_and_em_dash_rejected():
    r = validate_task_fill(
        slots={"hook": "just checking in"},
        rendered_text="also — this has an em-dash",
        grounding_sources=GROUNDING,
        check_grade=False,
    )
    assert not r.ok
    assert any("banned phrase" in e for e in r.errors)
    assert any("em-dash" in e for e in r.errors)


def test_per_slot_word_caps():
    ok = validate_task_fill(
        slots={"opener": "one two three"},
        rendered_text="one two three",
        grounding_sources=GROUNDING,
        per_slot_word_caps={"opener": 3},
        check_grade=False,
    )
    assert ok.ok
    over = validate_task_fill(
        slots={"opener": "one two three four"},
        rendered_text="one two three four",
        grounding_sources=GROUNDING,
        per_slot_word_caps={"opener": 3},
        check_grade=False,
    )
    assert not over.ok
    assert any("4 words (max 3)" in e for e in over.errors)


# ---------------------------------------------------------------- linkedin fill


async def test_good_linkedin_fill_passes():
    llm = MockLLM()
    llm.enqueue(GOOD_LI)
    result = await generate_linkedin_copy(
        llm=llm, brief=BRIEF, skeleton=DEFAULT_LINKEDIN_SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert result.ok
    assert result.attempts == 1
    assert result.payload["message"].startswith("Hi Dana, saw Acme Robotics opened a new site")
    assert result.payload["char_count"] <= 280
    assert result.payload["slots"]["cta_question"] == "Worth connecting?"


async def test_hallucinated_linkedin_fill_retries_then_passes():
    llm = MockLLM()
    llm.enqueue(BAD_LI)
    llm.enqueue(GOOD_LI)
    result = await generate_linkedin_copy(
        llm=llm, brief=BRIEF, skeleton=DEFAULT_LINKEDIN_SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert result.ok
    assert result.attempts == 2
    # the retry prompt carried the validator errors
    assert "REJECTED BY THE VALIDATOR" in llm.calls[1]["user"]
    assert "$40M" in llm.calls[1]["user"]


async def test_double_failure_goes_to_review():
    llm = MockLLM()
    llm.enqueue(BAD_LI)
    llm.enqueue(BAD_LI)
    result = await generate_linkedin_copy(
        llm=llm, brief=BRIEF, skeleton=DEFAULT_LINKEDIN_SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert not result.ok
    assert result.attempts == 2
    assert result.validation is not None and result.validation.errors


async def test_overlong_note_rejected():
    llm = MockLLM()
    long_fill = LinkedInSlotFill(
        personalization_hook=("saw Acme Robotics opened a facility in Austin. " * 4).strip(),
        value_bridge=("Flowbot cuts warehouse picking costs by a third. " * 3).strip(),
        cta_question="Worth connecting about warehouse automation robots for Acme Robotics?",
    )
    llm.enqueue(long_fill)
    llm.enqueue(long_fill)
    result = await generate_linkedin_copy(
        llm=llm, brief=BRIEF, skeleton=DEFAULT_LINKEDIN_SKELETON,
        value_prop=VALUE_PROP, persona=PERSONA, first_name="Dana",
    )
    assert not result.ok
    assert any("chars" in e for e in result.validation.errors)


# ---------------------------------------------------------------- call brief


GOOD_CALL = CallBrief(
    opener="Calling from Flowbot about warehouse picking costs at Acme Robotics.",
    pain_hypotheses=["manual picking costs may be rising with the new Austin facility"],
    objection_notes="If timing is bad, Flowbot cuts warehouse picking costs by a third.",
)


async def test_good_call_brief_passes():
    llm = MockLLM()
    llm.enqueue(GOOD_CALL)
    result = await generate_call_brief(
        llm=llm, brief=BRIEF, value_prop=VALUE_PROP, persona=PERSONA,
    )
    assert result.ok
    assert result.payload["brief"]["opener"].startswith("Calling from Flowbot")
    assert len(result.payload["brief"]["pain_hypotheses"]) == 1


async def test_call_brief_word_caps_enforced():
    llm = MockLLM()
    wordy = CallBrief(
        opener="Calling from Flowbot " + "about warehouse picking costs " * 7,  # 31 words > 25
        pain_hypotheses=["manual picking costs"],
        objection_notes="Flowbot cuts warehouse picking costs by a third.",
    )
    llm.enqueue(wordy)
    llm.enqueue(wordy)
    result = await generate_call_brief(
        llm=llm, brief=BRIEF, value_prop=VALUE_PROP, persona=PERSONA,
    )
    assert not result.ok
    assert any("opener" in e and "max 25" in e for e in result.validation.errors)


async def test_ungrounded_exec_name_in_brief_rejected():
    llm = MockLLM()
    invented = CallBrief(
        opener="Calling for Marcus Thornfield about warehouse picking costs.",
        pain_hypotheses=["manual picking costs"],
        objection_notes="Flowbot cuts warehouse picking costs by a third.",
    )
    llm.enqueue(invented)
    llm.enqueue(invented)
    result = await generate_call_brief(
        llm=llm, brief=BRIEF, value_prop=VALUE_PROP, persona=PERSONA,
    )
    assert not result.ok
    assert any("Marcus Thornfield" in e for e in result.validation.errors)
