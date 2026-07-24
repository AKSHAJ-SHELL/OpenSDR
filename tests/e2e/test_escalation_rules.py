"""M4.2 e2e: escalation through the real pipeline + rules CRUD."""

import asyncio
import uuid

from sqlalchemy import select

from craftsman.compliance.suppression import is_suppressed
from craftsman.core.models import EscalationRule, ReviewQueueItem
from craftsman.core.schemas import ReplyClassification
from craftsman.inbox.pipeline import handle_inbound
from craftsman.inbox.poller import InboundEmail
from craftsman.llm.mock_impl import MockLLM

from tests.e2e.test_reply_drafts import _auth, _scenario

LEGAL_REPLY = (
    "Remove me immediately. This is harassment and I have forwarded this thread to "
    "our lawyer. Delete my data per GDPR."
)


def _inbound(lead, outbound, body):
    return InboundEmail(
        from_addr=lead.email, subject="Re: quick idea for Acme", body=body,
        in_reply_to=outbound.smtp_message_id, references=[],
        message_id=f"<{uuid.uuid4().hex[:8]}@acme.test>",
    )


def test_legal_threat_suppresses_blocks_draft_and_files_review(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    # classifier calls it a confident objection — a draft would normally follow
    llm.enqueue(ReplyClassification(label="objection", confidence=0.95))
    queued = []
    asyncio.run(handle_inbound(
        db, llm, _inbound(lead, outbound, LEGAL_REPLY), enqueue_draft=queued.append
    ))
    assert queued == []  # block_draft: never a draft for a legal threat
    assert is_suppressed(db, lead.email)
    item = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.kind == "escalation"))
    assert item is not None
    assert "builtin:legal-threat" in item.payload["rules"]
    assert item.payload["suppressed"] is True


def test_legal_threat_fires_even_on_low_confidence(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="not_now", confidence=0.4))
    queued = []
    asyncio.run(handle_inbound(
        db, llm, _inbound(lead, outbound, LEGAL_REPLY), enqueue_draft=queued.append
    ))
    assert queued == []
    assert is_suppressed(db, lead.email)  # suppressed despite the hedging classifier
    # one review item from the low-confidence route (no duplicate escalation item)
    kinds = [r.kind for r in db.scalars(select(ReviewQueueItem)).all()]
    assert kinds.count("classification") == 1
    assert kinds.count("escalation") == 0


def test_benign_interested_still_drafts_and_does_not_suppress(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.95))
    queued = []
    msg = asyncio.run(handle_inbound(
        db, llm, _inbound(lead, outbound, "Sounds great, how does it work?"),
        enqueue_draft=queued.append,
    ))
    assert queued == [msg.id]
    assert not is_suppressed(db, lead.email)
    assert db.scalar(select(ReviewQueueItem)) is None


def test_campaign_rule_blocks_draft(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    db.add(EscalationRule(
        campaign_id=campaign.id,
        name="competitor-mentions-need-a-human",
        match={"keywords_any": ["Attentive"]},
        actions={"block_draft": True, "review_queue": True},
    ))
    db.flush()
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="objection", confidence=0.9))
    queued = []
    asyncio.run(handle_inbound(
        db, llm, _inbound(lead, outbound, "We already use Attentive for this."),
        enqueue_draft=queued.append,
    ))
    assert queued == []
    assert not is_suppressed(db, lead.email)  # rule only blocked the draft
    item = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.kind == "escalation"))
    assert item is not None and "competitor-mentions-need-a-human" in item.payload["rules"]


def test_global_rule_applies_to_any_campaign(db):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db, state="waiting")
    db.add(EscalationRule(
        campaign_id=None,  # global
        name="global-vip-domains",
        match={"keywords_any": ["board meeting"]},
        actions={"urgent_notify": True},
    ))
    db.flush()
    llm = MockLLM()
    llm.enqueue(ReplyClassification(label="interested", confidence=0.95))
    queued = []
    msg = asyncio.run(handle_inbound(
        db, llm, _inbound(lead, outbound, "Bring this to our board meeting next week."),
        enqueue_draft=queued.append,
    ))
    assert queued == [msg.id]  # urgent_notify alone doesn't block drafting


# ---------------------------------------------------------------- CRUD


def test_escalation_rules_crud_and_builtins(client, db, make_key):
    enr, lead, campaign, mailbox, outbound, _, _ = _scenario(db)
    read = make_key("read")
    operate = make_key("read", "operate")

    listed = client.get(
        f"/campaigns/{campaign.id}/escalation-rules", headers=_auth(read)
    )
    assert listed.status_code == 200
    builtins = [r for r in listed.json() if r["builtin"]]
    assert {b["name"] for b in builtins} == {
        "builtin:legal-threat", "builtin:interested-notify",
    }

    created = client.post(
        f"/campaigns/{campaign.id}/escalation-rules",
        json={
            "name": "pricing-to-review",
            "match": {"classifications": ["objection"], "keywords_any": ["pricing"]},
            "actions": {"review_queue": True, "block_autopilot": True},
        },
        headers=_auth(operate),
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    listed = client.get(
        f"/campaigns/{campaign.id}/escalation-rules", headers=_auth(read)
    ).json()
    assert any(r["id"] == rule_id and not r["builtin"] for r in listed)

    # read scope cannot create/delete
    assert client.post(
        f"/campaigns/{campaign.id}/escalation-rules",
        json={"name": "x"}, headers=_auth(read),
    ).status_code == 403
    assert client.delete(
        f"/campaigns/{campaign.id}/escalation-rules/{rule_id}", headers=_auth(operate)
    ).status_code == 204
    assert client.delete(
        f"/campaigns/{campaign.id}/escalation-rules/{rule_id}", headers=_auth(operate)
    ).status_code == 404
