"""Warmup-stage caps: fresh mailboxes ramp 10 → 20 → 30 → full daily_limit."""

WARMUP_CAPS = {0: 10, 1: 20, 2: 30, 3: 40}


def effective_daily_limit(daily_limit: int, warmup_stage: int) -> int:
    if warmup_stage >= 4:
        return daily_limit
    return min(daily_limit, WARMUP_CAPS.get(warmup_stage, 10))
