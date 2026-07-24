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


def acquire_domain_slot(
    domain: str,
    r: redis.Redis | None = None,
    now: float | None = None,
) -> float:
    """Domain-level token bucket wrapping the per-mailbox one (M5.3): several
    mailboxes on one sending domain must not collectively burst the domain.

    Governed by the `domain_min_interval_s` knob — 0 (the default) disables the
    bucket entirely and this returns 0.0 without touching Redis. Same contract
    as acquire_send_slot: 0.0 = clear to send, else seconds to wait.
    """
    interval = float(get_settings().domain_min_interval_s)
    if interval <= 0:
        return 0.0
    r = r or _redis()
    now = now if now is not None else time.time()
    key = f"sendslot:domain:{domain}"

    next_ok = r.get(key)
    if next_ok is not None and float(next_ok) > now:
        return float(next_ok) - now

    r.set(key, now + interval, ex=max(1, int(interval * 2)))
    return 0.0
