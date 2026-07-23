"""Prometheus /metrics (M0.6b Phase 2).

The collector reads Postgres + Redis at scrape time; here session_scope is pointed at
the test DB and Redis is faked, so we can assert on real content.
"""

import uuid
from contextlib import contextmanager

import fakeredis
import pytest

from craftsman.core import db as db_module
from craftsman.core import metrics as metrics_module
from craftsman.core.models import Campaign, Company, Enrollment, Lead, Message


@pytest.fixture()
def seeded(db):
    company = Company(domain=f"m-{uuid.uuid4().hex[:8]}.test")
    db.add(company)
    db.flush()
    campaign = Campaign(name="m", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@m.test", company_id=company.id, status="verified")
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="waiting", current_step=1)
    db.add(enr)
    db.flush()
    db.add(Message(enrollment_id=enr.id, direction="outbound", step_order=1, subject="s", body="b"))
    db.add(Message(enrollment_id=enr.id, direction="inbound", subject="r", body="a",
                   classification="interested", classification_confidence=0.9))
    db.flush()
    return db


@pytest.fixture()
def patched_stores(monkeypatch, seeded):
    """Point the collector's session_scope at the test DB and Redis at a fake."""
    @contextmanager
    def scope():
        yield seeded

    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    fake.rpush("send", "task-a", "task-b")  # 2 pending in the send queue
    fake.set("metrics:rejections:rate_limited", 3)

    monkeypatch.setattr(db_module, "session_scope", scope)
    monkeypatch.setattr(metrics_module, "_redis", lambda: fake)
    return fake


def test_collector_reports_db_and_redis_metrics(patched_stores):
    payload, content_type = metrics_module.metrics_payload()
    text = payload.decode()
    assert "text/plain" in content_type

    # DB-derived gauges
    assert 'craftsman_enrollments{state="waiting"} 1.0' in text
    assert 'craftsman_leads{status="verified"} 1.0' in text
    assert 'craftsman_replies{classification="interested"} 1.0' in text
    assert "craftsman_outbound_total 1.0" in text

    # Redis-derived
    assert 'craftsman_queue_depth{queue="send"} 2.0' in text
    assert 'craftsman_send_rejections_total{reason="rate_limited"} 3.0' in text


def test_record_rejection_increments_redis(monkeypatch):
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(metrics_module, "_redis", lambda: fake)
    metrics_module.record_rejection("suppressed")
    metrics_module.record_rejection("suppressed")
    assert fake.get("metrics:rejections:suppressed") == "2"


def test_record_rejection_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(metrics_module, "_redis", boom)
    metrics_module.record_rejection("whatever")  # must swallow — metrics never block a send


def test_metrics_endpoint_requires_read_key(client, make_key, patched_stores):
    assert client.get("/metrics").status_code == 401
    resp = client.get("/metrics", headers={"Authorization": f"Bearer {make_key('read')}"})
    assert resp.status_code == 200
    assert "craftsman_enrollments" in resp.text
