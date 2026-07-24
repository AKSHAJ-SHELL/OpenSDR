"""Placement endpoint attacks (M5.3), predict-then-run per TESTING.md §3.

Properties under attack:
- the seed list is schema-hardened: >10 seeds and non-email strings never reach
  the send path;
- a suppressed seed address still receives placement mail — DELIBERATE, recorded
  in deliverability/placement.py: suppression is a prospect-protection concept,
  and seeds are operator-owned test accounts the operator explicitly submitted;
  refusing would make the smoke test silently lie about coverage;
- placement runs are tenant data: org B can neither see, mark, nor start runs
  against org A's campaigns (item endpoints 404, list returns zero foreign rows).
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import generate_token, hash_token, key_prefix
from craftsman.compliance.suppression import suppress
from craftsman.core.models import (
    ApiKey,
    Campaign,
    Mailbox,
    Org,
    PlacementResult,
    PlacementRun,
    SequenceStep,
    SuppressionEntry,
    UnsubscribeToken,
    Variant,
)
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.workers import tasks

SKELETON = (
    "Subject: {{subject_hook}}\n\nHi {{first_name}},\n\n"
    "{{personalization_sentence}} {{value_prop_bridge}} {{cta_question}}\n\n{{signature}}"
)


@pytest.fixture()
def campaign_with_variant(db):
    campaign = Campaign(name="pl-adv", icp_description="x", value_prop="y", status="active")
    db.add(campaign)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=3)
    db.add(step)
    db.flush()
    db.add(Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={}))
    db.add(Mailbox(email=f"adv-{uuid.uuid4().hex[:6]}@advplace.example", daily_limit=40))
    db.flush()
    return campaign


def _op(make_key):
    return {"Authorization": f"Bearer {make_key('operate')}"}


# Predicted: an 11-seed list is rejected by the schema (422) before any row or
# task is created — the endpoint body never runs.
def test_refuses_more_than_ten_seeds(client, db, make_key, campaign_with_variant):
    seeds = [f"seed{i}@probe-mail.com" for i in range(11)]
    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": str(campaign_with_variant.id), "seed_emails": seeds},
        headers=_op(make_key),
    )
    assert r.status_code == 422
    assert db.scalar(select(PlacementRun.id).limit(1)) is None


# Predicted: non-email strings (injection-shaped included) are rejected by
# EmailStr validation — 422, no run row.
@pytest.mark.parametrize(
    "bad_seed",
    [
        "not-an-email",
        "a@b",
        "seed@probe-mail.com\nBcc: victim@example.com",  # header-injection shape
        "",
    ],
)
def test_refuses_non_email_seeds(client, db, make_key, campaign_with_variant, bad_seed):
    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": str(campaign_with_variant.id), "seed_emails": [bad_seed]},
        headers=_op(make_key),
    )
    assert r.status_code == 422
    assert db.scalar(select(PlacementRun.id).limit(1)) is None


# Predicted: an empty seed list is refused too (min_length=1).
def test_refuses_empty_seed_list(client, db, make_key, campaign_with_variant):
    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": str(campaign_with_variant.id), "seed_emails": []},
        headers=_op(make_key),
    )
    assert r.status_code == 422


# Predicted: placement requires operate scope — a read key gets 403.
def test_placement_requires_operate_scope(client, db, make_key, campaign_with_variant):
    r = client.post(
        "/deliverability/placement",
        json={
            "campaign_id": str(campaign_with_variant.id),
            "seed_emails": ["seed@probe-mail.com"],
        },
        headers={"Authorization": f"Bearer {make_key('read')}"},
    )
    assert r.status_code == 403


# Predicted: a SUPPRESSED seed still receives the placement mail — the chosen
# and documented stance (see module docstring + deliverability/placement.py):
# suppression protects prospects; seeds are the operator's own test accounts.
def test_suppressed_seed_still_receives_placement_mail(engine, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    seed = f"suppressed-{tag}@probe-mail.com"
    with Session(bind=engine) as s:
        campaign = Campaign(
            name=f"pl-sup-{tag}", icp_description="x", value_prop="y", status="active",
        )
        s.add(campaign)
        s.flush()
        step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=3)
        s.add(step)
        s.flush()
        variant = Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={})
        s.add(variant)
        mailbox = Mailbox(
            email=f"box-{tag}@supplace.example", smtp_host="smtp.supplace.example",
            smtp_port=587, daily_limit=1000, warmup_stage=4,
        )
        s.add(mailbox)
        suppress(s, seed, reason="unsubscribe")  # the attack setup: seed is suppressed
        run = PlacementRun(campaign_id=campaign.id, status="running")
        s.add(run)
        s.flush()
        s.add(PlacementResult(run_id=run.id, seed_email=seed))
        s.commit()
        ids = {
            "campaign_id": campaign.id, "variant_id": variant.id,
            "mailbox_id": mailbox.id, "run_id": str(run.id),
        }

    smtp_calls = []

    async def fake_send(msg, **kwargs):
        smtp_calls.append(msg)

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
        finally:
            s.close()

    monkeypatch.setattr(tasks, "session_scope", scope)
    try:
        tasks.run_placement.run(ids["run_id"])

        assert len(smtp_calls) == 1 and smtp_calls[0]["To"] == seed
        with Session(bind=engine) as s:
            result = s.scalar(
                select(PlacementResult).where(
                    PlacementResult.run_id == uuid.UUID(ids["run_id"])
                )
            )
            assert result.delivered is True and result.error is None
            # and the suppression entry itself is untouched
            assert s.scalar(
                select(SuppressionEntry).where(SuppressionEntry.email == seed)
            ) is not None
    finally:
        with Session(bind=engine) as s:
            s.query(PlacementResult).filter(
                PlacementResult.run_id == uuid.UUID(ids["run_id"])
            ).delete(synchronize_session=False)
            s.query(PlacementRun).filter(
                PlacementRun.id == uuid.UUID(ids["run_id"])
            ).delete(synchronize_session=False)
            s.query(UnsubscribeToken).filter(
                UnsubscribeToken.lead_email == seed
            ).delete(synchronize_session=False)
            s.query(SuppressionEntry).filter(SuppressionEntry.email == seed).delete(
                synchronize_session=False
            )
            s.query(Variant).filter(Variant.id == ids["variant_id"]).delete()
            s.query(SequenceStep).filter(
                SequenceStep.campaign_id == ids["campaign_id"]
            ).delete()
            s.query(Campaign).filter(Campaign.id == ids["campaign_id"]).delete()
            s.query(Mailbox).filter(Mailbox.id == ids["mailbox_id"]).delete()
            s.commit()


# ---------------------------------------------------------------- cross-tenant


@pytest.fixture()
def foreign_org_key(db):
    """A fresh org with its own operate+read key (cribbed from the M5.1d
    two_orgs fixture). Returns (token, org_id)."""
    with unscoped_context():
        org_b = Org(name="Placement B", slug=f"pl-b-{uuid.uuid4().hex[:6]}")
        db.add(org_b)
        db.flush()
    token = generate_token()
    with org_context(org_b.id):
        db.add(ApiKey(
            name="b-op", key_prefix=key_prefix(token),
            key_hash=hash_token(token), scopes=["operate"],
        ))
        db.flush()
    return token, org_b.id


# Predicted: org A's placement run is invisible to org B — list returns zero
# foreign rows, the item endpoint 404s (never 403), marking 404s, and starting
# a run against org A's campaign 404s.
def test_placement_run_invisible_across_tenants(
    client, db, make_key, campaign_with_variant, foreign_org_key
):
    run = PlacementRun(campaign_id=campaign_with_variant.id, status="running")
    db.add(run)
    db.flush()
    db.add(PlacementResult(run_id=run.id, seed_email="seed@probe-mail.com"))
    db.flush()
    run_id = str(run.id)
    campaign_id = str(campaign_with_variant.id)
    b_token, _ = foreign_org_key
    # production parity: API requests get fresh sessions; clear the warm
    # identity map so db.get() emits filtered SQL (two_orgs pattern)
    db.expunge_all()
    h = {"Authorization": f"Bearer {b_token}"}

    r = client.get("/deliverability/placement", headers=h)
    assert r.status_code == 200 and r.json() == []

    r = client.get(f"/deliverability/placement/{run_id}", headers=h)
    assert r.status_code == 404

    r = client.post(
        f"/deliverability/placement/{run_id}/mark",
        json={"marks": {"seed@probe-mail.com": "inbox"}},
        headers=h,
    )
    assert r.status_code == 404

    r = client.post(
        "/deliverability/placement",
        json={"campaign_id": campaign_id, "seed_emails": ["seed@probe-mail.com"]},
        headers=h,
    )
    assert r.status_code == 404  # org A's campaign does not exist for org B

    # and org A still sees its run untouched
    r = client.get(f"/deliverability/placement/{run_id}", headers=_op(make_key))
    assert r.status_code == 200
    assert r.json()["results"][0]["verdict"] == "pending"
