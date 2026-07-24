"""Audit-log export + retention (M5.4): NDJSON shape, `?since=` filtering,
admin gating, org scoping, and the retention sweep's per-org delete."""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from craftsman.core.models import AuditLog, Org
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.workers import tasks


def _other_org(db) -> Org:
    with unscoped_context():
        org = Org(name="Audit-B", slug=f"audit-b-{uuid.uuid4().hex[:6]}")
        db.add(org)
        db.flush()
    return org


def _audit_row(db, event, created_at=None, org_id=None):
    row = AuditLog(event=event, detail={"marker": event})
    if org_id is not None:
        row.org_id = org_id
    db.add(row)
    db.flush()
    if created_at is not None:
        row.created_at = created_at
        db.add(row)
        db.flush()
    return row


def test_export_streams_ndjson_of_the_callers_org_only(client, db, make_key):
    _audit_row(db, "export_a1")
    _audit_row(db, "export_a2")
    other = _other_org(db)
    with org_context(other.id):
        _audit_row(db, "export_b1", org_id=other.id)

    r = client.get("/audit/export", headers={"Authorization": f"Bearer {make_key('admin')}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")

    lines = [json.loads(line) for line in r.text.splitlines() if line]
    events = [line["event"] for line in lines]
    assert "export_a1" in events and "export_a2" in events
    assert "export_b1" not in events  # org B's rows never cross the boundary
    # NDJSON shape: every line is a flat object with the documented keys
    for line in lines:
        assert set(line) == {
            "id", "enrollment_id", "event", "from_state", "to_state", "detail", "created_at",
        }


def test_export_since_filter_and_ordering(client, db, make_key):
    now = datetime.now(timezone.utc)
    _audit_row(db, "old_row", created_at=now - timedelta(days=10))
    _audit_row(db, "new_row", created_at=now - timedelta(hours=1))

    since = (now - timedelta(days=1)).isoformat()
    r = client.get(
        "/audit/export",
        params={"since": since},  # params= URL-encodes the timezone's '+'
        headers={"Authorization": f"Bearer {make_key('admin')}"},
    )
    assert r.status_code == 200
    events = [json.loads(line)["event"] for line in r.text.splitlines() if line]
    assert "new_row" in events
    assert "old_row" not in events

    # no filter → oldest first (stable pagination for incremental consumers)
    r_all = client.get("/audit/export", headers={"Authorization": f"Bearer {make_key('admin')}"})
    all_events = [json.loads(line)["event"] for line in r_all.text.splitlines() if line]
    assert all_events.index("old_row") < all_events.index("new_row")


def test_export_requires_admin_scope(client, make_key):
    assert client.get("/audit/export").status_code == 401
    r = client.get(
        "/audit/export", headers={"Authorization": f"Bearer {make_key('operate')}"}
    )
    assert r.status_code == 403
    assert "admin" in r.json()["detail"]


def test_retention_sweep_deletes_only_old_rows_in_the_right_org(db, monkeypatch):
    """audit_retention_days=30: each org's sweep deletes ITS >30d rows and
    nothing else — org B's old rows survive an org-A perspective and vice
    versa; recent rows survive everywhere. Default (0) is covered too."""
    from craftsman.core.config import get_settings

    now = datetime.now(timezone.utc)
    old_a = _audit_row(db, "old_a", created_at=now - timedelta(days=40))
    new_a = _audit_row(db, "new_a", created_at=now - timedelta(days=5))
    other = _other_org(db)
    with org_context(other.id):
        old_b = _audit_row(db, "old_b", created_at=now - timedelta(days=40), org_id=other.id)
        new_b = _audit_row(db, "new_b", created_at=now - timedelta(days=5), org_id=other.id)

    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr(tasks, "session_scope", scope)

    def _events(org_id):
        with org_context(org_id):
            return set(db.scalars(select(AuditLog.event)).all())

    default_org_id = old_a.org_id

    # default knob (0) → sweep keeps everything, forever
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
    get_settings.cache_clear()
    try:
        tasks.reset_daily_counters.run()
        assert {"old_a", "new_a"} <= _events(default_org_id)
        assert {"old_b", "new_b"} <= _events(other.id)

        # retention on → only >30d rows die, each org pruning itself only
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
        get_settings.cache_clear()
        tasks.reset_daily_counters.run()
    finally:
        get_settings.cache_clear()

    a_events = _events(default_org_id)
    b_events = _events(other.id)
    assert "old_a" not in a_events and "new_a" in a_events
    assert "old_b" not in b_events and "new_b" in b_events
    # nothing leaked across: totals add up (no cross-org deletes)
    with org_context(other.id):
        assert db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.event.in_(["old_b", "new_b"]))
        ) == 1
    assert old_b.id != old_a.id and new_b.id != new_a.id  # sanity
