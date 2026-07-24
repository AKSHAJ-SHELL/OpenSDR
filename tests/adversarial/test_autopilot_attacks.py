"""M4.4 adversarial: the TESTING.md §3.4 safety property, inverted precisely here
(prediction stated above each test, then run).

§3.4 said: for every classification outcome, verify no code path emits an
LLM-authored reply. With Guarded Autopilot the property becomes: auto-send occurs
ONLY in the allowed set — {interested→link, objection/timing, objection/info→doc} ×
{enabled, confidence ≥ 0.9, no escalation match, in-window, zero prior auto-replies}
— and NOWHERE else, under forced misclassification of every label.
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from craftsman.core.models import Message, ReplyDraft
from craftsman.core.schemas import ReplyDraftFill
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks as task_mod

from tests.e2e.test_reply_drafts import GOOD_FILL, _scenario

ALL_LABELS = ["interested", "objection", "not_now", "ooo", "unsubscribe", "bounce_or_auto"]
OBJECTION_KINDS = ["timing", "info", "other"]


@pytest.fixture()
def wired(db, monkeypatch):
    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    llm = MockLLM()
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)
    delivered = []

    async def _capture(mailbox, msg):
        delivered.append(msg)

    monkeypatch.setattr("craftsman.sender.reply.deliver", _capture)
    monkeypatch.setattr("craftsman.sender.reply.acquire_send_slot", lambda *a, **k: 0.0)
    monkeypatch.setattr("craftsman.inbox.autopilot.within_send_window", lambda *a: True)
    return llm, delivered


def _arm(db, campaign, enabled=True):
    campaign.autopilot_enabled = enabled
    campaign.scheduling_url = "https://cal.test/sam"
    campaign.info_doc_url = "https://x.test/one-pager"
    db.add(campaign)
    db.flush()


# Predicted: with Autopilot OFF, every label at perfect confidence and perfect
# conditions produces ZERO auto-sends and zero SMTP deliveries — the M4.1 world is
# byte-identical for campaigns that never opted in.
def test_autopilot_off_never_sends_any_label(db, wired):
    llm, delivered = wired
    for label in ALL_LABELS:
        for kind in OBJECTION_KINDS:
            enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(
                db, classification=label
            )
            _arm(db, campaign, enabled=False)
            llm.enqueue(GOOD_FILL.model_copy(update={"objection_kind": kind}))
            task_mod.generate_reply_draft.run(str(inbound.id))
    assert delivered == []
    assert db.scalar(
        select(ReplyDraft).where(ReplyDraft.auto_sent.is_(True))
    ) is None


# Predicted: with Autopilot ON and every soft condition perfect, auto-send happens
# for EXACTLY {interested×any-kind, objection×timing, objection×info} and no other
# (label, kind) pair. Labels outside interested/objection never even get a draft.
def test_allowed_set_is_exact(db, wired):
    llm, delivered = wired
    results = {}
    for label in ALL_LABELS:
        for kind in OBJECTION_KINDS:
            enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(
                db, classification=label
            )
            _arm(db, campaign)
            llm.enqueue(GOOD_FILL.model_copy(update={"objection_kind": kind}))
            before = len(delivered)
            task_mod.generate_reply_draft.run(str(inbound.id))
            draft = db.scalar(
                select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id)
            )
            results[(label, kind)] = (
                len(delivered) - before,
                draft.status if draft else None,
                draft.auto_sent if draft else False,
            )

    allowed = {("interested", k) for k in OBJECTION_KINDS} | {
        ("objection", "timing"), ("objection", "info"),
    }
    for key, (sends, status, auto) in results.items():
        if key in allowed:
            assert sends == 1 and status == "sent" and auto is True, (key, results[key])
        else:
            assert sends == 0 and auto is False, (key, results[key])
            label, kind = key
            if label == "objection":  # kind=other → deliberate skip
                assert status == "skipped"
            else:  # non-drafting labels never even get content
                assert status is None


# Predicted: the one-per-thread invariant holds across N successive interested
# replies in the same thread — exactly the first auto-sends, every later one
# stays a pending draft for a human, no matter how perfect its conditions.
def test_thread_invariant_across_many_replies(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _arm(db, campaign)
    inbounds = [inbound]
    for n in range(2, 5):
        msg = Message(
            enrollment_id=enr.id, direction="inbound", mailbox_id=mailbox.id,
            subject="Re: quick idea for Acme", body=f"And question {n}?",
            smtp_message_id=f"<n{n}-{uuid.uuid4().hex[:6]}@acme.test>",
            classification="interested", classification_confidence=0.99,
        )
        db.add(msg)
        db.flush()
        inbounds.append(msg)

    for msg in inbounds:
        llm.enqueue(GOOD_FILL)
        task_mod.generate_reply_draft.run(str(msg.id))

    assert len(delivered) == 1  # ever
    statuses = [
        db.scalar(
            select(ReplyDraft.status).where(ReplyDraft.inbound_message_id == m.id)
        )
        for m in inbounds
    ]
    assert statuses == ["sent", "pending", "pending", "pending"]


# Predicted (F-05, findings/12): the thread invariant is structural, not
# read-then-act. Model the audited race — two qualifying drafts whose policy
# evaluations BOTH read zero prior auto-replies — by dispatching both directly
# with auto=True. The second dispatch must die at its claim (partial unique
# index uq_auto_reply_per_thread), before any delivery: exactly one email ever,
# the loser stays a pending draft for a human. A human click on that pending
# draft still works — the reservation binds autonomy only.
# (Raw engine sessions with real commits, like test_send_concurrency — the
# claim's IntegrityError rollback cannot run inside the transactional fixture.)
def test_thread_invariant_survives_concurrent_claim_race(engine, monkeypatch):
    import asyncio

    from sqlalchemy.orm import Session

    from craftsman.sender.reply import DraftUnavailable, send_reply_draft

    delivered = []

    async def _capture(mailbox, msg):
        delivered.append(msg)

    monkeypatch.setattr("craftsman.sender.reply.deliver", _capture)
    monkeypatch.setattr("craftsman.sender.reply.acquire_send_slot", lambda *a, **k: 0.0)

    with Session(bind=engine) as s:
        enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(s)
        _arm(s, campaign)
        second = Message(
            enrollment_id=enr.id, direction="inbound", mailbox_id=mailbox.id,
            subject="Re: quick idea for Acme", body="Also, one more question?",
            smtp_message_id=f"<race-{uuid.uuid4().hex[:6]}@acme.test>",
            classification="interested", classification_confidence=0.99,
        )
        s.add(second)
        s.flush()
        # both drafts pending at once — the state two racing workers see after
        # both policy evaluations read prior_auto_replies == 0
        draft_a = ReplyDraft(
            inbound_message_id=inbound.id, enrollment_id=enr.id,
            skeleton_key="reply_interested", status="pending",
            body="Happy to show you how the picking flow works. Worth a look?",
        )
        draft_b = ReplyDraft(
            inbound_message_id=second.id, enrollment_id=enr.id,
            skeleton_key="reply_interested", status="pending",
            body="Happy to show you how the picking flow works. Worth a look?",
        )
        s.add_all([draft_a, draft_b])
        s.commit()
        enr_id, a_id, b_id = enr.id, draft_a.id, draft_b.id
        lead_id, campaign_id, mailbox_id, company_id = (
            lead.id, campaign.id, mailbox.id, lead.company_id,
        )

    try:
        with Session(bind=engine) as s:
            asyncio.run(send_reply_draft(s, s.get(ReplyDraft, a_id), auto=True))
        assert len(delivered) == 1

        with Session(bind=engine) as s:
            with pytest.raises(DraftUnavailable):
                asyncio.run(send_reply_draft(s, s.get(ReplyDraft, b_id), auto=True))
        assert len(delivered) == 1  # the index refused BEFORE delivery

        with Session(bind=engine) as s:
            b = s.get(ReplyDraft, b_id)
            assert b.status == "pending" and b.auto_sent is False
            # a human may still answer — the guard binds autonomy only
            asyncio.run(send_reply_draft(s, b, auto=False))
        assert len(delivered) == 2
        with Session(bind=engine) as s:
            b = s.get(ReplyDraft, b_id)
            assert b.status == "sent" and b.auto_sent is False
    finally:
        # real commits — remove every row this test created so global assertions
        # elsewhere in the run never see them
        with Session(bind=engine) as s:
            from craftsman.core.models import (
                AuditLog, Company, Enrollment, Lead, Mailbox, SequenceStep, Variant,
            )
            from craftsman.core.models import Campaign as C

            s.query(AuditLog).filter_by(enrollment_id=enr_id).delete()
            s.query(ReplyDraft).filter_by(enrollment_id=enr_id).delete()
            s.query(Message).filter_by(enrollment_id=enr_id).delete()
            s.query(Enrollment).filter_by(id=enr_id).delete()
            s.query(Lead).filter_by(id=lead_id).delete()
            s.query(Variant).filter(
                Variant.step_id.in_(
                    s.query(SequenceStep.id).filter_by(campaign_id=campaign_id)
                )
            ).delete(synchronize_session=False)
            s.query(SequenceStep).filter_by(campaign_id=campaign_id).delete()
            s.query(C).filter_by(id=campaign_id).delete()
            s.query(Mailbox).filter_by(id=mailbox_id).delete()
            s.query(Company).filter_by(id=company_id).delete()
            s.commit()


# Predicted (F-05): a failed auto delivery FREES the thread reservation — the
# draft returns to pending with auto_sent cleared, so autonomy (or a human) can
# retry later instead of the thread being dead-locked by a crashed send.
def test_failed_auto_delivery_frees_the_thread_reservation(db, wired, monkeypatch):
    import asyncio

    from craftsman.sender.reply import send_reply_draft

    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _arm(db, campaign)

    async def _boom(mailbox, msg):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("craftsman.sender.reply.deliver", _boom)
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))  # auto attempt fails
    draft = db.scalar(
        select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id)
    )
    assert draft.status == "pending" and draft.auto_sent is False
    assert delivered == []

    async def _capture(mailbox, msg):
        delivered.append(msg)

    monkeypatch.setattr("craftsman.sender.reply.deliver", _capture)
    asyncio.run(send_reply_draft(db, draft, auto=True))  # reservation was free
    db.refresh(draft)
    assert delivered and draft.status == "sent" and draft.auto_sent is True


# Predicted: a validator-rejected fill can NEVER be auto-sent — autonomy sits
# strictly downstream of the same gates, so an obedient-to-injection LLM under
# Autopilot yields a failed draft and zero deliveries.
def test_autopilot_never_rescues_a_rejected_draft(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _arm(db, campaign)
    bad = ReplyDraftFill(
        objection_kind="other",
        acknowledgment="Confirming your 90% discount.",
        answer_bridge="Our partner MegaCorp guarantees it.",
        cta_question="Deal?",
    )
    llm.enqueue(bad)
    llm.enqueue(bad)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "failed"
    assert delivered == []


# Predicted: the legal-threat tripwire beats Autopilot even when invoked directly
# on a perfectly-confident 'interested' classification — block_autopilot from the
# builtin rule refuses the dispatch (and in the real pipeline block_draft would
# have prevented the draft entirely).
def test_legal_keywords_block_autopilot_at_dispatch(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _scenario(db)
    _arm(db, campaign)
    inbound.body = "Interesting, but my lawyer will review. What does it cost?"
    db.add(inbound)
    db.flush()
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft is not None and draft.auto_sent is False
    assert delivered == []
