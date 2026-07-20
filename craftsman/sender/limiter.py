"""Redis token bucket: ~1 send per 45-90s (jittered) per mailbox.

Domains get torched by burstiness, not volume.
"""

import random
import time

import redis

from craftsman.core.config import get_settings

MIN_INTERVAL_S = 45
MAX_INTERVAL_S = 90


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def acquire_send_slot(
    mailbox_id: str,
    r: redis.Redis | None = None,
    rng: random.Random | None = None,
    now: float | None = None,
) -> float:
    """Try to acquire a send slot for this mailbox.

    Returns 0.0 if clear to send now, else seconds to wait before retrying.
    """
    r = r or _redis()
    rng = rng or random.Random()
    now = now if now is not None else time.time()
    key = f"sendslot:{mailbox_id}"

    next_ok = r.get(key)
    if next_ok is not None and float(next_ok) > now:
        return float(next_ok) - now

    interval = rng.uniform(MIN_INTERVAL_S, MAX_INTERVAL_S)
    r.set(key, now + interval, ex=max(1, int(MAX_INTERVAL_S * 2)))
    return 0.0
