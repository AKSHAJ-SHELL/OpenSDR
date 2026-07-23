"""Assisted-channel copywriter (M3.2/M3.3): LinkedIn notes and call briefs.

Same contract as the email copywriter: one structured LLM call fills typed slots,
the deterministic validator gates everything, a human performs the touch. The LLM
never writes a whole message and never acts — for these channels it doesn't even
send: a person does, off-platform.
"""

from dataclasses import dataclass
from pathlib import Path

from craftsman.core.config import get_settings
from craftsman.core.schemas import CallBrief, LinkedInSlotFill, ResearchBrief
from craftsman.copywriter.fill import render_skeleton
from craftsman.copywriter.validator import ValidationResult, validate_task_fill
from craftsman.llm.client import LLMClient

DEFAULT_LINKEDIN_SKELETON = (
    Path(__file__).parent / "skeletons" / "linkedin_connection.txt"
).read_text().strip()

LINKEDIN_SYSTEM = """You write three short slots for a LinkedIn connection note. You are \
NOT writing the note — only the slots. Rules:

1. Use ONLY facts present in the RESEARCH BRIEF and CAMPAIGN sections below. Every \
company name, person name, product, or number you write must appear there verbatim. \
If the brief is thin, be generic rather than specific — never invent.
2. personalization_hook: ONE short clause tying a specific fact from the brief to the \
recipient. Plain words.
3. value_bridge: ONE short clause connecting their situation to the value prop.
4. cta_question: ONE tiny ask (worth connecting? open to compare notes?). Never ask \
for a meeting in a connection note.
5. The whole rendered note must fit a LinkedIn connection request (under 280 \
characters), so keep every slot tight. No em-dashes. No exclamation marks. No \
"I hope", no flattery. Write like a busy person, not a marketer."""

CALL_SYSTEM = """You prepare a short call brief for a human SDR. You are NOT writing a \
script — no fake rapport, no invented smalltalk. Rules:

1. Use ONLY facts present in the RESEARCH BRIEF and CAMPAIGN sections below. Every \
company name, person name, product, or number must appear there verbatim. If the \
brief is thin, be generic rather than specific — never invent.
2. opener: one sentence the caller can open with — who we are and the grounded reason \
for calling (max 25 words).
3. pain_hypotheses: up to TWO hypotheses about their pain, each drawn from the brief \
(max 20 words each). Hypotheses, not claims — phrase them as such.
4. objection_notes: what to say if they push back, grounded in the campaign value \
prop (max 40 words).
5. No em-dashes. No exclamation marks. Plain words a person can actually say."""


@dataclass
class TaskCopyResult:
    ok: bool
    payload: dict | None = None
    slots: dict | None = None
    validation: ValidationResult | None = None
    attempts: int = 0


def _context_prompt(brief: ResearchBrief, value_prop: str, persona: dict, extra_errors: list[str] | None, ask: str) -> str:
    parts = [
        "=== RESEARCH BRIEF (your only source of facts about the recipient) ===",
        brief.model_dump_json(indent=2),
        "",
        "=== CAMPAIGN ===",
        f"value_prop: {value_prop}",
        f"sender: {persona}",
        "",
        ask,
    ]
    if extra_errors:
        parts += [
            "",
            "=== YOUR PREVIOUS ATTEMPT WAS REJECTED BY THE VALIDATOR ===",
            *[f"- {e}" for e in extra_errors],
            "Fix every error. Remove any ungrounded claim rather than defending it.",
        ]
    return "\n".join(parts)


async def generate_linkedin_copy(
    *,
    llm: LLMClient,
    brief: ResearchBrief,
    skeleton: str,
    value_prop: str,
    persona: dict,
    first_name: str,
    max_attempts: int = 2,
) -> TaskCopyResult:
    """Fill → render → validate (grounding, banned phrases, ≤ LINKEDIN_NOTE_MAX_CHARS,
    grade) → retry once with errors appended → give up for human review."""
    settings = get_settings()
    static = {"first_name": first_name or "there"}
    errors: list[str] | None = None
    slots: dict = {}
    result: ValidationResult | None = None

    for attempt in range(1, max_attempts + 1):
        fill = await llm.structured(
            system=LINKEDIN_SYSTEM,
            user=_context_prompt(brief, value_prop, persona, errors, "Fill the three slots now."),
            schema=LinkedInSlotFill,
            max_tokens=300,
            temperature=0.7,
        )
        slots = fill.model_dump()
        rendered = render_skeleton(skeleton, slots, static)

        result = validate_task_fill(
            slots=slots,
            rendered_text=rendered,
            grounding_sources=[
                brief.model_dump(),
                {"value_prop": value_prop},
                persona,
                {"first_name": first_name},
            ],
            max_chars=settings.linkedin_note_max_chars,
            check_grade=True,
        )
        if result.ok:
            return TaskCopyResult(
                ok=True,
                payload={"message": rendered, "char_count": len(rendered), "slots": slots},
                slots=slots,
                validation=result,
                attempts=attempt,
            )
        errors = result.errors

    return TaskCopyResult(ok=False, slots=slots, validation=result, attempts=max_attempts)


async def generate_call_brief(
    *,
    llm: LLMClient,
    brief: ResearchBrief,
    value_prop: str,
    persona: dict,
    max_attempts: int = 2,
) -> TaskCopyResult:
    """Structured call brief → validate (grounding, banned phrases, per-field word
    caps; no grade — fragments) → retry once → give up for human review."""
    settings = get_settings()
    errors: list[str] | None = None
    slots: dict = {}
    result: ValidationResult | None = None

    for attempt in range(1, max_attempts + 1):
        fill = await llm.structured(
            system=CALL_SYSTEM,
            user=_context_prompt(brief, value_prop, persona, errors, "Write the call brief now."),
            schema=CallBrief,
            max_tokens=400,
            temperature=0.7,
        )
        brief_out = fill.model_dump()
        # flatten for the validator: each pain hypothesis is its own slot
        slots = {"opener": brief_out["opener"], "objection_notes": brief_out["objection_notes"]}
        caps = {
            "opener": settings.call_opener_max_words,
            "objection_notes": settings.call_objection_max_words,
        }
        for i, pain in enumerate(brief_out["pain_hypotheses"], start=1):
            slots[f"pain_hypothesis_{i}"] = pain
            caps[f"pain_hypothesis_{i}"] = settings.call_pain_max_words

        result = validate_task_fill(
            slots=slots,
            rendered_text=" ".join(slots.values()),
            grounding_sources=[
                brief.model_dump(),
                {"value_prop": value_prop},
                persona,
            ],
            per_slot_word_caps=caps,
            check_grade=False,
        )
        if result.ok:
            return TaskCopyResult(
                ok=True,
                payload={"brief": brief_out},
                slots=slots,
                validation=result,
                attempts=attempt,
            )
        errors = result.errors

    return TaskCopyResult(ok=False, slots=slots, validation=result, attempts=max_attempts)
