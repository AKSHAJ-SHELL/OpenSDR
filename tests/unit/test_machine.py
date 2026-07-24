import pytest

from craftsman.sequencer.machine import (
    TERMINAL_STATES,
    Event,
    InvalidTransition,
    is_terminal,
    next_state,
)


def test_happy_path_three_step_sequence():
    state = "queued"
    state = next_state(state, Event.RESEARCH_DONE)
    assert state == "ready"
    state = next_state(state, Event.SEND_OK)
    assert state == "waiting"
    state = next_state(state, Event.TIMER)
    assert state == "ready"


def test_interested_reply_stops_sequence():
    assert next_state("waiting", Event.REPLY_INTERESTED) == "replied_interested"
    assert is_terminal("replied_interested")


def test_ooo_reschedules_then_resumes():
    state = next_state("waiting", Event.REPLY_OOO)
    assert state == "ooo_rescheduled"
    assert next_state(state, Event.TIMER) == "ready"


def test_ooo_then_interested():
    assert next_state("ooo_rescheduled", Event.REPLY_INTERESTED) == "replied_interested"


def test_wildcard_bounce_from_any_nonterminal_state():
    for state in ("queued", "researching", "ready", "waiting", "ooo_rescheduled"):
        assert next_state(state, Event.BOUNCE) == "bounced"
        assert next_state(state, Event.UNSUBSCRIBE) == "unsubscribed"


def test_wildcard_does_not_resurrect_terminal_states():
    with pytest.raises(InvalidTransition):
        next_state("replied_interested", Event.BOUNCE)
    with pytest.raises(InvalidTransition):
        next_state("unsubscribed", Event.UNSUBSCRIBE)


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransition):
        next_state("queued", Event.SEND_OK)  # can't send before research
    with pytest.raises(InvalidTransition):
        next_state("ready", Event.TIMER)


def test_research_failure_goes_to_error():
    assert next_state("queued", Event.RESEARCH_FAILED) == "error"
    assert is_terminal("error")


def test_terminal_states_are_closed():
    """Terminal states accept no events — with exactly one carve-out (M4.3):
    replied_* → MEETING_BOOKED, because humans book meetings after replying and
    the booking is the funnel's real terminal. The carve-out is enumerated here
    so any new terminal exit breaks this test by construction."""
    allowed_terminal_exits = {
        ("replied_interested", Event.MEETING_BOOKED),
        ("replied_objection", Event.MEETING_BOOKED),
        ("replied_not_now", Event.MEETING_BOOKED),
    }
    for ts in TERMINAL_STATES:
        for event in Event:
            if (ts, event) in allowed_terminal_exits:
                assert next_state(ts, event) == "meeting_booked"
                continue
            with pytest.raises(InvalidTransition):
                next_state(ts, event)
