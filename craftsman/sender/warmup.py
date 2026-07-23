"""Warmup-stage caps: fresh mailboxes ramp 10 → 20 → 30 → full daily_limit."""

WARMUP_CAPS = {0: 10, 1: 20, 2: 30, 3: 40}


def effective_daily_limit(daily_limit: int, warmup_stage: int) -> int:
    if warmup_stage >= 4:
        return daily_limit
    return min(daily_limit, WARMUP_CAPS.get(warmup_stage, 10))


def warmup_schedule(daily_limit: int) -> list[dict]:
    """The day-by-day ramp a fresh mailbox walks: one stage per calendar day (advanced by
    `reset_daily_counters`), from stage 0 to full `daily_limit` at stage 4. Pure view of
    WARMUP_CAPS for the onboarding UI — it drives no sending."""
    return [
        {"day": stage + 1, "stage": stage, "cap": effective_daily_limit(daily_limit, stage)}
        for stage in range(5)
    ]
