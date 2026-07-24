"""Pure-function sequence state machine. No I/O — the transition table is data.

States:
  queued | researching | ready | waiting | awaiting_human_touch | replied_interested |
  replied_objection | replied_not_now | ooo_rescheduled | bounced | unsubscribed |
  finished_no_reply | meeting_booked | error

`awaiting_human_touch` (M3.1): an assisted-channel step generated a validated task and
is waiting for a human to perform the touch. Replies, bounces, and unsubscribes still
route normally from it — the sequence is paused, not deaf.

`meeting_booked` (M4.3): the funnel's terminal win, driven by a signed calendar
webhook. Explicit pairs only (no wildcard): a booking may arrive from any live state
OR from a replied_* terminal (the usual case — humans book after replying), which
works because explicit pairs are checked before the terminal guard.
"""

from enum import Enum


class Event(str, Enum):
    RESEARCH_DONE = "research_done"
    RESEARCH_FAILED = "research_failed"
    SEND_OK = "send_ok"
    SEND_FAILED = "send_failed"
    TIMER = "timer"
    TASK_CREATED = "task_created"  # M3.1: validated task queued for a human
    TASK_DONE = "task_done"  # human performed the touch
    TASK_SKIPPED = "task_skipped"  # human declined the touch; sequence advances
    TASK_EXPIRED = "task_expired"  # due window passed on a skip_on_expire step
    REPLY_INTERESTED = "reply_interested"
    REPLY_OBJECTION = "reply_objection"
    REPLY_NOT_NOW = "reply_not_now"
    REPLY_OOO = "reply_ooo"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"
    MEETING_BOOKED = "meeting_booked"  # M4.3: signed calendar webhook confirmed a booking


TERMINAL_STATES = {
    "replied_interested", "replied_objection", "replied_not_now",
    "bounced", "unsubscribed", "finished_no_reply", "meeting_booked", "error",
}

# (state, event) -> new_state.  "*" state matches any non-terminal state.
TRANSITIONS: dict[tuple[str, Event], str] = {
    ("queued", Event.RESEARCH_DONE): "ready",
    ("queued", Event.RESEARCH_FAILED): "error",
    ("researching", Event.RESEARCH_DONE): "ready",
    ("researching", Event.RESEARCH_FAILED): "error",
    ("ready", Event.SEND_OK): "waiting",
    ("ready", Event.SEND_FAILED): "error",
    ("waiting", Event.TIMER): "ready",
    ("waiting", Event.REPLY_INTERESTED): "replied_interested",
    ("waiting", Event.REPLY_OBJECTION): "replied_objection",
    ("waiting", Event.REPLY_NOT_NOW): "replied_not_now",
    ("waiting", Event.REPLY_OOO): "ooo_rescheduled",
    ("ooo_rescheduled", Event.TIMER): "ready",
    ("ooo_rescheduled", Event.REPLY_INTERESTED): "replied_interested",
    # M3.1 — assisted channels: ready → task queued → human completes/skips (or the
    # due window expires on a skip_on_expire step) → back into the normal wait cycle.
    ("ready", Event.TASK_CREATED): "awaiting_human_touch",
    ("awaiting_human_touch", Event.TASK_DONE): "waiting",
    ("awaiting_human_touch", Event.TASK_SKIPPED): "waiting",
    ("awaiting_human_touch", Event.TASK_EXPIRED): "waiting",
    # a reply can land while a task is open (e.g. to an earlier email step)
    ("awaiting_human_touch", Event.REPLY_INTERESTED): "replied_interested",
    ("awaiting_human_touch", Event.REPLY_OBJECTION): "replied_objection",
    ("awaiting_human_touch", Event.REPLY_NOT_NOW): "replied_not_now",
    ("awaiting_human_touch", Event.REPLY_OOO): "ooo_rescheduled",
    ("*", Event.BOUNCE): "bounced",
    ("*", Event.UNSUBSCRIBE): "unsubscribed",
    # M4.3 — a booking beats any in-flight state, and legitimately exits the
    # replied_* terminals (explicit pairs bypass the terminal guard by design).
    ("replied_interested", Event.MEETING_BOOKED): "meeting_booked",
    ("replied_objection", Event.MEETING_BOOKED): "meeting_booked",
    ("replied_not_now", Event.MEETING_BOOKED): "meeting_booked",
    ("waiting", Event.MEETING_BOOKED): "meeting_booked",
    ("awaiting_human_touch", Event.MEETING_BOOKED): "meeting_booked",
    ("ooo_rescheduled", Event.MEETING_BOOKED): "meeting_booked",
    ("ready", Event.MEETING_BOOKED): "meeting_booked",
}


class InvalidTransition(Exception):
    pass


def next_state(state: str, event: Event) -> str:
    """Pure transition lookup. Raises InvalidTransition for undefined pairs."""
    if (state, event) in TRANSITIONS:
        return TRANSITIONS[(state, event)]
    if state not in TERMINAL_STATES and ("*", event) in TRANSITIONS:
        return TRANSITIONS[("*", event)]
    raise InvalidTransition(f"no transition for state={state!r} event={event.value!r}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
