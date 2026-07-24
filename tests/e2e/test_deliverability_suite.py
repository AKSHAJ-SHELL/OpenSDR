"""Deliverability suite e2e (M5.3, G12).

Guarantees under test:
- record_bounce rolls every bounce into domain_stats (spam/block/reputation
  diagnostics → spam_bounces, the complaint proxy);
- the auto-pause budget fires AT domain_pause_bounce_threshold and not below,
  pauses only the bouncing domain's mailboxes, audit-logs `domain_auto_paused`,
  and does not re-fire once the domain is fully paused;
- un-pause is the existing PATCH /mailboxes health edit;
- GET /deliverability/domains aggregates per sending domain with every DNS call
  behind monkeypatched seams (dns_auth.resolve_txt + health._dns_query);
- a placement run delivers the constant-fill opener to each seed through the
  real send engine (aiosmtplib patched), records results, accepts operator
  marks, and touches NO campaign/org caps, bandit posteriors, enrollments,
  or Message rows.

Task-level placement tests use dedicated committed sessions (the task commits
internally), mirroring tests/e2e/test_dry_run.py.
"""

import uuid
from contextlib import contextmanager
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.core.models import (
    AuditLog,
    Campaign,
    DomainStat,
    Enrollment,
    Mailbox,
    Message,
    Org,
    PlacementResult,
    PlacementRun,
    SequenceStep,
    UnsubscribeToken,
    Variant,
)
from craftsman.core.tenancy import DEFAULT_ORG_ID
from craftsman.deliverability import dns_auth, health
from craftsman.sender.smtp import record_bounce
from craftsman.workers import tasks

SKELETON = (
    "Subject: {{subject_hook}}\n\nHi {{first_name}},\n\n"
    "{{personalization_sentence}} {{value_prop_bridge}} {{cta_question}}\n\n{{signature}}"
)


