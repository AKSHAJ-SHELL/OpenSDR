"""M4.1 e2e: Copilot reply drafts against real Postgres.

Covers: pipeline → draft generation (idempotent, suppression-gated, review-queue on
double reject) → inbox API (list / send / edit-send / discard) → dispatch through
the send engine (threading, no bandit, no sequence advance) → erasure.
"""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from craftsman.core.models import (
    Campaign,
    Company,
    Enrollment,
    Lead,
    Mailbox,
    Message,
    ReplyDraft,
    ReviewQueueItem,
    SequenceStep,
    Variant,
)
from craftsman.core.schemas import ReplyClassification, ReplyDraftFill
from craftsman.inbox.pipeline import handle_inbound
from craftsman.inbox.poller import InboundEmail
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks as task_mod

GOOD_FILL = ReplyDraftFill(
    objection_kind="other",
    acknowledgment="You said you still pick orders by hand.",
    answer_bridge="Flowbot cuts picking costs by a third.",
    cta_question="Worth a quick look?",
)

BRIEF_JSON = {
    "what_they_do": "Acme Robotics builds warehouse automation robots.",
    "industry": "logistics",
    "trigger_events": [],
    "likely_pain_points": ["manual picking costs"],
    "evidence_quotes": [],
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _scenario(db, *, state="replied_interested", classification="interested"):
    """Thread that already replied: outbound (with mailbox) + classified inbound."""
    company = Company(domain=f"rd-{uuid.uuid4().hex[:8]}.test", name="Acme Robotics",
                      research_brief=BRIEF_JSON)
    db.add(company)
    db.flush()
    campaign = Campaign(
        name="rd", icp_description="x",
        value_prop="Flowbot cuts picking costs by a third.",
        sender_persona={"name": "Sam", "title": "Founder", "company": "Flowbot"},
    )
    db.add(campaign)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=1)
    db.add(step)
    db.flush()
    variant = Variant(step_id=step.id, name="v1", skeleton="Subject: {{subject_hook}}\n\nx",
                      slot_schema={}, alpha=1.0, beta=1.0)
    db.add(variant)
    mailbox = Mailbox(
        email=f"sam@rd-{uuid.uuid4().hex[:6]}.test",
        smtp_host="smtp.test", smtp_port=587, smtp_user="u",
        daily_limit=40, warmup_stage=4,
    )
    db.add(mailbox)
    lead = Lead(email=f"{uuid.uuid4().hex[:8]}@rd.test", company_id=company.id,
                status="verified", first_name="Dana", timezone="UTC")
    db.add(lead)
    db.flush()
    enr = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state=state, current_step=1)
    db.add(enr)
    db.flush()
    outbound = Message(
        enrollment_id=enr.id, variant_id=variant.id, direction="outbound", step_order=1,
        mailbox_id=mailbox.id, subject="quick idea for Acme", body="original",
        smtp_message_id=f"<out-{uuid.uuid4().hex[:8]}@rd.test>",
        bandit_outcome="success",
        sent_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(outbound)
    db.flush()
    inbound = Message(
        enrollment_id=enr.id, direction="inbound", mailbox_id=mailbox.id,
        subject="Re: quick idea for Acme",
        body="Sounds interesting. How does it work? We still pick orders by hand.",
        smtp_message_id=f"<in-{uuid.uuid4().hex[:8]}@acme.test>",
        classification=classification, classification_confidence=0.95,
    )
    db.add(inbound)
    db.flush()
    return enr, lead, campaign, mailbox, outbound, inbound, variant


@pytest.fixture()
def wired(db, monkeypatch):
    """Draft worker wired to the test transaction, mock LLM, SMTP tripwired
    (generation must never send; dispatch tests re-patch deliver explicitly)."""

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    llm = MockLLM()
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)

    async def _boom(*a, **k):
        raise AssertionError("SMTP deliver() invoked during draft generation")

    monkeypatch.setattr("craftsman.sender.reply.deliver", _boom)
    return llm


