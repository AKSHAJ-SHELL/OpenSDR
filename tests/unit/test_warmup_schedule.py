"""Warmup ramp view (M1.4). The schedule drives no sending — it surfaces WARMUP_CAPS."""

from craftsman.sender.warmup import effective_daily_limit, warmup_schedule


def test_schedule_default_limit():
    sched = warmup_schedule(40)
    assert [s["cap"] for s in sched] == [10, 20, 30, 40, 40]
    assert [s["day"] for s in sched] == [1, 2, 3, 4, 5]
    assert [s["stage"] for s in sched] == [0, 1, 2, 3, 4]


def test_schedule_low_limit_caps_dont_exceed_it():
    # a mailbox capped at 15/day never shows 20/30 in its ramp
    sched = warmup_schedule(15)
    assert [s["cap"] for s in sched] == [10, 15, 15, 15, 15]


def test_schedule_agrees_with_effective_daily_limit():
    for stage in range(6):
        expected = effective_daily_limit(50, stage)
        entry = warmup_schedule(50)[min(stage, 4)]
        assert entry["cap"] == expected
