"""Adversarial (M2.3): the signal-triggered enrollment safety property. Predict-then-run
(TESTING.md §3). The ⛔-gated guarantee: an `enroll` rule auto-enrolls ONLY verified,
above-threshold, not-already-enrolled leads, lands them in `queued` and NOWHERE FURTHER
(research/validation never skipped), and re-firing double-enrolls no one. `notify`/
`boost_score` never mutate enrollment state. Plus: collectors reject SSRF targets.
"""

import uuid

import pytest
from sqlalchemy import select

from craftsman.core.models import (
    Campaign,
    Company,
    Enrollment,
    Lead,
    Message,
    Signal,
    SignalRule,
)
from craftsman.research.fetch import UnsafeURL
from craftsman.scoring.collectors import PageDiffCollector, RssFundingCollector
from craftsman.scoring.rules import evaluate_rules

THRESHOLD = 0.55


def _company(db):
    c = Company(domain="acme.com", name="Acme")
    db.add(c)
    db.flush()
    return c


def _lead(db, company, **kw):
    lead = Lead(
        email=kw.pop("email", f"l-{uuid.uuid4().hex[:8]}@acme.com"),
        company_id=company.id,
        email_verified=kw.pop("email_verified", True),
        status=kw.pop("status", "verified"),
        icp_score=kw.pop("icp_score", 0.9),
        **kw,
    )
    db.add(lead)
    db.flush()
    return lead


def _campaign_with_enroll_rule(db, signal_type="funding"):
    c = Campaign(name="intent", icp_description="x", value_prop="y")
    db.add(c)
    db.flush()
    db.add(SignalRule(campaign_id=c.id, signal_type=signal_type, action="enroll", active=True))
    db.flush()
    return c


def _fire(db, company, signal_type="funding"):
    sig = Signal(company_id=company.id, type=signal_type, payload={}, source="test")
    db.add(sig)
    db.flush()
    return evaluate_rules(db, sig, THRESHOLD)


def test_enroll_only_eligible_leads_and_lands_in_queued(db):
    company = _company(db)
    _campaign_with_enroll_rule(db)
    good = _lead(db, company, icp_score=0.9)  # verified, above → enrolls
    _lead(db, company, icp_score=0.1)  # below threshold → skip
    _lead(db, company, email_verified=False, status="new")  # unverified → skip
    _lead(db, company, icp_score=None)  # never scored → skip

    tally = _fire(db, company)
    assert tally["enroll"] == 1

    enrollments = db.scalars(select(Enrollment)).all()
    assert len(enrollments) == 1
    e = enrollments[0]
    assert e.lead_id == good.id
    # THE safety property: lands in queued and nowhere further
    assert e.state == "queued" and e.current_step == 0
    # no message was sent, no research skipped ahead — the state machine hasn't run yet
    assert db.scalars(select(Message)).all() == []


def test_refiring_the_signal_double_enrolls_no_one(db):
    company = _company(db)
    _campaign_with_enroll_rule(db)
    _lead(db, company, icp_score=0.9)
    _fire(db, company)
    _fire(db, company)  # same signal type fires again
    assert len(db.scalars(select(Enrollment)).all()) == 1


def test_notify_and_boost_never_mutate_state(db):
    company = _company(db)
    campaign = Campaign(name="c", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    db.add(SignalRule(campaign_id=campaign.id, signal_type="funding", action="notify", active=True))
    db.add(SignalRule(campaign_id=campaign.id, signal_type="funding", action="boost_score", active=True))
    db.flush()
    _lead(db, company, icp_score=0.9)

    pings = []
    sig = Signal(company_id=company.id, type="funding", payload={}, source="t")
    db.add(sig)
    db.flush()
    tally = evaluate_rules(db, sig, THRESHOLD, notify=pings.append)

    assert tally == {"boost_score": 1, "notify": 1, "enroll": 0}
    assert pings and "funding" in pings[0]
    assert db.scalars(select(Enrollment)).all() == []  # no autonomy from notify/boost


def test_inactive_rule_does_not_fire(db):
    company = _company(db)
    campaign = Campaign(name="c", icp_description="x", value_prop="y")
    db.add(campaign)
    db.flush()
    db.add(SignalRule(campaign_id=campaign.id, signal_type="funding", action="enroll", active=False))
    db.flush()
    _lead(db, company, icp_score=0.9)
    assert _fire(db, company)["enroll"] == 0
    assert db.scalars(select(Enrollment)).all() == []


def test_no_rule_means_no_autonomy(db):
    # the OFF-by-default guarantee: a signal with no matching rule enrolls no one
    company = _company(db)
    _lead(db, company, icp_score=0.9)
    assert _fire(db, company)["enroll"] == 0
    assert db.scalars(select(Enrollment)).all() == []


# ---------------------------------------------------------------- collector SSRF


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1/feed", "http://news.test/feed", "https://169.254.169.254/feed"],
)
async def test_rss_collector_blocks_ssrf(url, monkeypatch):
    from craftsman.research import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_resolve_ips", lambda host: ["127.0.0.1"])
    with pytest.raises(UnsafeURL):
        await RssFundingCollector(url)._fetch()


async def test_page_diff_collector_blocks_ssrf(monkeypatch):
    from craftsman.research import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_resolve_ips", lambda host: ["10.0.0.1"])
    # _fetch tries the path against a private-resolving domain → fetch_url_body raises
    # UnsafeURL, which the collector's per-company guard would catch; here we assert the
    # underlying fetch refuses rather than silently returning content.
    with pytest.raises(UnsafeURL):
        await PageDiffCollector("homepage_diff", [""], "tech_stack_change")._fetch("evil.com")
