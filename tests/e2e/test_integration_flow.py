"""Integration tests over real Postgres: import → enroll → tick → reply → bandit."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from craftsman.bandit.settle import settle_expired
from craftsman.compliance.suppression import is_suppressed, suppress
from craftsman.core.models import (
    Campaign,
    Company,
    Enrollment,
    Lead,
    Message,
    ReviewQueueItem,
    SequenceStep,
    SuppressionEntry,
    Variant,
)
from craftsman.core.schemas import ReplyClassification
from craftsman.inbox.pipeline import handle_inbound
from craftsman.inbox.poller import InboundEmail
from craftsman.ingest.csv_import import import_csv
from craftsman.llm.mock_impl import MockLLM
from craftsman.sequencer.tick import tick

CSV = b"""email,first_name,last_name,title,company,domain
dana@acme.com,Dana,Lopez,VP Operations,Acme Robotics,acme.com
raj@bcorp.io,Raj,Patel,Head of Warehouse,BCorp,bcorp.io
dana@acme.com,Dana,Lopez,VP Operations,Acme Robotics,acme.com
bad-email,Nope,Nope,None,None,none.com
blocked@spam.com,Blocked,Person,CEO,Spam Inc,spam.com
"""


def _setup_campaign(db, wait_days=(0, 3, 4)):
    campaign = Campaign(name="test", icp_description="ops leaders", value_prop="save money")
    db.add(campaign)
    db.flush()
    steps = []
    for i, wd in enumerate(wait_days, start=1):
        step = SequenceStep(campaign_id=campaign.id, step_order=i, wait_days=wd)
        db.add(step)
        db.flush()
        variant = Variant(
            step_id=step.id, name=f"v{i}", skeleton="Subject: {{s}}\n{{b}}", slot_schema={}
        )
        db.add(variant)
        steps.append(step)
    db.flush()
    return campaign, steps


def _enroll(db, campaign, email="dana@acme.com", state="waiting", step=1):
    company = Company(domain=email.split("@")[1])
    db.add(company)
    db.flush()
    lead = Lead(email=email, company_id=company.id, first_name="Dana", email_verified=True,
                status="verified")
    db.add(lead)
    db.flush()
    enrollment = Enrollment(
        lead_id=lead.id, campaign_id=campaign.id, state=state, current_step=step,
        next_action_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(enrollment)
    db.flush()
    return lead, enrollment


def test_csv_import_dedupes_and_suppresses(db):
    db.add(SuppressionEntry(email="blocked@spam.com", reason="manual"))
    db.flush()
    result = import_csv(db, CSV)
    assert result.imported == 2
    assert result.deduped == 1
    assert result.suppressed == 1
    assert len(result.errors) == 1
    lead = db.scalar(select(Lead).where(Lead.email == "dana@acme.com"))
    assert lead.company.domain == "acme.com"


def test_tick_queued_goes_to_researching_and_enqueues(db):
    campaign, _ = _setup_campaign(db)
    _, enrollment = _enroll(db, campaign, state="queued", step=0)
    research_calls, send_calls = [], []
    handled = tick(db, research_calls.append, send_calls.append)
    assert handled == 1
    assert enrollment.state == "researching"
    assert research_calls == [str(enrollment.id)]
    assert send_calls == []


def test_tick_waiting_timer_advances_step(db):
    campaign, _ = _setup_campaign(db)
    _, enrollment = _enroll(db, campaign, state="waiting", step=1)
    send_calls = []
    tick(db, lambda x: None, send_calls.append)
    assert enrollment.state == "ready"
    assert enrollment.current_step == 2
    assert send_calls == [str(enrollment.id)]


def test_tick_finishes_after_last_step(db):
    campaign, _ = _setup_campaign(db)
    _, enrollment = _enroll(db, campaign, state="waiting", step=3)
    tick(db, lambda x: None, lambda x: None)
    assert enrollment.state == "finished_no_reply"
    assert enrollment.next_action_at is None


def test_interested_reply_full_pipeline(db):
    """Inbound reply → classified → state stops → bandit rewarded."""
    campaign, steps = _setup_campaign(db)
    lead, enrollment = _enroll(db, campaign, state="waiting", step=1)
    variant = db.scalar(select(Variant).where(Variant.step_id == steps[0].id))
    outbound = Message(
        enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
        subject="hello", body="original", smtp_message_id="<msg-1@flowbot.io>",
        bandit_outcome="pending",
        outcome_deadline=datetime.now(timezone.utc) + timedelta(days=3),
        sent_at=datetime.now(timezone.utc),
    )
    db.add(outbound)
    db.flush()

    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.95))
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: hello", body="Sounds great, send details!",
        in_reply_to="<msg-1@flowbot.io>", references=[], message_id="<r1@acme.com>",
    )
    msg = asyncio.run(handle_inbound(db, llm, inbound))

    assert msg is not None and msg.classification == "interested"
    assert enrollment.state == "replied_interested"
    assert outbound.bandit_outcome == "success"
    db.flush()
    db.refresh(variant)
    assert variant.alpha == 2.0  # rewarded


def test_low_confidence_routes_to_review_not_state_change(db):
    campaign, steps = _setup_campaign(db)
    lead, enrollment = _enroll(db, campaign, state="waiting", step=1)
    variant = db.scalar(select(Variant).where(Variant.step_id == steps[0].id))
    outbound = Message(
        enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
        subject="hello", body="x", smtp_message_id="<msg-2@flowbot.io>",
        bandit_outcome="pending",
        outcome_deadline=datetime.now(timezone.utc) + timedelta(days=3),
        sent_at=datetime.now(timezone.utc),
    )
    db.add(outbound)
    db.flush()

    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="ooo", confidence=0.5))  # ambiguous OOO-with-interest
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: hello",
        body="OOO until Aug 10. PS this looks interesting!",
        in_reply_to="<msg-2@flowbot.io>", references=[], message_id="<r2@acme.com>",
    )
    asyncio.run(handle_inbound(db, llm, inbound))

    assert enrollment.state == "waiting"  # untouched
    assert outbound.bandit_outcome == "pending"  # untouched
    review = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.kind == "classification"))
    assert review is not None and review.payload["confidence"] == 0.5


def test_unsubscribe_reply_suppresses_and_penalizes(db):
    campaign, steps = _setup_campaign(db)
    lead, enrollment = _enroll(db, campaign, state="waiting", step=1)
    variant = db.scalar(select(Variant).where(Variant.step_id == steps[0].id))
    outbound = Message(
        enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
        subject="hello", body="x", smtp_message_id="<msg-3@flowbot.io>",
        bandit_outcome="pending",
        outcome_deadline=datetime.now(timezone.utc) + timedelta(days=3),
        sent_at=datetime.now(timezone.utc),
    )
    db.add(outbound)
    db.flush()

    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="unsubscribe", confidence=0.99))
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: hello", body="Please remove me from your list.",
        in_reply_to="<msg-3@flowbot.io>", references=[], message_id="<r3@acme.com>",
    )
    asyncio.run(handle_inbound(db, llm, inbound))

    assert enrollment.state == "unsubscribed"
    assert is_suppressed(db, lead.email)
    assert outbound.bandit_outcome == "failure"
    db.flush()
    db.refresh(variant)
    assert variant.beta == 2.0


def test_settle_expired_pending_counts_as_failure(db):
    campaign, steps = _setup_campaign(db)
    _, enrollment = _enroll(db, campaign, state="waiting", step=1)
    variant = db.scalar(select(Variant).where(Variant.step_id == steps[0].id))
    db.add(
        Message(
            enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
            subject="x", body="x", smtp_message_id="<old@flowbot.io>",
            bandit_outcome="pending",
            outcome_deadline=datetime.now(timezone.utc) - timedelta(hours=1),
            sent_at=datetime.now(timezone.utc) - timedelta(days=4),
        )
    )
    db.flush()
    settled = settle_expired(db)
    assert settled == 1
    db.flush()
    db.refresh(variant)
    assert variant.beta == 2.0


def test_suppression_is_permanent_for_lead(db):
    campaign, _ = _setup_campaign(db)
    lead, _ = _enroll(db, campaign)
    suppress(db, lead.email, reason="unsubscribe")
    assert is_suppressed(db, lead.email)
    assert lead.status == "suppressed"
