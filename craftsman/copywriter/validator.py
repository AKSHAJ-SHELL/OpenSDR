"""Deterministic validator — the anti-hallucination gate.

The LLM's slot fills pass through here before anything is sent. No model output
reaches an inbox without surviving these four checks.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import textstat
from rapidfuzz import fuzz

FUZZY_THRESHOLD = 90.0
MAX_SUBJECT_WORDS = 7
MAX_BODY_WORDS = 90
MAX_READING_GRADE = 8.0

_BANNED_PATH = Path(__file__).parent / "banned_phrases.txt"


def load_banned_phrases() -> list[str]:
    phrases = []
    for line in _BANNED_PATH.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            phrases.append(line)
    return phrases


BANNED_PHRASES = load_banned_phrases()

# Words that are capitalized mid-sentence but aren't claims about the world
_COMMON_CAPS = {
    "i", "i'm", "i'd", "i'll", "i've", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december",
}

_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?")
_PROPER_RE = re.compile(r"\b[A-Z][a-zA-Z0-9&.\-]*(?:\s+[A-Z][a-zA-Z0-9&.\-]*)*\b")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _collect_strings(obj) -> list[str]:
    """Flatten every string value out of a nested dict/list structure."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_collect_strings(v))
    return out


def extract_claims(text: str) -> tuple[list[str], list[str]]:
    """Return (proper_nouns, numbers) found in text.

    Proper nouns: capitalized token runs that are not sentence-initial common words.
    """
    numbers = _NUMBER_RE.findall(text)

    proper: list[str] = []
    for m in _PROPER_RE.finditer(text):
        candidate = m.group(0)
        # skip sentence-initial single common words ("The", "We", "Saw")
        start = m.start()
        sentence_initial = start == 0 or text[max(0, start - 2):start] in (". ", "! ", "? ", "\n", ": ")
        words = candidate.split()
        if sentence_initial and len(words) == 1:
            continue
        if candidate.lower() in _COMMON_CAPS:
            continue
        if len(words) == 1 and len(candidate) <= 2:  # "A", "So"
            continue
        proper.append(candidate)
    return proper, numbers


def _grounded(claim: str, corpus: list[str]) -> bool:
    claim_l = claim.lower()
    for source in corpus:
        if claim_l in source.lower():
            return True
        if fuzz.partial_ratio(claim_l, source.lower()) >= FUZZY_THRESHOLD:
            return True
    return False


def validate_fill(
    *,
    slots: dict[str, str],
    subject_slot: str,
    body_text: str,
    grounding_sources: list,
) -> ValidationResult:
    """Run all four gates over the filled slots.

    grounding_sources: any mix of dicts (ResearchBrief dump, campaign config,
    lead fields) and raw strings; every proper noun/number in the fill must
    fuzzy-match into this corpus.
    """
    errors: list[str] = []
    corpus: list[str] = []
    for src in grounding_sources:
        corpus.extend(_collect_strings(src))

    # 1. grounding: every proper noun / number must appear in the corpus
    for slot_name, value in slots.items():
        proper, numbers = extract_claims(value)
        for claim in proper + numbers:
            if not _grounded(claim, corpus):
                errors.append(
                    f"slot '{slot_name}': claim '{claim}' not found in research brief "
                    f"or campaign config — remove it or replace with grounded fact"
                )

    # 2. banned phrases + em-dash spam
    joined = " ".join(slots.values()).lower()
    for phrase in BANNED_PHRASES:
        if phrase in joined:
            errors.append(f"banned phrase: '{phrase}'")
    if "—" in joined or "–" in joined:
        errors.append("em-dash/en-dash detected — rewrite without it")

    # 3. length caps
    subject = slots.get(subject_slot, "")
    if len(subject.split()) > MAX_SUBJECT_WORDS:
        errors.append(f"subject is {len(subject.split())} words (max {MAX_SUBJECT_WORDS})")
    body_words = len(body_text.split())
    if body_words > MAX_BODY_WORDS:
        errors.append(f"body is {body_words} words (max {MAX_BODY_WORDS})")

    # 4. reading grade
    if body_text.strip():
        grade = textstat.flesch_kincaid_grade(body_text)
        if grade > MAX_READING_GRADE:
            errors.append(f"reading grade {grade:.1f} (max {MAX_READING_GRADE:.0f}) — use shorter words and sentences")

    return ValidationResult(ok=not errors, errors=errors)
