import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from craftsman.sequencer.scheduling import add_business_days, next_send_time


def test_add_business_days_skips_weekend():
    friday = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    assert add_business_days(friday, 1).weekday() == 0  # Monday
    assert add_business_days(friday, 3).weekday() == 2  # Wednesday


def test_send_lands_in_business_window_lead_tz():
    rng = random.Random(1)
    after = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)  # Monday
    for tz in ("America/New_York", "Europe/Berlin", "Asia/Tokyo", "America/Los_Angeles"):
        slot_utc = next_send_time(
            after_utc=after, wait_business_days=3, lead_tz=tz, rng=rng
        )
        local = slot_utc.astimezone(ZoneInfo(tz))
        assert local.weekday() < 5
        minutes = local.hour * 60 + local.minute
        assert 9 * 60 - 25 <= minutes <= 16 * 60 + 55, (tz, local)


def test_never_sends_on_weekend():
    rng = random.Random(2)
    thursday = datetime(2026, 7, 23, 20, 0, tzinfo=timezone.utc)
    for wait in range(0, 6):
        slot = next_send_time(
            after_utc=thursday, wait_business_days=wait,
            lead_tz="America/Los_Angeles", rng=rng,
        )
        assert slot.astimezone(ZoneInfo("America/Los_Angeles")).weekday() < 5


def test_bad_timezone_falls_back():
    slot = next_send_time(
        after_utc=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        wait_business_days=1,
        lead_tz="Not/AZone",
        rng=random.Random(3),
    )
    assert slot.tzinfo is not None
