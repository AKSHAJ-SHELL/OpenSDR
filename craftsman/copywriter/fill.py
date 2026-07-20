"""Copywriter: one structured LLM call fills typed slots in a fixed skeleton.

The LLM never writes a whole email. Inputs are strictly limited to the
ResearchBrief, the campaign value_prop/persona, and slot constraints.
"""

import re
from dataclasses import dataclass

from craftsman.core.schemas import ResearchBrief, SlotFill
from craftsman.llm.client import LLMClient
from craftsman.copywriter.validator import ValidationResult, validate_fill

COPYWRITER_SYSTEM = """You write four short slots for a cold email skeleton. You are NOT \
writing an email — only the slots. Rules:

1. Use ONLY facts present in the RESEARCH BRIEF and CAMPAIGN sections below. Every \
company name, person name, product, or number you write must appear there verbatim. \
If the brief is thin, be generic rather than specific — never invent.
2. subject_hook: max 7 words, lowercase except proper nouns, no clickbait, no colons.
3. personalization_sentence: ONE sentence tying a specific fact from the brief to the \
recipient. Plain words, grade-8 reading level.
4. value_prop_bridge: ONE sentence connecting their situation to the value prop.
5. cta_question: ONE short question with a tiny ask (worth a look? open to a quick \
comparison?). Never ask for "30 minutes".
6. Total across all slots under 80 words. No em-dashes. No exclamation marks. Write \
like a busy person, not a marketer."""


def render_skeleton(skeleton: str, slots: dict[str, str], static: dict[str, str]) -> str:
    """Fill {{slot}} placeholders from LLM slots + static values (first_name, signature)."""
    out = skeleton
    for key, value in {**static, **slots}.items():
        out = out.replace("{{" + key + "}}", value)
    leftover = re.findall(r"{{(\w+)}}", out)
    if leftover:
        raise ValueError(f"unfilled skeleton slots: {leftover}")
    return out


def split_subject_body(rendered: str) -> tuple[str, str]:
    lines = rendered.splitlines()
    subject = ""
    body_lines = []
    for line in lines:
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
        else:
            body_lines.append(line)
    return subject, "\n".join(body_lines).strip()


@dataclass
class CopyResult:
    ok: bool
    subject: str = ""
    body: str = ""
    slots: dict | None = None
    validation: ValidationResult | None = None
    attempts: int = 0


def _user_prompt(brief: ResearchBrief, value_prop: str, persona: dict, extra_errors: list[str] | None) -> str:
    parts = [
        "=== RESEARCH BRIEF (your only source of facts about the recipient) ===",
        brief.model_dump_json(indent=2),
        "",
        "=== CAMPAIGN ===",
        f"value_prop: {value_prop}",
        f"sender: {persona}",
        "",
        "Fill the four slots now.",
    ]
    if extra_errors:
        parts += [
            "",
            "=== YOUR PREVIOUS ATTEMPT WAS REJECTED BY THE VALIDATOR ===",
            *[f"- {e}" for e in extra_errors],
            "Fix every error. Remove any ungrounded claim rather than defending it.",
        ]
    return "\n".join(parts)


async def generate_copy(
    *,
    llm: LLMClient,
    brief: ResearchBrief,
    skeleton: str,
    value_prop: str,
    persona: dict,
    first_name: str,
    max_attempts: int = 2,
) -> CopyResult:
    """Fill → validate → retry once with errors appended → give up for human review."""
    signature = _signature(persona)
    static = {"first_name": first_name or "there", "signature": signature}
    errors: list[str] | None = None

    for attempt in range(1, max_attempts + 1):
        fill = await llm.structured(
            system=COPYWRITER_SYSTEM,
            user=_user_prompt(brief, value_prop, persona, errors),
            schema=SlotFill,
            max_tokens=400,
            temperature=0.7,
        )
        slots = fill.model_dump()
        rendered = render_skeleton(skeleton, slots, static)
        subject, body = split_subject_body(rendered)

        result = validate_fill(
            slots=slots,
            subject_slot="subject_hook",
            body_text=body,
            grounding_sources=[
                brief.model_dump(),
                {"value_prop": value_prop},
                persona,
                {"first_name": first_name},
            ],
        )
        if result.ok:
            return CopyResult(
                ok=True, subject=subject, body=body, slots=slots,
                validation=result, attempts=attempt,
            )
        errors = result.errors

    return CopyResult(ok=False, slots=slots, validation=result, attempts=max_attempts)


def _signature(persona: dict) -> str:
    lines = [persona.get("name", ""), persona.get("title", ""), persona.get("company", "")]
    if persona.get("calendly"):
        lines.append(persona["calendly"])
    return "\n".join(x for x in lines if x)
