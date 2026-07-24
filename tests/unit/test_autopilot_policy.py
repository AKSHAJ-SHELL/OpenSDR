"""M4.4: the Autopilot policy engine — exhaustive gate matrix (the 'fuzz' the
roadmap asks for is deliberate exhaustion: every combination of every gate)."""

import itertools
from datetime import datetime, timezone

from craftsman.inbox.autopilot import (
    AUTOPILOT_SKELETONS,
    MAX_AUTO_REPLIES_PER_THREAD,
    AutopilotContext,
    decide,
    within_send_window,
)

TUESDAY_NOON_UTC = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)  # 12:00 in NYC
SATURDAY_NOON_UTC = datetime(2026, 7, 25, 17, 0, tzinfo=timezone.utc)
TUESDAY_3AM_UTC = datetime(2026, 7, 21, 7, 0, tzinfo=timezone.utc)  # 03:00 in NYC


def _ctx(**overrides) -> AutopilotContext:
    base = dict(
        autopilot_enabled=True,
        skeleton_key="reply_interested",
        classification_confidence=0.95,
        escalation_blocks=False,
        prior_auto_replies_in_thread=0,
        scheduling_url="https://cal.test/sam",
        info_doc_url="https://x.test/one-pager",
        now_utc=TUESDAY_NOON_UTC,
        lead_tz="America/New_York",
    )
    base.update(overrides)
    return AutopilotContext(**base)


def test_all_gates_pass_sends():
    d = decide(_ctx())
    assert d.send and d.reason == "ok"


def test_exhaustive_gate_matrix():
    """Every combination of gate states: send=True iff EVERY gate passes."""
    skeletons = ["reply_interested", "reply_objection_timing", "reply_objection_info",
                 "reply_other", None]
    confidences = [None, 0.5, 0.89, 0.9, 0.99]
    checked = sent = 0
    for (enabled, skeleton, conf, esc, prior, sched, info, now) in itertools.product(
        [True, False],
        skeletons,
        confidences,
        [True, False],
        [0, 1, 2],
        ["https://cal.test/s", None],
        ["https://x.test/d", None],
        [TUESDAY_NOON_UTC, SATURDAY_NOON_UTC, TUESDAY_3AM_UTC],
    ):
        d = decide(_ctx(
            autopilot_enabled=enabled,
            skeleton_key=skeleton,
            classification_confidence=conf,
            escalation_blocks=esc,
            prior_auto_replies_in_thread=prior,
            scheduling_url=sched,
            info_doc_url=info,
            now_utc=now,
        ))
        expected = (
            enabled
            and skeleton in AUTOPILOT_SKELETONS
            and not (skeleton == "reply_interested" and not sched)
            and not (skeleton == "reply_objection_info" and not info)
            and conf is not None and conf >= 0.9
            and not esc
            and prior < MAX_AUTO_REPLIES_PER_THREAD
            and now == TUESDAY_NOON_UTC
        )
        assert d.send is expected, (
            f"enabled={enabled} skeleton={skeleton} conf={conf} esc={esc} "
            f"prior={prior} sched={bool(sched)} info={bool(info)} now={now} "
            f"→ {d.send} ({d.reason}), expected {expected}"
        )
        checked += 1
        sent += d.send
    assert checked == 2 * 5 * 5 * 2 * 3 * 2 * 2 * 3  # 3600 combinations, none skipped
    assert 0 < sent < checked  # both regions of the matrix were exercised


def test_disabled_wins_over_everything():
    d = decide(_ctx(autopilot_enabled=False))
    assert not d.send and d.reason == "autopilot_disabled"


def test_one_auto_reply_per_thread_is_hardcoded():
    assert MAX_AUTO_REPLIES_PER_THREAD == 1
    d = decide(_ctx(prior_auto_replies_in_thread=1))
    assert not d.send and d.reason == "auto_reply_already_sent_in_thread"


def test_confidence_boundary_exact():
    assert decide(_ctx(classification_confidence=0.9)).send  # >= 0.9 fires
    d = decide(_ctx(classification_confidence=0.8999))
    assert not d.send and d.reason == "confidence_below_threshold"


def test_missing_links_refuse_their_skeletons():
    d = decide(_ctx(skeleton_key="reply_interested", scheduling_url=None))
    assert not d.send and d.reason == "no_scheduling_url"
    d = decide(_ctx(skeleton_key="reply_objection_info", info_doc_url=None))
    assert not d.send and d.reason == "no_info_doc_url"
    # timing needs neither link
    assert decide(_ctx(skeleton_key="reply_objection_timing",
                       scheduling_url=None, info_doc_url=None)).send


def test_escalation_block_refuses():
    d = decide(_ctx(escalation_blocks=True))
    assert not d.send and d.reason == "escalation_block_autopilot"


# ---------------------------------------------------------------- business hours


def test_window_weekday_inside():
    assert within_send_window(TUESDAY_NOON_UTC, "America/New_York")


def test_window_weekend_refuses():
    assert not within_send_window(SATURDAY_NOON_UTC, "America/New_York")


def test_window_night_refuses():
    assert not within_send_window(TUESDAY_3AM_UTC, "America/New_York")


def test_window_is_lead_local():
    # 17:00 UTC = 02:00 in Tokyo (Wednesday) — outside; 12:00 in NYC — inside
    assert within_send_window(TUESDAY_NOON_UTC, "America/New_York")
    assert not within_send_window(TUESDAY_NOON_UTC, "Asia/Tokyo")


def test_window_bad_tz_falls_back():
    # unknown tz falls back to America/Los_Angeles (09:00 local at 16:00 UTC — inside)
    assert within_send_window(TUESDAY_NOON_UTC, "Not/AZone") == within_send_window(
        TUESDAY_NOON_UTC, "America/Los_Angeles"
    )
