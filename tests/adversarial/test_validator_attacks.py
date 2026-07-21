"""Adversarial validator suite — the full table from TESTING.md §3.1, predict-then-run.

Every test states its prediction (made before running) in a comment. Assertions encode
the behavior we verified; where prediction and desired behavior diverge outside the
M0.3 numeric scope, the case is a characterization and the gap is logged in
findings/04-validator.md — flagged, not fixed (working agreement §0).

Numeric rows assert DESIRED behavior — M0.3 makes them pass. Entity/casing/banned rows
characterize the unchanged fuzzy/banned paths.
"""

from craftsman.copywriter.validator import validate_fill

BRIEF = {
    "what_they_do": "Acme Corp raised $4M at $4.2M valuation, grew 12% in Q3 2025.",
    "evidence_quotes": [
        "Acme Corp raised a $4M Series A in March 2024.",
        "We serve 1,000 warehouses and grew 12% in Q3 2025.",
    ],
}


def _validate(text, body="plain body"):
    return validate_fill(
        slots={"s": text}, subject_slot="x", body_text=body, grounding_sources=[BRIEF]
    )


# ------------------------------------------------------- magnitude (must reject)


def test_magnitude_dollar_4m_vs_40m():
    # Predict: REJECT — one char apart but 10x the value; exact match catches it.
    assert not _validate("congrats on the $40M raise").ok


def test_magnitude_separator_1000_vs_10000():
    # Predict: REJECT — the historic partial_ratio hole, now closed.
    assert not _validate("serving 10,000 warehouses").ok


def test_silent_rounding_4_2m_vs_4m():
    # Predict: PASS — brief contains BOTH $4M and $4.2M, so '$4M' is genuinely
    # grounded here. The rounding attack is the reverse: fill $4M when the brief
    # has only $4.2M — covered in the unit suite; here we assert the honest corpus
    # read: both figures exist, both are usable.
    assert _validate("the $4M round").ok
    assert _validate("the $4.2M valuation").ok


def test_silent_rounding_rejected_when_only_precise_figure_exists():
    # Predict: REJECT — corpus with ONLY $4.2M; fill says $4M (rounded).
    brief = {"evidence_quotes": ["Raised $4.2M last spring."]}
    r = validate_fill(slots={"s": "the $4M raise"}, subject_slot="x",
                      body_text="b", grounding_sources=[brief])
    assert not r.ok


# ---------------------------------------------- normalization (must NOT reject)


def test_normalization_1000_no_separator_passes():
    # Predict: PASS — 1,000 == 1000 after normalization.
    assert _validate("1000 warehouses strong").ok


def test_q3_2025_vs_third_quarter_no_false_rejection():
    # Predict: PASS — "third quarter" has no digits and no capitalized tokens,
    # so nothing needs grounding.
    assert _validate("growth in the third quarter").ok


# --------------------------------------------------- entities (fuzzy, unchanged)


def test_entity_suffix_swap_acme_corp_vs_acme_group():
    # Predict: REJECT — partial_ratio("acme group", "...acme corp...") ≈ 84 < 90.
    assert not _validate("great work at Acme Group").ok


def test_entity_series_a_vs_series_b():
    # Predict: REJECT — partial_ratio("series b", "series a") = 87.5 < 90.
    assert not _validate("congrats on the Series B").ok


def test_entity_pluralization_anthropic_vs_anthropics():
    # Predict: PASS (wrong, but characterized) — partial_ratio("anthropics",
    # "anthropic") ≈ 95 ≥ 90, so pluralized entities slip the fuzzy gate.
    # KNOWN GAP — logged in findings/04; fixing means an entity-threshold or
    # token-boundary change, which is a knob decision outside M0.3.
    brief = {"evidence_quotes": ["Anthropic published the research."]}
    r = validate_fill(slots={"s": "the team at Anthropics"}, subject_slot="x",
                      body_text="b", grounding_sources=[brief])
    assert r.ok  # characterization of current behavior, not an endorsement


# ------------------------------------------------------- casing (characterized)


