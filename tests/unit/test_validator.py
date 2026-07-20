from craftsman.core.schemas import ResearchBrief, TriggerEvent
from craftsman.copywriter.validator import extract_claims, validate_fill

BRIEF = ResearchBrief(
    what_they_do="Acme Robotics builds warehouse automation robots for mid-size 3PLs.",
    industry="logistics automation",
    trigger_events=[
        TriggerEvent(
            claim="Acme Robotics opened a new facility in Austin",
            source_url="https://acmerobotics.com/news",
            approx_date="2026-05",
        )
    ],
    likely_pain_points=["manual picking costs", "labor shortages"],
    evidence_quotes=["We just opened our new Austin facility to serve Texas customers."],
)

CAMPAIGN = {"value_prop": "Flowbot cuts warehouse picking costs by a third."}


def _validate(slots, body=None):
    body_text = body or " ".join(slots.values())
    return validate_fill(
        slots=slots,
        subject_slot="subject_hook",
        body_text=body_text,
        grounding_sources=[BRIEF.model_dump(), CAMPAIGN, {"first_name": "Dana"}],
    )


def test_grounded_fill_passes():
    slots = {
        "subject_hook": "your new Austin facility",
        "personalization_sentence": "Saw that Acme Robotics opened a new facility in Austin.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert result.ok, result.errors


def test_hallucinated_proper_noun_rejected():
    slots = {
        "subject_hook": "your Series B",
        "personalization_sentence": "Congrats on the funding round led by Sequoia.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert not result.ok
    assert any("Sequoia" in e for e in result.errors)


def test_hallucinated_number_rejected():
    slots = {
        "subject_hook": "cutting costs in Austin",
        "personalization_sentence": "Acme Robotics grew 400% last year.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert not result.ok
    assert any("400" in e for e in result.errors)


def test_banned_phrase_rejected():
    slots = {
        "subject_hook": "quick question",
        "personalization_sentence": "Saw the new Austin facility.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert not result.ok
    assert any("banned phrase" in e for e in result.errors)


def test_em_dash_rejected():
    slots = {
        "subject_hook": "your Austin facility",
        "personalization_sentence": "Acme Robotics is growing — the Austin facility shows it.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert not result.ok
    assert any("em-dash" in e for e in result.errors)


def test_subject_length_cap():
    slots = {
        "subject_hook": "a very long subject line about the new Austin facility opening",
        "personalization_sentence": "Saw the new Austin facility.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots)
    assert not result.ok
    assert any("subject" in e for e in result.errors)


def test_body_length_cap():
    long_body = "word " * 120
    slots = {
        "subject_hook": "your Austin facility",
        "personalization_sentence": "Saw the new Austin facility.",
        "value_prop_bridge": "Flowbot cuts warehouse picking costs by a third.",
        "cta_question": "Worth a look?",
    }
    result = _validate(slots, body=long_body)
    assert not result.ok
    assert any("body is" in e for e in result.errors)


def test_extract_claims_skips_sentence_initial_common_words():
    proper, numbers = extract_claims("The team at Acme Robotics grew. We saw 40% growth.")
    assert "Acme Robotics" in proper
    assert "The" not in proper and "We" not in proper
    assert "40%" in numbers
