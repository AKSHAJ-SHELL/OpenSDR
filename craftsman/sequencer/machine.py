"""Pure-function sequence state machine. No I/O — the transition table is data.

States:
  queued | researching | ready | waiting | replied_interested | replied_objection |
  replied_not_now | ooo_rescheduled | bounced | unsubscribed | finished_no_reply | error
"""

from enum import Enum


class Event(str, Enum):
    RESEARCH_DONE = "research_done"
    RESEARCH_FAILED = "research_failed"
    SEND_OK = "send_ok"
    SEND_FAILED = "send_failed"
    TIMER = "timer"
    REPLY_INTERESTED = "reply_interested"
    REPLY_OBJECTION = "reply_objection"
    REPLY_NOT_NOW = "reply_not_now"
    REPLY_OOO = "reply_ooo"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"


TERMINAL_STATES = {
    "replied_interested", "replied_objection", "replied_not_now",
    "bounced", "unsubscribed", "finished_no_reply", "error",
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
    ("*", Event.BOUNCE): "bounced",
    ("*", Event.UNSUBSCRIBE): "unsubscribed",
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
