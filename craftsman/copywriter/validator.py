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
_COMMITMENT_PATH = Path(__file__).parent / "commitment_terms.txt"


def load_banned_phrases() -> list[str]:
    phrases = []
    for line in _BANNED_PATH.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            phrases.append(line)
    return phrases


def load_commitment_terms() -> list[str]:
    """Terms implying a commercial/legal commitment (M4.1) — same file format as
    banned phrases; see commitment_terms.txt for the gate's semantics."""
    terms = []
    for line in _COMMITMENT_PATH.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


BANNED_PHRASES = load_banned_phrases()
COMMITMENT_TERMS = load_commitment_terms()

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


def validate_reply_fill(
    *,
    slots: dict[str, str],
    rendered_body: str,
    grounding_sources: list,
    reply_text: str,
    campaign_sources: list,
    max_words: int,
) -> ValidationResult:
    """The gates for reply drafts (M4.1). `validate_fill` (email) is untouched.

    `grounding_sources` are the TRUSTED sources (research brief, campaign config,
    persona, lead fields); `reply_text` is the prospect's reply — it joins the
    grounding corpus so the draft may reference what they actually said (and only
    that), but it can never license a commitment. On top of the four inherited
    gates, the commitment gate — a draft must not INTRODUCE commitments, fail-closed:

    - commitment terms (commitment_terms.txt) appearing in the draft must also appear
      in the campaign's own config (value_prop / persona) — grounding anywhere else
      is NOT enough ("what's your pricing?" doesn't license a pricing promise);
    - currency amounts must ground in a TRUSTED source. A brief-vetted fact
      ("congrats on the $4M raise") passes; a prospect's "can you do $500?" can
      never be echoed back as an offer on reply-text grounding alone.
    """
    errors: list[str] = []
    trusted_corpus: list[str] = []
    for src in grounding_sources:
        trusted_corpus.extend(_collect_strings(src))
    corpus = trusted_corpus + ([reply_text] if reply_text else [])
    campaign_corpus: list[str] = []
    for src in campaign_sources:
        campaign_corpus.extend(_collect_strings(src))

    # 1. grounding — entities fuzzy, numbers exact, over the full corpus (reply incl.)
    corpus_numerics = _corpus_numerics(corpus)
    trusted_numerics = _corpus_numerics(trusted_corpus)
    for slot_name, value in slots.items():
        proper, numbers = extract_claims(value)
        for claim in proper:
            if not _grounded(claim, corpus):
                errors.append(
                    f"slot '{slot_name}': claim '{claim}' not found in research brief, "
                    f"campaign config, or the prospect's reply — remove it or replace "
                    f"with grounded fact"
                )
        for token in numbers:
            norm = normalize_numeric(token)
            if norm is None or not _numeric_grounded(norm[0], norm[1], corpus_numerics):
                errors.append(
                    f"slot '{slot_name}': number '{token}' has no exact match in the "
                    f"grounding corpus — numbers are never fuzzy-matched"
                )
            elif norm[1] == "currency" and not _numeric_grounded(
                norm[0], norm[1], trusted_numerics
            ):
                errors.append(
                    f"slot '{slot_name}': currency amount '{token}' grounds only in "
                    f"the prospect's reply — a draft may never echo a price back as "
                    f"an offer"
                )

    # 1b. commitment terms — campaign config is the only license
    joined = (" ".join(slots.values()) + " " + rendered_body).lower()
    campaign_joined = " ".join(campaign_corpus).lower()
    for term in COMMITMENT_TERMS:
        if term in joined and term not in campaign_joined:
            errors.append(
                f"commitment term '{term}' is not in the campaign config — a reply "
                f"may not introduce commitments the operator didn't write down"
            )

    # 2. banned phrases + em-dash spam
    for phrase in BANNED_PHRASES:
        if phrase in joined:
            errors.append(f"banned phrase: '{phrase}'")
    if "—" in joined or "–" in joined:
        errors.append("em-dash/en-dash detected — rewrite without it")

    # 3. length cap — replies stay short (⛔ Gate M4 default 120 words rendered)
    body_words = len(rendered_body.split())
    if body_words > max_words:
        errors.append(f"reply is {body_words} words (max {max_words})")

    # 4. reading grade
    if rendered_body.strip():
        grade = textstat.flesch_kincaid_grade(rendered_body)
        if grade > MAX_READING_GRADE:
            errors.append(
                f"reading grade {grade:.1f} (max {MAX_READING_GRADE:.0f}) — use shorter words and sentences"
            )

    return ValidationResult(ok=not errors, errors=errors)
