"""Unit tests for the erasure PII scrubber (M0.4)."""

from craftsman.compliance.suppression import _identifiers, scrub_pii
from craftsman.core.models import Lead

IDS = ["dana.lopez@acme.com", "Dana Lopez", "Dana", "Lopez"]


def test_scrub_replaces_name_and_email_case_insensitive():
    brief = {
        "what_they_do": "Acme builds robots. DANA LOPEZ leads ops.",
        "evidence_quotes": ["Contact dana.lopez@acme.com for a tour."],
    }
    scrubbed, changed = scrub_pii(brief, IDS)
    assert changed
    text = str(scrubbed)
    assert "DANA" not in text and "Dana" not in text and "Lopez" not in text
    assert "dana.lopez@acme.com" not in text
    assert "[redacted]" in text
    assert "Acme builds robots" in scrubbed["what_they_do"]  # company facts stay


def test_scrub_walks_nested_structures():
    brief = {
        "trigger_events": [
            {"claim": "Dana Lopez announced the Austin opening", "source_url": "https://x.co"}
        ],
        "likely_pain_points": [["nested", "Dana said costs hurt"]],
    }
    scrubbed, changed = scrub_pii(brief, IDS)
    assert changed
    assert "Dana" not in str(scrubbed)
    assert "Austin opening" in scrubbed["trigger_events"][0]["claim"]
    assert scrubbed["trigger_events"][0]["source_url"] == "https://x.co"


def test_scrub_untouched_brief_reports_unchanged():
    brief = {"what_they_do": "Acme builds robots.", "industry": "logistics"}
    scrubbed, changed = scrub_pii(brief, IDS)
    assert not changed
    assert scrubbed == brief


def test_scrub_non_string_values_pass_through():
    obj = {"count": 3, "score": 0.7, "flag": True, "nothing": None}
    scrubbed, changed = scrub_pii(obj, IDS)
    assert not changed
    assert scrubbed == obj


def test_identifiers_skip_short_names():
    lead = Lead(email="al.wu@x.co", first_name="Al", last_name="Wu")
    ids = _identifiers(lead)
    assert "al.wu@x.co" in ids
    assert "Al Wu" in ids  # full name is specific enough to keep
    assert "Al" not in ids and "Wu" not in ids  # initials-length names skipped


def test_identifiers_include_long_names_individually():
    lead = Lead(email="dana@x.co", first_name="Dana", last_name="Lopez")
    ids = _identifiers(lead)
    assert set(["dana@x.co", "Dana Lopez", "Dana", "Lopez"]) == set(ids)
