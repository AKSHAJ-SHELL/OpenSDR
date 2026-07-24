"""Per-org quotas (M5.1c): send cap, mailbox count, enrichment budget.

All three follow the same shape: NULL = unlimited (the self-hoster default,
so nothing changes for an upgrade), atomic conditional UPDATE for the two
counters that race under concurrent workers.
"""

import concurrent.futures as cf

from sqlalchemy.orm import Session

from craftsman.core.models import Org
from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context
from craftsman.ingest.enrichment import reserve_enrichment_calls
from craftsman.sender.smtp import release_org_slot, reserve_org_slot


def _org(db) -> Org:
    return db.get(Org, DEFAULT_ORG_ID)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── send cap ───────────────────────────────────────────────────────────────


def test_null_cap_is_unlimited(db):
    org = _org(db)
    assert org.daily_send_cap is None
    for _ in range(50):
        assert reserve_org_slot(db, org.id) is True


def test_send_cap_reserve_release_cycle(db):
    org = _org(db)
    org.daily_send_cap = 2
    org.sent_today = 0
    db.add(org)
    db.flush()
    assert reserve_org_slot(db, org.id) is True
    assert reserve_org_slot(db, org.id) is True
    assert reserve_org_slot(db, org.id) is False  # cap reached
    release_org_slot(db, org.id)
    assert reserve_org_slot(db, org.id) is True  # released slot is reusable
    release_org_slot(db, org.id)
    release_org_slot(db, org.id)
    release_org_slot(db, org.id)
    release_org_slot(db, org.id)  # never goes negative
    db.flush()
    db.refresh(org)
    assert org.sent_today == 0


# Predict: with cap=5 and 12 racing workers the atomic UPDATE serializes them —
# exactly 5 reservations succeed, same guarantee the campaign cap proved in M0.6a.
def test_org_cap_holds_under_concurrent_reservations(engine, default_org_ctx):
    with Session(bind=engine) as s:
        org = s.get(Org, DEFAULT_ORG_ID)
        org.daily_send_cap, org.sent_today = 5, 0
        s.add(org)
        s.commit()
    try:
        def reserve_once(_):
            with org_context(DEFAULT_ORG_ID), Session(bind=engine) as s:
                ok = reserve_org_slot(s, DEFAULT_ORG_ID)
                s.commit()
                return ok

        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            results = list(ex.map(reserve_once, range(12)))
        assert sum(results) == 5
    finally:
        with Session(bind=engine) as s:
            org = s.get(Org, DEFAULT_ORG_ID)
            org.daily_send_cap, org.sent_today = None, 0
            s.add(org)
            s.commit()


# ─── enrichment budget ──────────────────────────────────────────────────────


def test_enrichment_budget(db):
    org = _org(db)
    assert reserve_enrichment_calls(db, org.id, 3) is True  # NULL budget

    org.enrichment_daily_budget, org.enrichment_calls_today = 5, 0
    db.add(org)
    db.flush()
    assert reserve_enrichment_calls(db, org.id, 3) is True
    assert reserve_enrichment_calls(db, org.id, 3) is False  # 3+3 > 5, refused whole
    assert reserve_enrichment_calls(db, org.id, 2) is True  # exact fit
    db.refresh(org)
    assert org.enrichment_calls_today == 5


# ─── mailbox quota + the org view ───────────────────────────────────────────


def test_mailbox_quota_blocks_creation(client, make_key, db):
    admin = make_key("admin")
    org = _org(db)
    org.max_mailboxes = 1
    db.add(org)
    db.flush()

    payload = {
        "email": "one@example.com", "smtp_host": "smtp.example.com",
        "smtp_port": 587, "smtp_user": "u", "smtp_password": "p",
    }
    assert client.post("/mailboxes", headers=_auth(admin), json=payload).status_code == 200
    payload["email"] = "two@example.com"
    r = client.post("/mailboxes", headers=_auth(admin), json=payload)
    assert r.status_code == 409
    assert "quota" in r.json()["detail"]


def test_org_view_reports_quota_and_usage(client, make_key, db):
    org = _org(db)
    org.daily_send_cap, org.sent_today = 100, 7
    db.add(org)
    db.flush()
    r = client.get("/org", headers=_auth(make_key("read")))
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "default"
    assert body["daily_send_cap"] == 100 and body["sent_today"] == 7
    assert body["max_mailboxes"] is None  # unlimited reads as null, honestly


def test_daily_reset_zeroes_org_counters(db, monkeypatch):
    from contextlib import contextmanager

    from craftsman.workers import tasks as task_mod

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    org = _org(db)
    org.sent_today, org.enrichment_calls_today = 42, 9
    db.add(org)
    db.flush()
    task_mod.reset_daily_counters.run()
    db.refresh(org)
    assert org.sent_today == 0 and org.enrichment_calls_today == 0