@pytest.fixture()
def sendable(db, monkeypatch):
    """Patch dispatch I/O: capture deliveries, rate limiter always clear."""
    delivered = []

    async def _capture(mailbox, msg):
        delivered.append((mailbox, msg))

    monkeypatch.setattr("craftsman.sender.reply.deliver", _capture)
    monkeypatch.setattr("craftsman.sender.reply.acquire_send_slot", lambda *a, **k: 0.0)
    return delivered


def _draft(db, enr, inbound, *, status="pending", body="Hi Dana,\n\ndraft body.\n\nSam"):
    d = ReplyDraft(
        inbound_message_id=inbound.id, enrollment_id=enr.id,
        skeleton_key="reply_interested", slots={}, body=body, status=status,
    )
    db.add(d)
    db.flush()
    return d


# ---------------------------------------------------------------- generation


def test_pipeline_enqueues_draft_for_interested(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.95))
    queued = []
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: quick idea for Acme", body="Tell me more!",
        in_reply_to=outbound.smtp_message_id, references=[],
        message_id="<r@acme.test>",
    )
    msg = asyncio.run(handle_inbound(db, llm, inbound, enqueue_draft=queued.append))
    assert queued == [msg.id]


def test_pipeline_does_not_enqueue_for_unsubscribe(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="unsubscribe", confidence=0.99))
    queued = []
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: quick idea for Acme", body="Remove me.",
        in_reply_to=outbound.smtp_message_id, references=[],
        message_id="<r2@acme.test>",
    )
    asyncio.run(handle_inbound(db, llm, inbound, enqueue_draft=queued.append))
    assert queued == []


def test_low_confidence_does_not_enqueue(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.5))
    queued = []
    inbound = InboundEmail(
        from_addr=lead.email, subject="Re: quick idea for Acme", body="hmm",
        in_reply_to=outbound.smtp_message_id, references=[],
        message_id="<r3@acme.test>",
    )
    asyncio.run(handle_inbound(db, llm, inbound, enqueue_draft=queued.append))
    assert queued == []  # review queue instead; no draft for an unconfident label


def test_worker_generates_pending_draft(db, wired):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    wired.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft is not None and draft.status == "pending"
    assert draft.skeleton_key == "reply_interested"
    assert draft.body.startswith("Hi Dana,")
    assert draft.detail == {"attempts": 1}


def _real_scope(engine):
    """Production-shaped session_scope against the test DB — for tests whose subject
    is the worker's own commit/rollback behavior (claim durability, duplicate path).
    Same harness pattern as test_send_idempotency / test_duplicate_generation."""
    from sqlalchemy.orm import Session

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

    return scope


def _cleanup(engine, enr_id):
    from sqlalchemy.orm import Session

    from craftsman.core.models import AuditLog

    with Session(bind=engine) as s:
        enr = s.get(Enrollment, enr_id)
        if enr is None:
            return
        campaign = s.get(Campaign, enr.campaign_id)
        lead = s.get(Lead, enr.lead_id)
        s.query(ReplyDraft).filter(ReplyDraft.enrollment_id == enr_id).delete()
        s.query(ReviewQueueItem).filter(ReviewQueueItem.enrollment_id == enr_id).delete()
        s.query(Message).filter(Message.enrollment_id == enr_id).delete()
        s.query(AuditLog).filter(AuditLog.enrollment_id == enr_id).delete()
        s.query(Enrollment).filter(Enrollment.id == enr_id).delete()
        for step in s.query(SequenceStep).filter(SequenceStep.campaign_id == campaign.id):
            s.query(Variant).filter(Variant.step_id == step.id).delete()
            s.delete(step)
        company_id = lead.company_id
        s.delete(lead)
        s.delete(campaign)
        if company_id:
            company = s.get(Company, company_id)
            if company is not None:
                s.delete(company)
        s.commit()


