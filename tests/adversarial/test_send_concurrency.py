"""Concurrency correctness for the send path (M0.6a, finding B3).

The per-campaign cap must hold under concurrent workers (the read-then-act race),
and the per-mailbox spacing must be enforced via Redis (cross-worker), not per-worker
memory. Uses real Postgres row locking via separate sessions.
"""

import concurrent.futures as cf
import uuid

from sqlalchemy.orm import Session

from craftsman.core.models import Campaign
from craftsman.sender.smtp import release_campaign_slot, reserve_campaign_slot


def _make_campaign(engine, cap):
    with Session(bind=engine) as s:
        c = Campaign(
            name=f"cap-{uuid.uuid4().hex[:8]}",
            icp_description="ops", value_prop="save money", daily_cap=cap,
        )
        s.add(c)
        s.commit()
        return c.id


def _drop_campaign(engine, cid):
    with Session(bind=engine) as s:
        c = s.get(Campaign, cid)
        if c is not None:
            s.delete(c)
            s.commit()


def test_cap_holds_under_concurrent_reservations(engine, default_org_ctx):
    """Predict: with cap=5 and 12 racing workers, exactly 5 reservations succeed —
    the atomic UPDATE's row lock serializes them, so no over-send."""
    from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context

    cap = 5
    workers = 12
    cid = _make_campaign(engine, cap)
    try:
        def reserve_once(_):
            # threads start with an empty contextvars context — enter the org
            # explicitly, exactly like a Celery worker task does (M5.1)
            with org_context(DEFAULT_ORG_ID), Session(bind=engine) as s:
                camp = s.get(Campaign, cid)
                ok = reserve_campaign_slot(s, camp)
                s.commit()
                return ok

        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(reserve_once, range(workers)))

        assert sum(results) == cap  # exactly the cap, never more
        with Session(bind=engine) as s:
            assert s.get(Campaign, cid).sent_today == cap
    finally:
        _drop_campaign(engine, cid)


def test_reserve_then_release_frees_the_slot(engine, default_org_ctx):
    cid = _make_campaign(engine, cap=1)
    try:
        with Session(bind=engine) as s:
            assert reserve_campaign_slot(s, s.get(Campaign, cid)) is True
            s.commit()
        with Session(bind=engine) as s:  # cap full now
            assert reserve_campaign_slot(s, s.get(Campaign, cid)) is False
            s.commit()
        with Session(bind=engine) as s:  # release returns the slot
            release_campaign_slot(s, cid)
            s.commit()
        with Session(bind=engine) as s:  # reusable again
            assert reserve_campaign_slot(s, s.get(Campaign, cid)) is True
            s.commit()
    finally:
        _drop_campaign(engine, cid)


def test_release_never_goes_negative(engine, default_org_ctx):
    cid = _make_campaign(engine, cap=5)
    try:
        with Session(bind=engine) as s:
            release_campaign_slot(s, cid)  # nothing reserved yet
            s.commit()
        with Session(bind=engine) as s:
            assert s.get(Campaign, cid).sent_today == 0  # guarded by sent_today > 0
    finally:
        _drop_campaign(engine, cid)


def test_send_spacing_is_enforced_via_redis():
    """§3.3 mechanism check: per-mailbox spacing lives in Redis (shared across workers),
    not per-worker memory — so the guarantee survives multi-worker deployment."""
    import fakeredis

    from craftsman.sender.limiter import acquire_send_slot

    r = fakeredis.FakeStrictRedis(decode_responses=True)
    box = "mbox-1"
    # first send is clear
    assert acquire_send_slot(box, r=r, now=1000.0) == 0.0
    # an immediate second send for the same mailbox is spaced out
    wait = acquire_send_slot(box, r=r, now=1000.0)
    assert wait > 0
    # the state is in Redis, so a different worker (same server) sees it too
    assert r.get(f"sendslot:{box}") is not None
