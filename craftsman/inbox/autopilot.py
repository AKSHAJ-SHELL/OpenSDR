"""Guarded Autopilot (M4.4, ⛔ Gate M4 Option B — opt-in, off by default).

A pure policy engine deciding whether ONE validated Copilot draft may auto-send.
Everything about it is deliberately narrow:

- It only ever dispatches a draft that already exists — slot-filled into a fixed
  skeleton and passed by the same validator Copilot uses. Autonomy never relaxes a
  content gate; free-text replies remain impossible by construction.
- Only the three deterministic resolutions may auto-send: interested → scheduling
  link (requires campaign.scheduling_url), timing objection → follow-up offer,
  info objection → approved one-pager (requires campaign.info_doc_url). Everything
  else — pricing, competitor, hostile, ambiguous, 'other' — stays a pending draft
  for a human.
- **≤ 1 auto-reply per thread, ever — hardcoded, not a knob.** A reply to the
  auto-reply always escalates to a human; there are no AI↔human conversation loops.
- Confidence gate (⛔ AUTOPILOT_MIN_CONFIDENCE, default 0.9), escalation
  block_autopilot, and lead-local business hours all veto.
- Enabling requires an admin-scoped API call per campaign (deliberate friction);
  the kill switch (`POST /campaigns/{id}/autopilot/disable`) is operate-scoped —
  easier to stop than to start — and instant.

`decide()` returns the reason it refused, so every non-send is explainable in
logs and the audit trail.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from craftsman.core.config import get_settings

# The complete set of skeletons Autopilot may ever dispatch. Not configurable.
AUTOPILOT_SKELETONS = frozenset(
    {"reply_interested", "reply_objection_timing", "reply_objection_info"}
)

# Hardcoded invariant (not a knob — a knob invites raising it): one auto-reply
# per thread, ever.
MAX_AUTO_REPLIES_PER_THREAD = 1


@dataclass(frozen=True)
class AutopilotContext:
    autopilot_enabled: bool
    skeleton_key: str | None
    classification_confidence: float | None
    escalation_blocks: bool
    prior_auto_replies_in_thread: int
    scheduling_url: str | None
    info_doc_url: str | None
    now_utc: datetime
    lead_tz: str | None


@dataclass(frozen=True)
class AutopilotDecision:
    send: bool
    reason: str  # 'ok' when send=True, else the first failed gate


def within_send_window(now_utc: datetime, lead_tz: str | None) -> bool:
    """Lead-local business hours: same weekday + window knobs the sender uses."""
    settings = get_settings()
    try:
        tz = ZoneInfo(lead_tz or "America/Los_Angeles")
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    local = now_utc.astimezone(tz)
    if local.weekday() >= 5:
        return False
    start = local.replace(
        hour=settings.send_window_start_hour, minute=0, second=0, microsecond=0
    )
    end = local.replace(
        hour=settings.send_window_end_hour,
        minute=settings.send_window_end_minute,
        second=0,
        microsecond=0,
    )
    return start <= local <= end


def decide(ctx: AutopilotContext) -> AutopilotDecision:
    """ALL gates must pass. First refusal wins the reason string."""
    settings = get_settings()

    if not ctx.autopilot_enabled:
        return AutopilotDecision(False, "autopilot_disabled")
    if ctx.skeleton_key not in AUTOPILOT_SKELETONS:
        return AutopilotDecision(False, f"skeleton_not_deterministic:{ctx.skeleton_key}")
    if ctx.skeleton_key == "reply_interested" and not ctx.scheduling_url:
        return AutopilotDecision(False, "no_scheduling_url")
    if ctx.skeleton_key == "reply_objection_info" and not ctx.info_doc_url:
        return AutopilotDecision(False, "no_info_doc_url")
    if (
        ctx.classification_confidence is None
        or ctx.classification_confidence < settings.autopilot_min_confidence
    ):
        return AutopilotDecision(False, "confidence_below_threshold")
    if ctx.escalation_blocks:
        return AutopilotDecision(False, "escalation_block_autopilot")
    if ctx.prior_auto_replies_in_thread >= MAX_AUTO_REPLIES_PER_THREAD:
        # includes the reply-to-an-auto-reply case: any prior auto-send in the
        # thread means a human owns this conversation now
        return AutopilotDecision(False, "auto_reply_already_sent_in_thread")
    if not within_send_window(ctx.now_utc, ctx.lead_tz):
        return AutopilotDecision(False, "outside_business_hours")
    return AutopilotDecision(True, "ok")


def prior_auto_replies(db, enrollment_id) -> int:
    """Count auto-sent replies in this thread (draft rows with auto_sent=true that
    actually dispatched)."""
    from sqlalchemy import func, select

    from craftsman.core.models import ReplyDraft

    return db.scalar(
        select(func.count(ReplyDraft.id)).where(
            ReplyDraft.enrollment_id == enrollment_id,
            ReplyDraft.auto_sent.is_(True),
            ReplyDraft.status.in_(["sent", "edited_sent"]),
        )
    ) or 0
