"""Reply copywriter (M4.1 Copilot): drafts for interested/objection replies.

Same contract as every other copywriter in this repo: one structured LLM call fills
typed slots in a FIXED skeleton, a deterministic validator gates the result, and the
LLM never writes a whole message. Two things are reply-specific:

- the grounding corpus includes the prospect's reply text, so the draft may reference
  what they actually said — and only that;
- the LLM picks WHICH fixed skeleton fits an objection (timing / info / other) as an
  enum choice. `other` (pricing, competitor, hostile, anything without a
  deterministic resolution) deliberately produces NO draft — a human writes those.

Under Copilot a human click is the only path to dispatch. Under Guarded Autopilot
(M4.4, opt-in) the same skeletons and the same validator apply — autonomy never
relaxes a gate.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from craftsman.core.config import get_settings
from craftsman.core.schemas import ReplyDraftFill, ResearchBrief
from craftsman.copywriter.fill import _signature, render_skeleton
from craftsman.copywriter.validator import ValidationResult, validate_reply_fill
from craftsman.llm.client import LLMClient

_SKELETON_DIR = Path(__file__).parent / "skeletons"

REPLY_SKELETONS: dict[str, str] = {
    key: (_SKELETON_DIR / f"{key}.txt").read_text().strip()
    for key in ("reply_interested", "reply_objection_timing", "reply_objection_info")
}

REPLY_SYSTEM = """You write three short slots for a reply to a prospect who answered a \
cold email. You are NOT writing the reply — only the slots. Rules:

1. Use ONLY facts present in the RESEARCH BRIEF, CAMPAIGN, and THE PROSPECT'S REPLY \
sections below. Every company name, person name, product, or number you write must \
appear there verbatim. Never invent. Never promise anything (pricing, discounts, \
legal terms, guarantees) that is not already in the CAMPAIGN section.
2. objection_kind: for an objection reply, classify its deterministic resolution — \
"timing" (busy now / next quarter / circle back later), "info" (send me more \
information / a deck / details), or "other" (pricing, competitor, hostile, anything \
else). For an interested reply this field is ignored; set it to "other".
3. acknowledgment: ONE sentence showing you read their reply — reference what they \
actually said, in plain words. No flattery, no "great question".
4. answer_bridge: ONE sentence moving things forward using only grounded facts.
5. cta_question: ONE short question with a tiny ask. Never ask for "30 minutes".
6. Never convert word-form numbers into digits: if a source says "a third", write \
"a third", not "33%". A digit or percent you write must appear digit-for-digit in \
the sources.
7. Total across all slots under 60 words. No em-dashes. No exclamation marks. Write \
like a busy person, not a marketer."""


@dataclass
class ReplyCopyResult:
    ok: bool
    skeleton_key: str | None = None
    body: str = ""
    slots: dict | None = None
    validation: ValidationResult | None = None
    attempts: int = 0
    skipped_reason: str | None = None  # set when deliberately not drafting


def _skeleton_for(label: str, objection_kind: str) -> tuple[str | None, str | None]:
    """(skeleton_key, skipped_reason) — exactly one is set."""
    if label == "interested":
        return "reply_interested", None
    if label == "objection":
        if objection_kind == "timing":
            return "reply_objection_timing", None
        if objection_kind == "info":
            return "reply_objection_info", None
        return None, "objection_needs_human"
    return None, f"no_draft_for_label:{label}"


def _context_prompt(
    brief: ResearchBrief,
    value_prop: str,
    persona: dict,
    reply_text: str,
    label: str,
    errors: list[str] | None,
) -> str:
    parts = [
        "=== RESEARCH BRIEF (facts about the recipient) ===",
        brief.model_dump_json(indent=2),
        "",
        "=== CAMPAIGN (the only source of anything promise-shaped) ===",
        f"value_prop: {value_prop}",
        f"sender: {persona}",
        "",
        f"=== THE PROSPECT'S REPLY (classified: {label}) ===",
        reply_text,
        "",
        "Fill the slots for the reply now.",
    ]
    if errors:
        parts += [
            "",
            "=== YOUR PREVIOUS ATTEMPT WAS REJECTED BY THE VALIDATOR ===",
            *[f"- {e}" for e in errors],
            "Fix every error. Remove any ungrounded claim or commitment rather than defending it.",
        ]
    return "\n".join(parts)


def _tidy(rendered: str) -> str:
    """Collapse the blank runs left by empty static slots (scheduling/info lines)."""
    return re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"


async def generate_reply_copy(
    *,
    llm: LLMClient,
    brief: ResearchBrief,
    value_prop: str,
    persona: dict,
    first_name: str,
    reply_text: str,
    label: str,
    scheduling_line: str = "",
    info_line: str = "",
    max_attempts: int = 2,
) -> ReplyCopyResult:
    """Fill → select skeleton (enum) → render → validate (grounding incl. the reply
    text, commitment gate, ≤ REPLY_DRAFT_MAX_WORDS, grade) → retry once with errors
    appended → give up for human review. `scheduling_line` / `info_line` are static
    slots composed by the caller from campaign config (M4.3) — never LLM content."""
    settings = get_settings()
    static = {
        "first_name": first_name or "there",
        "signature": _signature(persona),
        "followup_weeks": str(settings.reply_followup_weeks),
        "scheduling_line": f"\n{scheduling_line}" if scheduling_line else "",
        "info_line": f"\n{info_line}" if info_line else "",
    }
    errors: list[str] | None = None
    slots: dict = {}
    result: ValidationResult | None = None
    skeleton_key: str | None = None

    for attempt in range(1, max_attempts + 1):
        fill = await llm.structured(
            system=REPLY_SYSTEM,
            user=_context_prompt(brief, value_prop, persona, reply_text, label, errors),
            schema=ReplyDraftFill,
            max_tokens=400,
            temperature=0.7,
        )
        skeleton_key, skipped = _skeleton_for(label, fill.objection_kind)
        if skeleton_key is None:
            return ReplyCopyResult(ok=False, skipped_reason=skipped, attempts=attempt)

        slots = {
            "acknowledgment": fill.acknowledgment,
            "answer_bridge": fill.answer_bridge,
            "cta_question": fill.cta_question,
        }
        rendered = _tidy(render_skeleton(REPLY_SKELETONS[skeleton_key], slots, static))

        result = validate_reply_fill(
            slots=slots,
            rendered_body=rendered,
            grounding_sources=[
                brief.model_dump(),
                {"value_prop": value_prop},
                persona,
                {"first_name": first_name},
            ],
            reply_text=reply_text,
            campaign_sources=[{"value_prop": value_prop}, persona],
            max_words=settings.reply_draft_max_words,
        )
        if result.ok:
            return ReplyCopyResult(
                ok=True,
                skeleton_key=skeleton_key,
                body=rendered,
                slots=slots,
                validation=result,
                attempts=attempt,
            )
        errors = result.errors

    return ReplyCopyResult(
        ok=False, skeleton_key=skeleton_key, slots=slots, validation=result,
        attempts=max_attempts,
    )
