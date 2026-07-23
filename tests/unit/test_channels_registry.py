"""M3.1: channel registry — the fork-friendly seam behaves as documented."""

import pytest

from craftsman.channels import (
    ALL_CHANNELS,
    ASSISTED_CHANNELS,
    get_channel,
    is_assisted,
)


def test_email_is_the_only_autonomous_channel():
    """The design constraint of M3, as an executable assertion."""
    autonomous = [name for name in ALL_CHANNELS if not get_channel(name).assisted]
    assert autonomous == ["email"]


def test_assisted_channels_are_linkedin_and_call():
    assert ASSISTED_CHANNELS == {"linkedin_task", "call_task"}
    assert is_assisted("linkedin_task")
    assert is_assisted("call_task")
    assert not is_assisted("email")


def test_unknown_channel_raises():
    with pytest.raises(KeyError):
        get_channel("carrier_pigeon")


def test_email_spec_matches_copywriter_vocabulary():
    from craftsman.copywriter.fill import LLM_SLOTS, STATIC_SLOTS

    spec = get_channel("email")
    assert spec.uses_skeleton
    assert spec.llm_slots == LLM_SLOTS
    assert spec.static_slots == STATIC_SLOTS


def test_linkedin_spec_matches_fill_schema():
    from craftsman.core.schemas import LinkedInSlotFill

    spec = get_channel("linkedin_task")
    assert spec.uses_skeleton
    assert spec.llm_slots == frozenset(LinkedInSlotFill.model_fields)
    assert "signature" not in spec.static_slots  # LinkedIn shows the real profile


def test_call_channel_has_no_skeleton():
    spec = get_channel("call_task")
    assert not spec.uses_skeleton
    assert spec.llm_slots == frozenset()
    assert spec.outcomes == ("connected", "voicemail", "no_answer")
