"""Dead-letter recording for terminally-failed tasks (M0.6b Phase 3)."""

import uuid
from contextlib import contextmanager

from sqlalchemy import func, select

from craftsman.core.models import DeadLetter
from craftsman.workers import celery_app


def _patch_scope(monkeypatch, db):
    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr("craftsman.core.db.session_scope", scope)


def test_task_failure_records_a_dead_letter(db, monkeypatch):
    _patch_scope(monkeypatch, db)
    eid = str(uuid.uuid4())

    class _Sender:
        name = "craftsman.workers.tasks.generate_and_send"

    celery_app._record_dead_letter(
        sender=_Sender(), task_id="abc-123",
        exception=RuntimeError("smtp exploded"), args=[eid], kwargs={},
        einfo="Traceback (most recent call last): ...",
    )
    db.flush()

    row = db.scalar(select(DeadLetter).where(DeadLetter.task_id == "abc-123"))
    assert row is not None
    assert row.task_name.endswith("generate_and_send")
    assert "smtp exploded" in row.exception
    assert row.enrollment_id == eid
    assert row.args == [eid]


def test_dead_letter_recording_never_raises(monkeypatch):
    @contextmanager
    def boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr("craftsman.core.db.session_scope", boom)
    # must swallow so the original task failure isn't masked
    celery_app._record_dead_letter(
        sender=None, task_id="x", exception=ValueError("orig"), args=["e"], kwargs={}, einfo="t"
    )


def test_dead_letters_endpoint_requires_read_key(client, make_key, db):
    db.add(DeadLetter(task_name="t", task_id="k1", exception="boom", enrollment_id="e1"))
    db.flush()
    assert client.get("/dead-letters").status_code == 401
    resp = client.get("/dead-letters", headers={"Authorization": f"Bearer {make_key('read')}"})
    assert resp.status_code == 200
    assert any(item["task_id"] == "k1" for item in resp.json())


def test_dead_letter_count_appears_in_metrics(db, monkeypatch):
    import fakeredis

    from craftsman.core import db as db_module
    from craftsman.core import metrics as metrics_module

    db.add(DeadLetter(task_name="t", exception="x"))
    db.flush()

    @contextmanager
    def scope():
        yield db

    monkeypatch.setattr(db_module, "session_scope", scope)
    monkeypatch.setattr(metrics_module, "_redis", lambda: fakeredis.FakeStrictRedis(decode_responses=True))

    text = metrics_module.metrics_payload()[0].decode()
    assert "craftsman_dead_letters" in text
    n = db.scalar(select(func.count(DeadLetter.id)))
    assert f"craftsman_dead_letters {float(n)}" in text
