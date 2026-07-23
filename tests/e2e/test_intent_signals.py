"""End-to-end intent signals (M2.3): rule CRUD API, scoring-weights + lead-signals read
surfaces, and the collect_signals task wiring (collect → persist → fire rules)."""

import uuid
from contextlib import contextmanager

from sqlalchemy import select

from craftsman.core.models import Campaign, Company, Enrollment, Lead, Signal, SignalRule
from craftsman.scoring.collectors import CollectedSignal


def _h(make_key, scope="operate"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def _campaign(db):
    c = Campaign(name="intent", icp_description="x", value_prop="y")
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------- rule CRUD


def test_signal_rule_crud(client, db, make_key):
    campaign = _campaign(db)
    h = _h(make_key)
    created = client.post(
        f"/campaigns/{campaign.id}/signal-rules",
        headers=h,
        json={"signal_type": "funding", "action": "enroll"},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    # duplicate → 409
    dup = client.post(
        f"/campaigns/{campaign.id}/signal-rules",
        headers=h,
        json={"signal_type": "funding", "action": "enroll"},
    )
    assert dup.status_code == 409

    listed = client.get(f"/campaigns/{campaign.id}/signal-rules", headers=_h(make_key, "read"))
    assert listed.status_code == 200 and len(listed.json()) == 1

    assert client.delete(f"/campaigns/{campaign.id}/signal-rules/{rule_id}", headers=h).status_code == 204
    assert client.get(f"/campaigns/{campaign.id}/signal-rules", headers=_h(make_key, "read")).json() == []


def test_rule_endpoints_scope_gated(client, db, make_key):
    campaign = _campaign(db)
    # read key cannot create (operate-gated)
    r = client.post(
        f"/campaigns/{campaign.id}/signal-rules",
        headers=_h(make_key, "read"),
        json={"signal_type": "funding", "action": "notify"},
    )
    assert r.status_code == 403
    assert client.post(f"/campaigns/{campaign.id}/signal-rules", json={"signal_type": "funding", "action": "notify"}).status_code == 401


def test_invalid_signal_type_or_action_rejected(client, db, make_key):
    campaign = _campaign(db)
    bad = client.post(
        f"/campaigns/{campaign.id}/signal-rules",
        headers=_h(make_key),
        json={"signal_type": "funding", "action": "delete_everything"},
    )
    assert bad.status_code == 422  # Literal-constrained schema


# ---------------------------------------------------------------- read surfaces


def test_scoring_weights_endpoint(client, db, make_key):
    w = client.get("/leads/scoring-weights", headers=_h(make_key, "read"))
    assert w.status_code == 200
    body = w.json()
    assert body["cosine"] == 0.7 and body["rule"] == 0.3
    assert body["signal_cosine"] == 0.6 and body["signal_rule"] == 0.25 and body["signal"] == 0.15


def test_lead_signals_surface(client, db, make_key):
    company = Company(domain="acme.com", name="Acme")
    db.add(company)
    db.flush()
    lead = Lead(email="a@acme.com", company_id=company.id)
    db.add(lead)
    db.add(Signal(company_id=company.id, type="funding", payload={"link": "x"}, source="rss"))
    db.flush()
    resp = client.get(f"/leads/{lead.id}/signals", headers=_h(make_key, "read"))
    assert resp.status_code == 200 and resp.json()[0]["type"] == "funding"
    assert client.get(f"/leads/{uuid.uuid4()}/signals", headers=_h(make_key, "read")).status_code == 404


# ---------------------------------------------------------------- task wiring


def test_collect_signals_persists_and_fires_rules(db, monkeypatch):
    from craftsman.workers import tasks as task_mod

    company = Company(domain="acme.com", name="Acme")
    db.add(company)
    db.flush()
    campaign = _campaign(db)
    db.add(SignalRule(campaign_id=campaign.id, signal_type="funding", action="enroll", active=True))
    db.add(Lead(email="a@acme.com", company_id=company.id, email_verified=True, status="verified", icp_score=0.9))
    db.flush()

    class FakeCollector:
        name = "fake"

        async def collect(self, db_):
            return [CollectedSignal(company_id=company.id, type="funding", payload={}, source="fake")]

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    monkeypatch.setattr(
        "craftsman.scoring.collectors.build_collectors", lambda s: [FakeCollector()]
    )

    n = task_mod.collect_signals()
    assert n == 1
    assert len(db.scalars(select(Signal)).all()) == 1
    # the enroll rule fired → one queued enrollment (auto-enrollment, off-by-default proven
    # here because we explicitly created the rule)
    e = db.scalars(select(Enrollment)).all()
    assert len(e) == 1 and e[0].state == "queued"


def test_collect_signals_noop_when_disabled(db, monkeypatch):
    from craftsman.workers import tasks as task_mod

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    # default settings → no collectors configured
    monkeypatch.setattr(
        "craftsman.scoring.collectors.build_collectors", lambda s: []
    )
    assert task_mod.collect_signals() == 0
