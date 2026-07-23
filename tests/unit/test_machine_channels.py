"""M3.1: state-machine transitions for assisted channels (awaiting_human_touch).

Additive only — the existing transition tests in test_machine.py are untouched and
must keep passing: email-only campaigns never enter the new state.
"""

import pytest

from craftsman.sequencer.machine import (
    TERMINAL_STATES,
    Event,
    InvalidTransition,
    is_terminal,
    next_state,
)


def test_ready_task_created_enters_awaiting_human_touch():
    assert next_state("ready", Event.TASK_CREATED) == "awaiting_human_touch"


@pytest.mark.parametrize("event", [Event.TASK_DONE, Event.TASK_SKIPPED, Event.TASK_EXPIRED])
def test_task_resolution_returns_to_waiting(event):
    assert next_state("awaiting_human_touch", event) == "waiting"


def test_awaiting_human_touch_is_not_terminal():
    assert not is_terminal("awaiting_human_touch")
    assert "awaiting_human_touch" not in TERMINAL_STATES


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (Event.REPLY_INTERESTED, "replied_interested"),
        (Event.REPLY_OBJECTION, "replied_objection"),
        (Event.REPLY_NOT_NOW, "replied_not_now"),
        (Event.REPLY_OOO, "ooo_rescheduled"),
    ],
)
def test_replies_route_normally_while_task_open(event, expected):
    """A reply landing while a task is open (e.g. to an earlier email step) must not
    be lost — the sequence is paused, not deaf."""
    assert next_state("awaiting_human_touch", event) == expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [(Event.BOUNCE, "bounced"), (Event.UNSUBSCRIBE, "unsubscribed")],
)
def test_wildcards_cover_awaiting_human_touch(event, expected):
    assert next_state("awaiting_human_touch", event) == expected


def test_email_states_cannot_take_task_events():
    """Task events are meaningless outside the task flow — undefined, not silently
    absorbed."""
    with pytest.raises(InvalidTransition):
        next_state("waiting", Event.TASK_DONE)
    with pytest.raises(InvalidTransition):
        next_state("queued", Event.TASK_CREATED)
    with pytest.raises(InvalidTransition):
        next_state("ready", Event.TASK_EXPIRED)


def test_terminal_states_reject_task_events():
    for state in TERMINAL_STATES:
        with pytest.raises(InvalidTransition):
            next_state(state, Event.TASK_DONE)
