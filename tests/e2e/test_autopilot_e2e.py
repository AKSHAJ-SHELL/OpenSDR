"""M4.4 e2e: Guarded Autopilot through the real worker + API, real Postgres."""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from craftsman.core.models import AuditLog, Message, ReplyDraft
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks as task_mod

from tests.e2e.test_reply_drafts import GOOD_FILL, _auth, _scenario


@pytest.fixture()
def wired(db, monkeypatch):
    """Worker on the test session, mock LLM, dispatch I/O captured, clock-free
    business hours (patched open; the hours gate has its own unit coverage)."""

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)
    llm = MockLLM()
    monkeypatch.setattr(task_mod, "get_llm", lambda: llm)
    delivered = []

    async def _capture(mailbox, msg):
        delivered.append((mailbox, msg))

    monkeypatch.setattr("craftsman.sender.reply.deliver", _capture)
    monkeypatch.setattr("craftsman.sender.reply.acquire_send_slot", lambda *a, **k: 0.0)
    monkeypatch.setattr("craftsman.inbox.autopilot.within_send_window", lambda *a: True)
    return llm, delivered


def _autopilot_scenario(db, **kw):
    enr, lead, campaign, mailbox, outbound, inbound, variant = _scenario(db, **kw)
    campaign.autopilot_enabled = True
    campaign.scheduling_url = "https://cal.test/sam/15min"
    campaign.info_doc_url = "https://flowbot.test/one-pager"
    db.add(campaign)
    db.flush()
    return enr, lead, campaign, mailbox, outbound, inbound, variant


def _second_inbound(db, enr, mailbox, n=2):
    msg = Message(
        enrollment_id=enr.id, direction="inbound", mailbox_id=mailbox.id,
        subject="Re: quick idea for Acme", body=f"Follow-up question number {n}?",
        smtp_message_id=f"<in{n}-{uuid.uuid4().hex[:6]}@acme.test>",
        classification="interested", classification_confidence=0.97,
    )
    db.add(msg)
    db.flush()
    return msg


def test_autopilot_sends_interested_once(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, variant = _autopilot_scenario(db)
    a0, b0 = variant.alpha, variant.beta
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))

    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "sent" and draft.auto_sent is True
    assert len(delivered) == 1
    _, email_msg = delivered[0]
    assert email_msg["In-Reply-To"] == inbound.smtp_message_id
    assert "https://cal.test/sam/15min" in email_msg.get_content()

    audit = db.scalar(select(AuditLog).where(AuditLog.event == "autopilot_send"))
    assert audit is not None and audit.detail["skeleton"] == "reply_interested"
    db.refresh(variant)
    assert (variant.alpha, variant.beta) == (a0, b0)  # bandit untouched
    db.refresh(enr)
    assert enr.state == "replied_interested"  # no sequence advance


def test_second_reply_in_thread_escalates(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _autopilot_scenario(db)
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    assert len(delivered) == 1

    second = _second_inbound(db, enr, mailbox)
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(second.id))

    second_draft = db.scalar(
        select(ReplyDraft).where(ReplyDraft.inbound_message_id == second.id)
    )
    assert second_draft.status == "pending" and second_draft.auto_sent is False
    assert len(delivered) == 1  # ≤ 1 auto-reply per thread, ever
    declined = db.scalar(select(AuditLog).where(AuditLog.event == "autopilot_declined"))
    assert declined.detail["reason"] == "auto_reply_already_sent_in_thread"


def test_low_confidence_stays_pending(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _autopilot_scenario(db)
    inbound.classification_confidence = 0.85
    db.add(inbound)
    db.flush()
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "pending" and not draft.auto_sent
    assert delivered == []


def test_kill_switch_is_instant(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _autopilot_scenario(db)
    campaign.autopilot_enabled = False  # the kill switch's effect
    db.add(campaign)
    db.flush()
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "pending"
    assert delivered == []
    # off = silent: no autopilot audit noise for campaigns that never opted in
    assert db.scalar(select(AuditLog).where(AuditLog.event == "autopilot_declined")) is None


def test_escalation_block_autopilot_rule_stops_send(db, wired):
    from craftsman.core.models import EscalationRule

    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _autopilot_scenario(db)
    db.add(EscalationRule(
        campaign_id=campaign.id,
        name="vip-accounts-human-only",
        match={"keywords_any": ["pick orders"]},  # matches the scenario reply text
        actions={"block_autopilot": True},
    ))
    db.flush()
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "pending"
    assert delivered == []


def test_missing_scheduling_url_escalates_interested(db, wired):
    llm, delivered = wired
    enr, lead, campaign, mailbox, outbound, inbound, _ = _autopilot_scenario(db)
    campaign.scheduling_url = None
    db.add(campaign)
    db.flush()
    llm.enqueue(GOOD_FILL)
    task_mod.generate_reply_draft.run(str(inbound.id))
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.inbound_message_id == inbound.id))
    assert draft.status == "pending"
    assert delivered == []


# ---------------------------------------------------------------- enable/disable API


def test_enable_requires_admin(client, db, make_key):
    enr, lead, campaign, *_ = _scenario(db)
    operate = make_key("read", "operate")
    r = client.post(f"/campaigns/{campaign.id}/autopilot/enable", headers=_auth(operate))
    assert r.status_code == 403  # deliberate friction: operate is not enough
    db.refresh(campaign)
    assert campaign.autopilot_enabled is False

    admin = make_key("admin")
    r = client.post(f"/campaigns/{campaign.id}/autopilot/enable", headers=_auth(admin))
    assert r.status_code == 200 and r.json() == {"autopilot_enabled": True}
    db.refresh(campaign)
    assert campaign.autopilot_enabled is True
    audit = db.scalar(select(AuditLog).where(AuditLog.event == "autopilot_enabled"))
    assert audit.detail["campaign_id"] == str(campaign.id)


def test_disable_needs_only_operate(client, db, make_key):
    enr, lead, campaign, *_ = _scenario(db)
    campaign.autopilot_enabled = True
    db.add(campaign)
    db.flush()
    operate = make_key("operate")
    r = client.post(f"/campaigns/{campaign.id}/autopilot/disable", headers=_auth(operate))
    assert r.status_code == 200 and r.json() == {"autopilot_enabled": False}
    db.refresh(campaign)
    assert campaign.autopilot_enabled is False
    assert db.scalar(select(AuditLog).where(AuditLog.event == "autopilot_disabled")) is not None


def test_campaign_out_exposes_flag(client, db, make_key):
    enr, lead, campaign, *_ = _scenario(db)
    token = make_key("read")
    r = client.get(f"/campaigns/{campaign.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["autopilot_enabled"] is False