def _headers(make_key, scope="read"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def _patch_dns(monkeypatch, txt_table=None, query_table=None):
    """Both DNS seams: dns_auth.resolve_txt (SPF/DKIM/DMARC) and health._dns_query
    (MX/A + DNSBL). Missing keys read as NXDOMAIN/no-answer."""
    txt_table = txt_table or {}
    query_table = query_table or {}

    monkeypatch.setattr(
        dns_auth, "resolve_txt", lambda name, timeout=5.0: list(txt_table.get(name, []))
    )
    monkeypatch.setattr(
        health,
        "_dns_query",
        lambda name, rdtype="A", timeout=5.0: list(query_table.get((name, rdtype), [])),
    )


def _mailbox(db, email, **kw):
    box = Mailbox(email=email, smtp_host="smtp.test", smtp_port=587, daily_limit=40, **kw)
    db.add(box)
    db.flush()
    return box


def _stat(db, domain):
    return db.scalar(
        select(DomainStat).where(DomainStat.domain == domain, DomainStat.day == date.today())
    )


# ---------------------------------------------------------------- bounce rollup


def test_record_bounce_updates_domain_stats(db):
    box = _mailbox(db, f"s-{uuid.uuid4().hex[:6]}@rollup.test")
    domain = "rollup.test"

    record_bounce(db, box, diagnostic="550 5.1.1 user unknown")
    record_bounce(db, box, diagnostic="554 rejected as SPAM by policy")
    db.flush()

    row = _stat(db, domain)
    assert row is not None
    assert row.hard_bounces == 1 and row.spam_bounces == 1 and row.sends == 0
    # the existing per-mailbox behavior is untouched: 2 bounces → degraded
    assert box.hard_bounces_today == 2 and box.health == "degraded"


def test_auto_pause_fires_at_threshold_not_below(db):
    tag = uuid.uuid4().hex[:6]
    domain, other = f"burn-{tag}.test", f"safe-{tag}.test"
    box_a = _mailbox(db, f"a@{domain}")
    box_b = _mailbox(db, f"b@{domain}")
    box_other = _mailbox(db, f"c@{other}")

    # 4 bounces (threshold default 5): nothing pauses
    for _ in range(4):
        record_bounce(db, box_a, diagnostic="user unknown")
    db.flush()
    assert box_a.health == "degraded" and box_b.health == "ok"  # degraded ≠ paused
    assert (
        db.scalar(select(func.count(AuditLog.id)).where(AuditLog.event == "domain_auto_paused"))
        == 0
    )

    # 5th bounce crosses the budget: the whole domain pauses — and only it
    record_bounce(db, box_a, diagnostic="mailbox full")
    db.flush()
    assert box_a.health == "paused" and box_b.health == "paused"
    assert box_other.health == "ok"

    audits = list(
        db.scalars(select(AuditLog).where(AuditLog.event == "domain_auto_paused"))
    )
    assert len(audits) == 1
    assert audits[0].detail["domain"] == domain
    assert audits[0].detail["bounces_today"] == 5
    assert sorted(audits[0].detail["mailboxes"]) == sorted([box_a.email, box_b.email])

    # further bounces on the already-paused domain do not re-audit / re-notify
    record_bounce(db, box_a, diagnostic="user unknown")
    db.flush()
    assert (
        db.scalar(select(func.count(AuditLog.id)).where(AuditLog.event == "domain_auto_paused"))
        == 1
    )


def test_unpause_is_the_mailbox_health_patch(client, db, make_key):
    box = _mailbox(db, f"p-{uuid.uuid4().hex[:6]}@pausedom.test", health="paused")
    r = client.patch(
        f"/mailboxes/{box.id}",
        json={"health": "ok"},
        headers={"Authorization": f"Bearer {make_key('admin')}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["health"] == "ok"


# ---------------------------------------------------------------- GET /deliverability/domains


def test_domain_health_endpoint_shape(client, db, make_key, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    domain = f"health-{tag}.test"
    box = _mailbox(db, f"x@{domain}", dkim_selector="sel1")
    _mailbox(db, f"y@{domain}", health="paused")

    _patch_dns(
        monkeypatch,
        txt_table={
            domain: ["v=spf1 include:_spf.test ~all"],
            f"sel1._domainkey.{domain}": ["v=DKIM1; p=abc"],
            # no _dmarc record → missing → -15
        },
        query_table={
            (domain, "MX"): [f"10 mx.{domain}"],
            (f"mx.{domain}", "A"): ["192.0.2.55"],
            # listed on zen (default zone #1) → -40; spamcop clear
            ("55.2.0.192.zen.spamhaus.org", "A"): ["127.0.0.2"],
        },
    )
    # rollup data: 100 sends, 3 hard (3% → -10), 0 spam
    for _ in range(100):
        health.record_domain_send(db, domain)
    for _ in range(3):
        health._bump_domain_stat(db, domain, hard=1)
    db.flush()

    r = client.get("/deliverability/domains", headers=_headers(make_key))
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["domain"] == domain)

    assert entry["mailboxes"] == 2 and entry["paused_mailboxes"] == 1
    assert entry["spf"]["status"] == "pass"
    assert entry["dkim"]["status"] == "pass" and entry["dkim"]["selector"] == "sel1"
    assert entry["dmarc"]["status"] == "missing"
    zones = {b["zone"]: b for b in entry["blocklists"]}
    assert zones["zen.spamhaus.org"]["status"] == "listed"
    assert zones["zen.spamhaus.org"]["listed_ips"] == ["192.0.2.55"]
    assert zones["bl.spamcop.net"]["status"] == "clear"
    assert entry["stats_7d"]["sends"] == 100
    assert entry["stats_7d"]["hard_bounces"] == 3
    # 100 - 15 (dmarc) - 40 (one listing) - 10 (3% bounce rate) = 35
    assert entry["score"] == 35
    assert entry["components"] == {
        "dns_auth": 15, "blocklist": 40, "bounce_rate": 10, "complaint_rate": 0,
    }
    assert box.id  # silence unused warning-ish readability


def test_domain_health_requires_read_scope(client, db):
    assert client.get("/deliverability/domains").status_code == 401


# ---------------------------------------------------------------- placement lifecycle


def _placement_scenario(engine):
    """Committed campaign + step-1 variants + mailbox, dry-run-test style."""
    tag = uuid.uuid4().hex[:8]
    with Session(bind=engine) as s:
        campaign = Campaign(
            name=f"pl-{tag}", icp_description="x", value_prop="we help teams cut costs",
            daily_cap=50, status="active",
            sender_persona={"name": "Sam", "title": "Founder"},
        )
        s.add(campaign)
        s.flush()
        step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=3)
        s.add(step)
        s.flush()
        # two arms: v2 has the higher posterior mean → the "winning" variant
        v1 = Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={},
                     alpha=1.0, beta=9.0)
        v2 = Variant(step_id=step.id, name="v2", skeleton=SKELETON, slot_schema={},
                     alpha=5.0, beta=5.0)
        s.add_all([v1, v2])
        mailbox = Mailbox(
            email=f"box-{tag}@place.test", smtp_host="smtp.place.test", smtp_port=587,
            daily_limit=1000, warmup_stage=4, health="ok",
        )
        s.add(mailbox)
        s.commit()
        return {
            "campaign_id": campaign.id, "winning_variant_id": v2.id,
            "mailbox_id": mailbox.id, "tag": tag,
        }


def _cleanup_placement(engine, ids):
    with Session(bind=engine) as s:
        run_ids = select(PlacementRun.id).where(PlacementRun.campaign_id == ids["campaign_id"])
        s.query(PlacementResult).filter(PlacementResult.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
        s.query(PlacementRun).filter(PlacementRun.campaign_id == ids["campaign_id"]).delete()
        s.query(UnsubscribeToken).filter(
            UnsubscribeToken.lead_email.like(f"%{ids['tag']}%")
        ).delete(synchronize_session=False)
        s.query(Variant).filter(
            Variant.step_id.in_(
                select(SequenceStep.id).where(SequenceStep.campaign_id == ids["campaign_id"])
            )
        ).delete(synchronize_session=False)
        s.query(SequenceStep).filter(SequenceStep.campaign_id == ids["campaign_id"]).delete()
        s.query(Campaign).filter(Campaign.id == ids["campaign_id"]).delete()
        s.query(Mailbox).filter(Mailbox.id == ids["mailbox_id"]).delete()
        s.commit()


def _patch_placement_task(engine, monkeypatch):
    """Task runs on the test engine; SMTP captured at the aiosmtplib seam; the
    mailbox rate bucket answers 'clear' without Redis."""
    smtp_calls: list[dict] = []

    async def fake_send(msg, **kwargs):
        smtp_calls.append({"msg": msg, **kwargs})

    monkeypatch.setattr("craftsman.sender.smtp.aiosmtplib.send", fake_send)
    monkeypatch.setattr(
        "craftsman.deliverability.placement.acquire_send_slot", lambda *a, **k: 0.0
    )

    @contextmanager
    def scope():
        s = Session(bind=engine)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(tasks, "session_scope", scope)
    return smtp_calls


def test_placement_run_lifecycle(engine, monkeypatch):
    """create → deliver to each seed via the patched deliver seam → mark → read
    back — and prove caps/bandit/enrollments/messages are untouched."""
    ids = _placement_scenario(engine)
    seeds = [f"seed1-{ids['tag']}@probe-mail.com", f"seed2-{ids['tag']}@probe-mail.com"]
    try:
        with Session(bind=engine) as s:
            run = PlacementRun(campaign_id=ids["campaign_id"], status="running")
            s.add(run)
            s.flush()
            for seed in seeds:
                s.add(PlacementResult(run_id=run.id, seed_email=seed))
            s.commit()
            run_id = str(run.id)
            msg_count_before = s.scalar(select(func.count(Message.id)))
            enroll_before = s.scalar(select(func.count(Enrollment.id)))

        smtp_calls = _patch_placement_task(engine, monkeypatch)
        tasks.run_placement.run(run_id)

        assert len(smtp_calls) == 2
        recipients = sorted(c["msg"]["To"] for c in smtp_calls)
        assert recipients == sorted(seeds)
        for call in smtp_calls:
            msg = call["msg"]
            # marked, compliance-shaped, and rendered from the WINNING skeleton
            assert msg["X-Craftsman-Placement"] == run_id
            assert msg["List-Unsubscribe"]
            assert "placement test" in msg.get_content()
            assert call["hostname"] == "smtp.place.test"  # the real deliver() path

        with Session(bind=engine) as s:
            run = s.get(PlacementRun, uuid.UUID(run_id))
            assert run.status == "complete" and run.finished_at is not None
            results = {r.seed_email: r for r in run.results}
            assert all(r.delivered and r.verdict == "pending" for r in results.values())
            assert all(r.mailbox_id == ids["mailbox_id"] for r in results.values())

            # ---- untouched: campaign cap, org cap, bandit, enrollments, messages
            campaign = s.get(Campaign, ids["campaign_id"])
            assert campaign.sent_today == 0
            org = s.get(Org, DEFAULT_ORG_ID)
            assert org.sent_today == 0
            v2 = s.get(Variant, ids["winning_variant_id"])
            assert (v2.alpha, v2.beta) == (5.0, 5.0)
            assert s.scalar(select(func.count(Message.id))) == msg_count_before
            assert s.scalar(select(func.count(Enrollment.id))) == enroll_before

            # touched, deliberately: real sends spend real mailbox + domain budget
            box = s.get(Mailbox, ids["mailbox_id"])
            assert box.sent_today == 2
            stat = s.scalar(
                select(DomainStat).where(
                    DomainStat.domain == "place.test", DomainStat.day == date.today()
                )
            )
            assert stat is not None and stat.sends >= 2

            audit = s.scalar(
                select(AuditLog).where(AuditLog.event == "placement_run_complete")
                .order_by(AuditLog.created_at.desc()).limit(1)
            )
            assert audit is not None and audit.detail["run_id"] == run_id
            assert audit.detail["variant_id"] == str(ids["winning_variant_id"])
    finally:
        _cleanup_placement(engine, ids)


def test_placement_endpoint_flow(client, db, make_key, monkeypatch):
    """POST creates run + pending results and enqueues the worker; mark + get
    round-trip. The Celery handoff is captured, never executed."""
    campaign = Campaign(name="pl-api", icp_description="x", value_prop="y", status="active")
    db.add(campaign)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=3)
    db.add(step)
    db.flush()
    db.add(Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={}))
    _mailbox(db, f"api-{uuid.uuid4().hex[:6]}@apiplace.test")
    db.flush()

    enqueued = []
    monkeypatch.setattr(tasks.run_placement, "delay", lambda rid: enqueued.append(rid))

    op = {"Authorization": f"Bearer {make_key('operate')}"}
    r = client.post(
        "/deliverability/placement",
        json={
            "campaign_id": str(campaign.id),
            # duplicate seed collapses to one result
            "seed_emails": ["s1@probe-mail.com", "S1@probe-mail.com", "s2@probe-mail.com"],
        },
        headers=op,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "running"
    assert sorted(x["seed_email"] for x in body["results"]) == ["s1@probe-mail.com", "s2@probe-mail.com"]
    assert all(x["verdict"] == "pending" for x in body["results"])
    assert enqueued == [body["id"]]
    # audit-logged at creation
    assert db.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.event == "placement_run_started")
    ) == 1

    # mark: partial, then read back
    r = client.post(
        f"/deliverability/placement/{body['id']}/mark",
        json={"marks": {"s1@probe-mail.com": "inbox", "s2@probe-mail.com": "spam"}},
        headers=op,
    )
    assert r.status_code == 200, r.text
    verdicts = {x["seed_email"]: x["verdict"] for x in r.json()["results"]}
    assert verdicts == {"s1@probe-mail.com": "inbox", "s2@probe-mail.com": "spam"}

    r = client.get(f"/deliverability/placement/{body['id']}", headers=_headers(make_key))
    assert r.status_code == 200
    assert {x["seed_email"]: x["verdict"] for x in r.json()["results"]} == verdicts

    # list endpoint carries the run too
    r = client.get("/deliverability/placement", headers=_headers(make_key))
    assert any(run["id"] == body["id"] for run in r.json())

    # marking an address that isn't part of the run is a 400, not silent creation
    r = client.post(
        f"/deliverability/placement/{body['id']}/mark",
        json={"marks": {"stranger@probe-mail.com": "inbox"}},
        headers=op,
    )
    assert r.status_code == 400


def test_placement_requires_step1_variant_and_mailbox(client, db, make_key):
    campaign = Campaign(name="pl-bare", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    op = {"Authorization": f"Bearer {make_key('operate')}"}
    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": str(campaign.id), "seed_emails": ["s@probe-mail.com"]},
        headers=op,
    )
    assert r.status_code == 400 and "variant" in r.json()["detail"]

    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": str(uuid.uuid4()), "seed_emails": ["s@probe-mail.com"]},
        headers=op,
    )
    assert r.status_code == 404
