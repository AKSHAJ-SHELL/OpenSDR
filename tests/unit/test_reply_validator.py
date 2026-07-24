"""M4.1: validate_reply_fill — the reply-specific gates.

Boundary cases per the plan: word cap at/over, commitment terms in/out of campaign
config, currency held to the trusted-source standard (a prospect's reply can never
license a price), grounding against the inbound reply text, banned phrases, grade.
"""

from craftsman.copywriter.validator import validate_reply_fill

BRIEF = {
    "what_they_do": "Acme Robotics builds warehouse automation robots.",
    "trigger_events": [
        {"claim": "Acme Robotics raised $4M", "source_url": "u", "approx_date": "2026"}
    ],
    "likely_pain_points": ["manual picking costs"],
}
CAMPAIGN = {"value_prop": "Flowbot cuts picking costs by a third."}
PERSONA = {"name": "Sam", "company": "Flowbot"}
REPLY = "Sounds interesting. How does this work with Zapier? We use it a lot."


def _validate(slots, rendered=None, campaign=CAMPAIGN, max_words=120, reply=None):
    return validate_reply_fill(
        slots=slots,
        rendered_body=rendered if rendered is not None else " ".join(slots.values()),
        grounding_sources=[BRIEF, campaign, PERSONA],
        reply_text=reply if reply is not None else REPLY,
        campaign_sources=[campaign, PERSONA],
        max_words=max_words,
    )


def test_grounded_reply_slots_pass():
    r = _validate({
        "acknowledgment": "You asked how this works with Zapier.",
        "answer_bridge": "Flowbot cuts picking costs by a third.",
        "cta_question": "Worth a quick look?",
    })
    assert r.ok, r.errors


def test_reply_text_is_legitimate_grounding():
    # "Zapier" appears only in the prospect's reply — that IS allowed
    r = _validate({"acknowledgment": "You asked if this works with Zapier."})
    assert r.ok, r.errors


def test_ungrounded_entity_rejects():
    r = _validate({"answer_bridge": "Our customer DataDog saw great results."})
    assert not r.ok
    assert any("DataDog" in e for e in r.errors)


def test_ungrounded_number_rejects():
    r = _validate({"answer_bridge": "We cut costs by 47%."})
    assert not r.ok
    assert any("47%" in e for e in r.errors)


def test_magnitude_drift_rejects():
    # $4M is in the brief; $40M is not — never fuzzy-match numbers
    r = _validate({"acknowledgment": "Congrats on raising $40M."})
    assert not r.ok


def test_grounded_brief_number_passes():
    r = _validate({"acknowledgment": "Congrats on raising $4M."})
    assert r.ok, r.errors  # brief-grounded currency is trusted (see below)


# ---------------------------------------------------------------- commitment gate


def test_commitment_term_not_in_campaign_rejects():
    r = _validate({"answer_bridge": "We can offer a discount if timing is the issue."})
    assert not r.ok
    assert any("commitment term 'discount'" in e for e in r.errors)


def test_commitment_term_in_campaign_config_passes():
    campaign = {"value_prop": "Flowbot cuts picking costs by a third. Flat pricing, no contract."}
    r = _validate(
        {"answer_bridge": "There is no contract to sign."},
        campaign=campaign,
    )
    assert r.ok, r.errors


def test_prospect_currency_cannot_be_echoed():
    # the prospect names a price; echoing it back would read as an offer —
    # currency must ground in a TRUSTED source, never the reply alone
    r = _validate(
        {"answer_bridge": "Yes, $500 per month works."},
        reply="Can you do $500 per month?",
    )
    assert not r.ok
    assert any("currency amount" in e for e in r.errors)


def test_campaign_currency_is_licensed():
    campaign = {"value_prop": "Plans start at $500 per month."}
    r = _validate({"answer_bridge": "Plans start at $500 per month."}, campaign=campaign)
    assert r.ok, r.errors


def test_brief_currency_is_licensed():
    # a brief-vetted fact is not a commitment — same standard as the opener emails
    r = _validate({"acknowledgment": "Congrats on raising $4M."})
    assert r.ok, r.errors


# ---------------------------------------------------------------- caps + style


def test_word_cap_boundary():
    at_cap = "We can help with that soon."  # 6 words, grade-safe
    over_cap = "We can help with that very soon."  # 7 words
    assert _validate({"a": "ok"}, rendered=at_cap, max_words=6).ok
    r = _validate({"a": "ok"}, rendered=over_cap, max_words=6)
    assert not r.ok
    assert any("7 words (max 6)" in e for e in r.errors)


def test_banned_phrase_rejects():
    r = _validate({"cta_question": "Happy to circle back, worth a look?"})
    assert not r.ok
    assert any("banned phrase" in e for e in r.errors)


def test_em_dash_rejects():
    r = _validate({"answer_bridge": "It works well — really well."})
    assert not r.ok
    assert any("em-dash" in e for e in r.errors)


def test_reading_grade_gate():
    dense = (
        "Nevertheless, notwithstanding contemporaneous organizational considerations, "
        "our multidimensional operational infrastructure demonstrably facilitates "
        "extraordinarily consequential productivity enhancements."
    )
    r = _validate({"answer_bridge": dense}, rendered=dense)
    assert not r.ok
    assert any("reading grade" in e for e in r.errors)