def test_worker_is_idempotent(engine, monkeypatch):
    from sqlalchemy.orm import Session

    with Session(bind=engine) as setup:
        enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(setup)
        enr_id, inbound_id = enr.id, str(inbound.id)
        setup.commit()

    monkeypatch.setattr(task_mod, "session_scope", _real_scope(engine))
    llm = MockLLM()
    llm.enqueue(GOOD_FILL)
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)

    try:
        task_mod.generate_reply_draft.run(inbound_id)
        task_mod.generate_reply_draft.run(inbound_id)  # no queued fill: must not LLM
        with Session(bind=engine) as s:
            drafts = s.scalars(
                select(ReplyDraft).where(
                    ReplyDraft.inbound_message_id == uuid.UUID(inbound_id)
                )
            ).all()
            assert len(drafts) == 1
            assert drafts[0].status == "pending"
    finally:
        _cleanup(engine, enr_id)


def test_suppressed_lead_gets_no_draft(db, wired):
    from craftsman.compliance.suppression import suppress

    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    suppress(db, lead.email, reason="unsubscribe")
    task_mod.generate_reply_draft.run(str(inbound.id))
    assert db.scalar(select(ReplyDraft)) is None


def test_double_reject_fails_to_review_queue(engine, monkeypatch):
    from sqlalchemy.orm import Session

    with Session(bind=engine) as setup:
        enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(setup)
        enr_id, inbound_id = enr.id, str(inbound.id)
        setup.commit()

    monkeypatch.setattr(task_mod, "session_scope", _real_scope(engine))
    llm = MockLLM()
    bad = GOOD_FILL.model_copy(update={"answer_bridge": "Our customer DataDog uses us."})
    llm.enqueue(bad)
    llm.enqueue(bad)
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)

    try:
        task_mod.generate_reply_draft.run(inbound_id)
        with Session(bind=engine) as s:
            draft = s.scalar(
                select(ReplyDraft).where(
                    ReplyDraft.inbound_message_id == uuid.UUID(inbound_id)
                )
            )
            assert draft.status == "failed"
            item = s.scalar(
                select(ReviewQueueItem).where(ReviewQueueItem.kind == "reply_draft")
            )
            assert item is not None
            assert any("DataDog" in e for e in item.payload["errors"])
    finally:
        _cleanup(engine, enr_id)


def test_objection_other_records_skip(db, wired):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(
        db, state="replied_objection", classification="objection"
    )
    wired.enqueue(GOOD_FILL)  # objection_kind == other
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "skipped"
    assert draft.detail == {"reason": "objection_needs_human"}


# ---------------------------------------------------------------- dispatch (API)


def test_send_draft_full_path(client, db, make_key, sendable):
    enr, lead, campaign, mailbox, outbound, inbound, variant = _scenario(db)
    draft = _draft(db, enr, inbound)
    a0, b0 = variant.alpha, variant.beta
    token = make_key("read", "operate")

    r = client.post(f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "sent"
    assert body["auto_sent"] is False

    assert len(sendable) == 1
    _, email_msg = sendable[0]
    assert email_msg["In-Reply-To"] == inbound.smtp_message_id
    assert email_msg["Subject"] == "Re: quick idea for Acme"
    assert email_msg["From"] == mailbox.email
    assert email_msg["To"] == lead.email

    sent = db.scalar(select(Message).where(
        Message.enrollment_id == enr.id, Message.direction == "outbound",
        Message.step_order.is_(None),
    ))
    assert sent is not None and sent.sent_at is not None
    assert sent.bandit_outcome is None  # replies never settle into the bandit

    db.refresh(enr)
    assert enr.state == "replied_interested"  # no sequence advance
    db.refresh(variant)
    assert (variant.alpha, variant.beta) == (a0, b0)  # posteriors untouched


def test_double_send_is_409_single_email(client, db, make_key, sendable):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    token = make_key("operate")
    assert client.post(f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token)).status_code == 200
    r = client.post(f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token))
    assert r.status_code == 409
    assert len(sendable) == 1


