"""Deterministic validator — the anti-hallucination gate.

The LLM's slot fills pass through here before anything is sent. No model output
reaches an inbox without surviving these four checks.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import textstat
from rapidfuzz import fuzz

FUZZY_THRESHOLD = 90.0  # entities only — numbers use exact match after normalization
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

# Full numeric tokens: optional currency symbol, digits with separators/decimals,
# optional magnitude suffix (k/m/b/bn or thousand/million/billion), optional percent.
# The lookbehind stops mid-word digits ("Q3", "v2") from extracting as numbers; the
# lookahead stops "4kg" from reading as 4 thousand (the 'k' must not start a word).
_NUMERIC_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<cur>[$€£])?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:(?P<suf>bn|[kmb])(?![A-Za-z])|\s(?P<word>thousand|million|billion)\b)?"
    r"(?P<pct>%)?",
    re.IGNORECASE,
)
_PROPER_RE = re.compile(r"\b[A-Z][a-zA-Z0-9&.\-]*(?:\s+[A-Z][a-zA-Z0-9&.\-]*)*\b")

_MAGNITUDES = {
    "k": Decimal(1_000),
    "thousand": Decimal(1_000),
    "m": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
}


def normalize_numeric(token: str) -> tuple[Decimal, str] | None:
    """Canonicalize a numeric token to (value, kind).

    kind is one of 'plain' | 'percent' | 'currency'. Separators and currency symbols
    are stripped, magnitude suffixes expanded exactly via Decimal: $4M -> (4000000,
    'currency'); 1,000 -> (1000, 'plain'); 12% -> (12, 'percent'). Returns None for
    anything that doesn't parse — callers treat that as ungrounded (fail closed).
    """
    m = _NUMERIC_RE.fullmatch(token.strip())
    if m is None:
        return None
    try:
        value = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation:
        return None
    suffix = (m.group("suf") or m.group("word") or "").lower()
    if suffix:
        value *= _MAGNITUDES[suffix]
    if m.group("pct"):
        kind = "percent"
    elif m.group("cur"):
        kind = "currency"
    else:
        kind = "plain"
    return value, kind


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
    Numbers: full numeric tokens including currency symbol / magnitude suffix / percent.
    """
    numbers = [m.group(0) for m in _NUMERIC_RE.finditer(text)]

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


def _corpus_numerics(corpus: list[str]) -> list[tuple[Decimal, str]]:
    """Extract and normalize every numeric token in the grounding corpus."""
    out: list[tuple[Decimal, str]] = []
    for source in corpus:
        for m in _NUMERIC_RE.finditer(source):
            norm = normalize_numeric(m.group(0))
            if norm is not None:
                out.append(norm)
    return out


def _numeric_grounded(value: Decimal, kind: str, corpus_numerics: list[tuple[Decimal, str]]) -> bool:
    """Exact-match grounding for numbers.

    A percent claim needs a percent source. A currency claim matches a currency or
    plain source of equal value (symbols are unit-insensitive by design: £4M grounds
    against $4M). A bare number matches any source of equal value — its digits are
    genuinely present in the brief.
    """
    for cvalue, ckind in corpus_numerics:
        if cvalue != value:
            continue
        if kind == "percent" and ckind != "percent":
            continue
        if kind == "currency" and ckind == "percent":
            continue
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

    # 1. grounding — two paths:
    #    entities: fuzzy match (partial_ratio >= FUZZY_THRESHOLD), unchanged
    #    numbers:  exact match after normalization ($4M == $4,000,000 != $40M)
    corpus_numerics = _corpus_numerics(corpus)
    for slot_name, value in slots.items():
        proper, numbers = extract_claims(value)
        for claim in proper:
            if not _grounded(claim, corpus):
                errors.append(
                    f"slot '{slot_name}': claim '{claim}' not found in research brief "
                    f"or campaign config — remove it or replace with grounded fact"
                )
        for token in numbers:
            norm = normalize_numeric(token)
            if norm is None or not _numeric_grounded(norm[0], norm[1], corpus_numerics):
                if norm is None:
                    shown = "unparseable"
                else:
                    v = norm[0]
                    shown = f"{int(v):,}" if v == v.to_integral_value() else f"{v:,}"
                errors.append(
                    f"slot '{slot_name}': number '{token}' (= {shown}) has no exact "
                    f"match in the research brief or campaign config — numbers are "
                    f"never fuzzy-matched; remove it or use the grounded figure"
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


def validate_task_fill(
    *,
    slots: dict[str, str],
    rendered_text: str,
    grounding_sources: list,
    max_chars: int | None = None,
    per_slot_word_caps: dict[str, int] | None = None,
    check_grade: bool = True,
) -> ValidationResult:
    """The four gates for assisted-channel content (M3.2/M3.3) — same grounding
    corpus machinery, same banned phrases, channel-appropriate caps.

    LinkedIn notes: char cap on the rendered text (max_chars), grade check on.
    Call briefs: per-slot word caps, grade check off (structured fragments, not prose).
    `validate_fill` (email) is untouched — email caps are not renegotiated here.
    """
    errors: list[str] = []
    corpus: list[str] = []
    for src in grounding_sources:
        corpus.extend(_collect_strings(src))

    # 1. grounding — identical two-path policy: entities fuzzy, numbers exact
    corpus_numerics = _corpus_numerics(corpus)
    for slot_name, value in slots.items():
        proper, numbers = extract_claims(value)
        for claim in proper:
            if not _grounded(claim, corpus):
                errors.append(
                    f"slot '{slot_name}': claim '{claim}' not found in research brief "
                    f"or campaign config — remove it or replace with grounded fact"
                )
        for token in numbers:
            norm = normalize_numeric(token)
            if norm is None or not _numeric_grounded(norm[0], norm[1], corpus_numerics):
                errors.append(
                    f"slot '{slot_name}': number '{token}' has no exact match in the "
                    f"research brief or campaign config — numbers are never fuzzy-matched"
                )

    # 2. banned phrases + em-dash spam — checked over slots AND the rendered text
    joined = (" ".join(slots.values()) + " " + rendered_text).lower()
    for phrase in BANNED_PHRASES:
        if phrase in joined:
            errors.append(f"banned phrase: '{phrase}'")
    if "—" in joined or "–" in joined:
        errors.append("em-dash/en-dash detected — rewrite without it")

    # 3. caps — per channel
    if max_chars is not None and len(rendered_text) > max_chars:
        errors.append(
            f"rendered message is {len(rendered_text)} chars (max {max_chars})"
        )
    for slot_name, cap in (per_slot_word_caps or {}).items():
        words = len(slots.get(slot_name, "").split())
        if words > cap:
            errors.append(f"slot '{slot_name}' is {words} words (max {cap})")

    # 4. reading grade (LinkedIn prose only; briefs are fragments)
    if check_grade and rendered_text.strip():
        grade = textstat.flesch_kincaid_grade(rendered_text)
        if grade > MAX_READING_GRADE:
            errors.append(
                f"reading grade {grade:.1f} (max {MAX_READING_GRADE:.0f}) — use shorter words and sentences"
            )

    return ValidationResult(ok=not errors, errors=errors)