def test_lowercase_brands_are_invisible_to_the_gate():
    # Predict: PASS (uncaught) — _PROPER_RE only starts at [A-Z], so "iPhone",
    # "deepmind", "eBay" are never extracted as claims at all.
    # KNOWN GAP — logged in findings/04; out of M0.3 scope.
    assert _validate("using deepmind tech on iPhone and eBay").ok


def test_sentence_initial_common_noun_not_flagged():
    # Predict: PASS — single sentence-initial capitalized words are skipped by
    # design ("Warehouses are…" is not a claim).
    assert _validate("Warehouses are changing fast").ok


# ---------------------------------------------------------------- dates / units


def test_date_partial_2024_alone_passes():
    # Predict: PASS — "2024" appears in "March 2024"; the year is genuinely grounded.
    assert _validate("since your 2024 launch").ok


def test_unit_swap_12_percent_vs_12x():
    # Predict: PASS (accepted limitation) — "12x" extracts as bare 12 ('x' is not a
    # recognized suffix), and under the approved asymmetric rule a bare number may
    # ground against 12%. A strict-kinds rule would catch this at the cost of false
    # rejections. Logged in findings/04 as a residual gap of the approved design.
    assert _validate("we can get you 12x results").ok


def test_percent_hallucinated_value_rejected():
    # Predict: REJECT — 40% appears nowhere; percent needs an exact percent source.
    assert not _validate("grew 40% last year").ok


# ------------------------------------------------- banned phrases (characterized)


def test_banned_phrase_altered_case_caught():
    # Predict: REJECT — matching is on lowercased text.
    assert not _validate("QUICK QUESTION for you").ok


def test_banned_phrase_trailing_punctuation_caught():
    # Predict: REJECT — substring match ignores what follows.
    assert not _validate("quick question!").ok


def test_banned_phrase_embedded_double_space_not_caught():
    # Predict: PASS (uncaught) — "quick  question" defeats substring matching.
    # KNOWN GAP — logged in findings/04.
    assert _validate("quick  question for you").ok


def test_banned_phrase_unicode_lookalike_not_caught():
    # Predict: PASS (uncaught) — Cyrillic 'у' in "qуick question" defeats substring.
    # KNOWN GAP — logged in findings/04.
    assert _validate("qуick question for you").ok


def test_banned_near_variant_hope_this_email_finds_you_well():
    # Predict: PASS (uncaught) — the list has "i hope this email finds you well"
    # but not the I-less variant. KNOWN GAP — a phrase-list addition is a product
    # decision (the list is a knob); logged in findings/04.
    assert _validate("Hope this email finds you well!").ok


# ------------------------------------------------------ length / grade boundaries


def test_subject_exactly_seven_words_passes():
    # Predict: PASS — cap is "> 7", so exactly 7 is legal.
    r = validate_fill(slots={"s": "one two three four five six seven"},
                      subject_slot="s", body_text="short body",
                      grounding_sources=[BRIEF])
    assert not any("subject" in e for e in r.errors)


def test_subject_eight_words_rejected():
    # Predict: REJECT — one over the cap.
    r = validate_fill(slots={"s": "one two three four five six seven eight"},
                      subject_slot="s", body_text="short body",
                      grounding_sources=[BRIEF])
    assert any("subject" in e for e in r.errors)


def test_body_exactly_ninety_words_passes():
    # Predict: PASS — cap is "> 90".
    r = _validate("fine", body="word " * 90)
    assert not any("body is" in e for e in r.errors)


def test_body_ninety_one_words_rejected():
    # Predict: REJECT.
    r = _validate("fine", body="word " * 91)
    assert any("body is" in e for e in r.errors)


def test_body_with_url_counts_as_one_word():
    # Predict: PASS — str.split() treats a URL as a single token, so an 89-word
    # body plus a URL is 90 words exactly.
    r = _validate("fine", body=("word " * 89) + "https://example.com/a/very/long/path")
    assert not any("body is" in e for e in r.errors)


def test_em_dash_in_body_but_not_slots_not_flagged():
    # Predict: PASS (uncaught) — the em-dash check only scans the joined SLOTS,
    # not body_text; a dash arriving from the skeleton itself is invisible.
    # KNOWN GAP (minor: skeletons are human-authored) — logged in findings/04.
    r = _validate("fine", body="the skeleton text — with a dash")
    assert not any("em-dash" in e for e in r.errors)