def test_edited_send_revalidates_and_marks_edited(client, db, make_key, sendable):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    token = make_key("operate")
    edited = "Hi Dana,\n\nFlowbot cuts picking costs by a third. Worth a look?\n\nSam"
    r = client.post(
        f"/inbox/drafts/{draft.id}/send", json={"body": edited}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "edited_sent"
    _, email_msg = sendable[0]
    assert "Worth a look?" in email_msg.get_content()


def test_invalid_edit_is_422_draft_stays_pending(client, db, make_key, sendable):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    token = make_key("operate")
    r = client.post(
        f"/inbox/drafts/{draft.id}/send",
        json={"body": "Hi Dana, we can offer a discount and our customer DataDog loves us."},
        headers=_auth(token),
    )
    assert r.status_code == 422
    errors = r.json()["detail"]["validation_errors"]
    assert any("discount" in e for e in errors)
    assert any("DataDog" in e for e in errors)
    assert sendable == []
    db.refresh(draft)
    assert draft.status == "pending"


def test_rate_limited_send_is_429_draft_pending(client, db, make_key, monkeypatch):
    async def _nope(*a, **k):
        raise AssertionError("must not deliver while rate limited")

    monkeypatch.setattr("craftsman.sender.reply.deliver", _nope)
    monkeypatch.setattr("craftsman.sender.reply.acquire_send_slot", lambda *a, **k: 30.0)
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    token = make_key("operate")
    r = client.post(f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token))
    assert r.status_code == 429
    db.refresh(draft)
    assert draft.status == "pending"


def test_suppressed_at_send_discards_draft(client, db, make_key, sendable):
    from craftsman.compliance.suppression import suppress

    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    suppress(db, lead.email, reason="unsubscribe")
    token = make_key("operate")
    r = client.post(f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token))
    assert r.status_code == 409
    assert sendable == []
    db.refresh(draft)
    assert draft.status == "discarded"
    assert draft.detail["reason"] == "suppressed_at_send"


def test_discard_then_send_is_409(client, db, make_key, sendable):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    draft = _draft(db, enr, inbound)
    token = make_key("operate")
    r = client.post(f"/inbox/drafts/{draft.id}/discard", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "discarded"
    assert client.post(
        f"/inbox/drafts/{draft.id}/discard", headers=_auth(token)
    ).status_code == 409
    assert client.post(
        f"/inbox/drafts/{draft.id}/send", json={}, headers=_auth(token)
    ).status_code == 409
    assert sendable == []


def test_drafts_list_and_scopes(client, db, make_key):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _draft(db, enr, inbound)
    assert client.get("/inbox/drafts").status_code == 401
    token = make_key("read")
    listed = client.get("/inbox/drafts", headers=_auth(token))
    assert listed.status_code == 200
    item = listed.json()[0]
    assert item["lead_email"] == lead.email
    assert item["campaign_name"] == "rd"
    assert item["inbound_body"].startswith("Sounds interesting")
    # read scope cannot send
    r = client.post(f"/inbox/drafts/{item['id']}/send", json={}, headers=_auth(token))
    assert r.status_code == 403


# ---------------------------------------------------------------- erasure + metrics


def test_erase_lead_deletes_reply_drafts(db):
    from craftsman.compliance.suppression import erase_lead

    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _draft(db, enr, inbound)
    erase_lead(db, lead)
    db.flush()
    assert db.scalar(select(ReplyDraft)) is None
    assert db.scalar(select(Message)) is None


def test_analytics_reports_acceptance_rate(client, db, make_key):
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _draft(db, enr, inbound, status="sent")
    d2 = ReplyDraft(inbound_message_id=outbound.id, enrollment_id=enr.id,
                    status="discarded", body="x")
    db.add(d2)
    db.flush()
    token = make_key("read")
    data = client.get("/analytics/overview", headers=_auth(token)).json()
    assert data["reply_drafts"]["sent"] == 1
    assert data["reply_drafts"]["discarded"] == 1
    assert data["draft_acceptance_rate"] == 0.5
