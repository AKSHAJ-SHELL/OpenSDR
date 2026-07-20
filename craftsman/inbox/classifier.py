"""Reply Classifier: one enum-constrained Sonnet call per inbound reply.

confidence < 0.7 → human review queue instead of acting.
The system NEVER auto-replies to a human who engaged.
"""

from craftsman.core.schemas import ReplyClassification
from craftsman.llm.client import LLMClient

CLASSIFIER_SYSTEM = """You classify replies to cold sales emails. Choose exactly one label:

- interested: any genuine buying signal, question about the product, or meeting openness.
- objection: engaged but pushing back (price, timing of budget, "we use X already").
- not_now: polite deferral without engagement ("not a priority this quarter").
- ooo: auto-reply out-of-office. Extract the return date into ooo_return_date if stated.
- unsubscribe: any request to stop emailing, however phrased. When in doubt between
  unsubscribe and not_now, choose unsubscribe — over-suppressing is the safe error.
- bounce_or_auto: delivery failure notices, mailer-daemon, or non-OOO auto-responses.

Adversarial cases to get right:
- An OOO that ALSO says "this looks interesting, ping me when I'm back" is ooo (the
  human hasn't engaged yet), but mention it via confidence < 0.7 so a human reviews it.
- A polite "please remove me from your list, but good luck!" is unsubscribe, not not_now.
- "Talk to my colleague X" with contact info is interested.

Set confidence honestly. If two labels are plausible, confidence must be below 0.7."""


async def classify_reply(llm: LLMClient, reply_text: str) -> ReplyClassification:
    return await llm.structured(
        system=CLASSIFIER_SYSTEM,
        user=f"Reply to classify:\n\n{reply_text}",
        schema=ReplyClassification,
        max_tokens=200,
        temperature=0.0,
    )
