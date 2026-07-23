"""Unit tests for numeric normalization and exact-match grounding (M0.3, finding B1).

Numbers must never ride the fuzzy path: $4M == $4,000,000 != $40M != $4.2M.
"""

from decimal import Decimal

from craftsman.copywriter.validator import (
    _numeric_grounded,
    normalize_numeric,
    validate_fill,
)

# ---------------------------------------------------------------- normalization


def test_magnitude_suffixes_expand_exactly():
    assert normalize_numeric("$4M") == (Decimal(4_000_000), "currency")
    assert normalize_numeric("4k") == (Decimal(4_000), "plain")
    assert normalize_numeric("2B") == (Decimal(2_000_000_000), "plain")
    assert normalize_numeric("€3.5bn") == (Decimal("3500000000.0"), "currency")
    assert normalize_numeric("4 million") == (Decimal(4_000_000), "plain")
    assert normalize_numeric("1 thousand") == (Decimal(1_000), "plain")


def test_separators_and_symbols_strip():
    assert normalize_numeric("1,000") == (Decimal(1_000), "plain")
    assert normalize_numeric("$4,000,000") == (Decimal(4_000_000), "currency")
    assert normalize_numeric("£4M") == (Decimal(4_000_000), "currency")


def test_decimal_exactness_no_float_drift():
    value, _ = normalize_numeric("4.2M")
    assert value == Decimal("4200000")
    assert value != Decimal("4000000")  # silent rounding is a difference, not noise


def test_percent_kind():
    assert normalize_numeric("12%") == (Decimal(12), "percent")
    assert normalize_numeric("0.5%") == (Decimal("0.5"), "percent")


def test_unparseable_returns_none():
    assert normalize_numeric("twelve") is None
    assert normalize_numeric("") is None
    assert normalize_numeric("$") is None


# ---------------------------------------------------------------- matching rules


def _nums(*tokens):
    return [normalize_numeric(t) for t in tokens]


def test_currency_matches_currency_and_plain_of_equal_value():
    corpus = _nums("$4,000,000")
    assert _numeric_grounded(Decimal(4_000_000), "currency", corpus)
    corpus_plain = _nums("4M")
    assert _numeric_grounded(Decimal(4_000_000), "currency", corpus_plain)


def test_currency_rejects_different_value():
    corpus = _nums("$4M")
    assert not _numeric_grounded(Decimal(40_000_000), "currency", corpus)  # $40M
    assert not _numeric_grounded(Decimal(4_200_000), "currency", corpus)  # $4.2M rounding


def test_currency_symbols_are_value_interchangeable():
    # human decision 2026-07-21: £4M grounds against $4M (value match, symbol ignored)
    corpus = _nums("$4M")
    assert _numeric_grounded(*normalize_numeric("£4M"), corpus)


def test_percent_requires_percent_source():
    assert _numeric_grounded(Decimal(12), "percent", _nums("12%"))
    assert not _numeric_grounded(Decimal(12), "percent", _nums("12"))
    assert not _numeric_grounded(Decimal(12), "percent", _nums("$12"))


def test_bare_number_matches_any_kind_of_equal_value():
    assert _numeric_grounded(Decimal(12), "plain", _nums("12%"))
    assert _numeric_grounded(Decimal(12), "plain", _nums("$12"))
    assert _numeric_grounded(Decimal(12), "plain", _nums("12"))
    assert not _numeric_grounded(Decimal(12), "plain", _nums("120"))


def test_currency_does_not_match_percent_source():
    assert not _numeric_grounded(Decimal(12), "currency", _nums("12%"))


# ---------------------------------------------------------------- end to end


BRIEF = {"evidence_quotes": ["They raised $4M in 2024 and grew 12% serving 1,000 customers."]}


def _check(text):
    # body_text kept simple and low-grade so only the grounding gate is exercised
    return validate_fill(
        slots={"s": text}, subject_slot="x", body_text="plain body", grounding_sources=[BRIEF]
    )


def test_magnitude_hallucination_rejected_end_to_end():
    result = _check("congrats on the $40M raise")
    assert not result.ok
    assert any("$40M" in e and "never fuzzy-matched" in e for e in result.errors)


def test_separator_variation_passes_end_to_end():
    assert _check("serving 1000 customers").ok
    assert _check("serving 1,000 customers").ok


def test_equivalent_magnitude_forms_pass_end_to_end():
    assert _check("the $4,000,000 round").ok
    assert _check("the $4M round").ok


def test_ten_x_separator_attack_rejected_end_to_end():
    # the historic bug: partial_ratio("10,000", "...1,000...") >= 90
    result = _check("serving 10,000 customers")
    assert not result.ok
