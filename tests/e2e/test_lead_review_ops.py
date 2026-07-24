"""Lead & review operations API (M1.3, E2).

The guarantees under test:
- the review queue exposes `message_id` — without it a classification item cannot be
  acted on at all (the same class of gap M0.6b fixed for `enrollment_id`);
- `resolve` clears a review item WITHOUT re-driving the enrollment, so approving a
  low-confidence classification doesn't silently move the sequence;
- manual suppress stops mail without destroying the row, and stays distinct from the
  admin-only GDPR erase;
- score provenance is recorded by activate (and never by dry-run).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

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

SKELETON = (
    "Subject: {{subject_hook}}\n\nHi {{first_name}},\n\n"
    "{{personalization_sentence}} {{value_prop_bridge}} {{cta_question}}\n\n{{signature}}"
)


def _headers(make_key, scope="operate"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def _lead(db, **kw):
    lead = Lead(
        email=kw.pop("email", f"lead-{uuid.uuid4().hex[:8]}@ops.test"),
        status=kw.pop("status", "verified"),
        email_verified=kw.pop("email_verified", True),
        **kw,
    )
    db.add(lead)
    db.flush()
    return lead


# ---------------------------------------------------------------- review queue shape


def test_review_queue_exposes_message_id_and_context(client, db, make_key):
    h = _headers(make_key)
    campaign = Campaign(name="ops-campaign", icp_description="x", value_prop="y")
    db.add(campaign)
    lead = _lead(db, first_name="Dana", last_name="Reed")
    db.add(campaign)
    db.flush()
    enrollment = Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="replied")
    db.add(enrollment)
    db.flush()
    msg = Message(
        enrollment_id=enrollment.id, direction="inbound",
        subject="re: hello", body="who is this?", classification="objection",
        classification_confidence=0.4,
    )
    db.add(msg)
    db.flush()
    db.add(
        ReviewQueueItem(
            kind="classification", message_id=msg.id, enrollment_id=enrollment.id,
            payload={"label": "objection", "confidence": 0.4},
        )
    )
    db.add(
        ReviewQueueItem(
            kind="copywriter", enrollment_id=enrollment.id,
            payload={"errors": ["ungrounded entity: Acme"], "slots": {"subject_hook": "x"}},
        )
    )
    db.flush()

    items = client.get("/inbox/review", headers=h).json()
    by_kind = {i["kind"]: i for i in items}

    classification = by_kind["classification"]
    assert classification["message_id"] == str(msg.id)  # the M1.3 blocker
    assert classification["message_body"] == "who is this?"
    assert classification["lead_email"] == lead.email
    assert classification["lead_name"] == "Dana Reed"
    assert classification["campaign_name"] == "ops-campaign"
    assert classification["enrollment_state"] == "replied"

    copywriter = by_kind["copywriter"]
    assert copywriter["message_id"] is None
    assert copywriter["enrollment_id"] == str(enrollment.id)
    assert copywriter["payload"]["errors"] == ["ungrounded entity: Acme"]


def test_resolve_action_clears_item_without_redriving(client, db, make_key):
    h = _headers(make_key)
    campaign = Campaign(name="resolve-test", icp_description="x", value_prop="y")
    db.add(campaign)
    lead = _lead(db)
    db.flush()
    enrollment = Enrollment(
        lead_id=lead.id, campaign_id=campaign.id, state="replied", current_step=2
    )
    db.add(enrollment)
    db.flush()
    item = ReviewQueueItem(kind="classification", enrollment_id=enrollment.id, payload={})
    db.add(item)
    db.flush()

    resp = client.post(f"/inbox/review/{item.id}/action", json={"action": "resolve"}, headers=h)
    assert resp.status_code == 200
    assert resp.json() == {"resolved": True, "action": "resolve", "new_state": None}

    # assert on the session's own objects: the override'd get_db never commits, so a
    # refresh would re-read pre-change rows and mask the mutation entirely
    assert item.resolved is True
    # the whole point: approving a classification must not move the sequence
    assert enrollment.state == "replied" and enrollment.current_step == 2


def test_redrive_actions_still_change_state(client, db, make_key):
    h = _headers(make_key)
    campaign = Campaign(name="redrive-test", icp_description="x", value_prop="y")
    db.add(campaign)
    lead = _lead(db)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=0)
    db.add(step)
    enrollment = Enrollment(
        lead_id=lead.id, campaign_id=campaign.id, state="error", current_step=1
    )
    db.add(enrollment)
    db.flush()
    item = ReviewQueueItem(kind="copywriter", enrollment_id=enrollment.id, payload={})
    db.add(item)
    db.flush()

    resp = client.post(f"/inbox/review/{item.id}/action", json={"action": "skip"}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["new_state"] == "finished_no_reply"  # was the last step


def test_unknown_action_rejected(client, db, make_key):
    h = _headers(make_key)
    item = ReviewQueueItem(kind="copywriter", payload={})
    db.add(item)
    db.flush()
    resp = client.post(f"/inbox/review/{item.id}/action", json={"action": "nuke"}, headers=h)
    assert resp.status_code == 400


# ---------------------------------------------------------------- suppress


def test_manual_suppress_keeps_row_and_is_idempotent(client, db, make_key):
    h = _headers(make_key)
    lead = _lead(db)

    assert client.post(f"/leads/{lead.id}/suppress", headers=h).status_code == 204
    assert lead.status == "suppressed"
    entry = db.scalar(
        select(SuppressionEntry).where(SuppressionEntry.email == lead.email.lower())
    )
    assert entry is not None and entry.reason == "manual"
    # the row survives — this is not erasure
    assert db.get(Lead, lead.id) is not None

    # idempotent
    assert client.post(f"/leads/{lead.id}/suppress", headers=h).status_code == 204


def test_suppress_scopes_and_404(client, db, make_key):
    read_h = _headers(make_key, "read")
    assert client.post(f"/leads/{uuid.uuid4()}/suppress", headers=read_h).status_code == 403
    op_h = _headers(make_key)
    assert client.post(f"/leads/{uuid.uuid4()}/suppress", headers=op_h).status_code == 404


def test_erase_still_requires_admin(client, db, make_key):
    """Suppress is operate; erase stays admin. M1.3 must not widen erasure."""
    lead = _lead(db)
    assert (
        client.delete(f"/leads/{lead.id}/erase", headers=_headers(make_key)).status_code == 403
    )
    assert (
        client.delete(
            f"/leads/{lead.id}/erase", headers=_headers(make_key, "admin")
        ).status_code
        == 204
    )


# ---------------------------------------------------------------- score provenance


def test_activate_records_score_provenance(client, db, make_key):
    h = _headers(make_key)
    created = client.post(
        "/campaigns",
        json={
            "name": "provenance",
            "icp_description": "operations leaders at logistics firms",
            "value_prop": "cut manual ops work",
        },
        headers=h,
    ).json()
    client.post(
        f"/campaigns/{created['id']}/variants",
        json={"step_order": 1, "name": "v1", "skeleton": SKELETON},
        headers=h,
    )
    company = Company(domain=f"prov-{uuid.uuid4().hex[:6]}.test", name="logistics ops")
    db.add(company)
    db.flush()
    lead = _lead(db, company_id=company.id, title="head of operations")

    assert client.post(f"/campaigns/{created['id']}/activate", headers=h).status_code == 200

    db.refresh(lead)
    assert lead.icp_score is not None
    assert lead.icp_cosine is not None and lead.icp_rule is not None
    assert lead.icp_scored_campaign_id == uuid.UUID(created["id"])
    assert lead.icp_scored_at is not None
    # the parts must reconstruct the whole, or the popover would lie
    assert abs((0.7 * lead.icp_cosine + 0.3 * lead.icp_rule) - lead.icp_score) < 0.001
    assert lead.icp_rule == 0.8  # "head" keyword

    listed = client.get("/leads", headers=h).json()
    row = next(item for item in listed if item["id"] == str(lead.id))
    assert row["icp_scored_campaign_name"] == "provenance"
    assert row["icp_matched_keyword"] == "head"


def test_unscored_lead_reports_null_provenance(client, db, make_key):
    lead = _lead(db, title="operations lead")
    listed = client.get("/leads", headers=_headers(make_key, "read")).json()
    row = next(item for item in listed if item["id"] == str(lead.id))
    assert row["icp_cosine"] is None and row["icp_scored_campaign_name"] is None
    # derived-at-read-time field still works without a stored score
    assert row["icp_matched_keyword"] == "lead"


def test_lead_filters_still_work(client, db, make_key):
    h = _headers(make_key, "read")
    _lead(db, status="verified", icp_score=0.9)
    _lead(db, status="disqualified", icp_score=0.1)
    verified = client.get("/leads?status=verified", headers=h).json()
    assert all(item["status"] == "verified" for item in verified)
    high = client.get("/leads?score_gte=0.5", headers=h).json()
    assert all(item["icp_score"] >= 0.5 for item in high)
