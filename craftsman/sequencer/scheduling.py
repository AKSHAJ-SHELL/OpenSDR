"""Send-time scheduling: lead-local business hours, business days, jitter.

Sends land 9:00–16:30 in the lead's timezone, jittered ±20 min — never batch-blasted.
"""

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from craftsman.core.config import get_settings


def add_business_days(start: datetime, days: int) -> datetime:
    """Advance `days` business days (skip Sat/Sun)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def next_send_time(
    *,
    after_utc: datetime,
    wait_business_days: int,
    lead_tz: str,
    rng: random.Random | None = None,
) -> datetime:
    """Earliest business-hours slot >= after + wait_business_days, in the lead's tz.

    Returns a UTC datetime.
    """
    settings = get_settings()
    rng = rng or random.Random()
    try:
        tz = ZoneInfo(lead_tz)
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")

    local = after_utc.astimezone(tz)
    target = add_business_days(local, wait_business_days) if wait_business_days > 0 else local

    start_h = settings.send_window_start_hour
    end_h, end_m = settings.send_window_end_hour, settings.send_window_end_minute
    jitter = timedelta(minutes=rng.uniform(-settings.send_jitter_minutes, settings.send_jitter_minutes))

    window_start = target.replace(hour=start_h, minute=0, second=0, microsecond=0)
    window_end = target.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    if target < window_start:
        slot = window_start + abs(jitter)
    elif target > window_end:
        # roll to next business day's window
        nxt = add_business_days(target, 1)
        slot = nxt.replace(hour=start_h, minute=0, second=0, microsecond=0) + abs(jitter)
    else:
        slot = target + jitter
        slot = max(min(slot, window_end), window_start)

    # weekends never send
    while slot.weekday() >= 5:
        slot = add_business_days(slot, 1).replace(hour=start_h, minute=0, second=0, microsecond=0) + abs(jitter)

    return slot.astimezone(timezone.utc)
