"""GDPR erasure cascade tests (M0.4, finding C2).

The pre-M0.4 bug: erase_lead did a bare db.delete(lead), which violates the
enrollments.lead_id FK for any lead with history — erasure only worked for leads
with nothing to erase.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from craftsman.compliance.suppression import erase_lead, is_suppressed
from craftsman.core.models import (
    AuditLog,
    Campaign,
    Company,
    Enrollment,
    Lead,
    Message,
    ReviewQueueItem,
    SequenceStep,
    SuppressionEntry,
    UnsubscribeToken,
    Variant,
)


def _full_history_lead(db, email="dana.lopez@acme-erase.com"):
    """A lead with every kind of history the app can create."""
    company = Company(
        domain="acme-erase.com",
        name="Acme Robotics",
        research_brief={
            "what_they_do": "Acme Robotics builds warehouse robots. Dana Lopez leads ops.",
            "industry": "logistics",
            "trigger_events": [
                {
                    "claim": "Dana Lopez announced the new Austin facility",
                    "source_url": "https://acme-erase.com/news",
                    "approx_date": "2026-05",
                }
            ],
            "likely_pain_points": ["manual picking costs"],
            "evidence_quotes": [
                "Contact dana.lopez@acme-erase.com for a tour.",
                "We just opened our new Austin facility.",
            ],
        },
        research_fetched_at=datetime.now(timezone.utc),
    )
    db.add(company)
    db.flush()

    lead = Lead(
        email=email, company_id=company.id, first_name="Dana", last_name="Lopez",
        title="VP Operations", email_verified=True, status="verified",
    )
    db.add(lead)
    campaign = Campaign(name="erase-test", icp_description="ops", value_prop="save money")
    db.add(campaign)
    db.flush()
    step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=0)
    db.add(step)
    db.flush()
    variant = Variant(step_id=step.id, name="v1", skeleton="Subject: {{s}}\n{{b}}", slot_schema={})
    db.add(variant)
    enrollment = Enrollment(
        lead_id=lead.id, campaign_id=campaign.id, state="waiting", current_step=1,
        next_action_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(enrollment)
    db.flush()

    outbound = Message(
        enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
        subject="hi Dana", body="Hi Dana, saw the Austin facility news.",
        smtp_message_id="<out-1@erase.test>", bandit_outcome="pending",
        sent_at=datetime.now(timezone.utc),
    )
    inbound = Message(
        enrollment_id=enrollment.id, direction="inbound",
        subject="Re: hi Dana", body="This is Dana - my personal cell is 555-0100.",
        smtp_message_id="<in-1@erase.test>", classification="interested",
        classification_confidence=0.95,
    )
    db.add_all([outbound, inbound])
    db.flush()

    db.add_all([
        ReviewQueueItem(
            kind="copywriter", enrollment_id=enrollment.id,
            payload={"errors": ["x"], "slots": {"s": "Hi Dana Lopez"}},
        ),
        ReviewQueueItem(
            kind="classification", message_id=inbound.id, enrollment_id=enrollment.id,
            payload={"label": "ooo", "confidence": 0.5, "from": email},
        ),
        AuditLog(
            enrollment_id=enrollment.id, from_state="queued", to_state="researching",
            event="RESEARCH_START", detail={"note": "researching Dana Lopez"},
        ),
        AuditLog(
            enrollment_id=enrollment.id, from_state="ready", to_state="waiting",
            event="SEND_OK", detail={"step": 1},
        ),
        UnsubscribeToken(token=f"tok-{uuid.uuid4().hex[:12]}", lead_email=email),
    ])
    db.flush()
    return lead, company, enrollment


def test_erase_with_enrollment_no_integrity_error(db):
    lead, _, _ = _full_history_lead(db, email="regress@acme-erase.com")
    erase_lead(db, lead)  # pre-M0.4: raises IntegrityError on flush
    db.flush()
    assert db.scalar(select(Lead).where(Lead.email == "regress@acme-erase.com")) is None


def test_zero_rows_sweep_and_audit_anonymized(db):
    email = "dana.lopez@acme-erase.com"
    lead, company, enrollment = _full_history_lead(db, email=email)
    lead_id, company_id, enrollment_id = lead.id, company.id, enrollment.id

    erase_lead(db, lead)
    db.flush()
    db.expire_all()

    # gone: every row that identifies the person
    assert db.get(Lead, lead_id) is None
    assert db.scalars(select(Enrollment).where(Enrollment.lead_id == lead_id)).all() == []
    assert db.scalars(select(Message).where(Message.enrollment_id == enrollment_id)).all() == []
    assert (
        db.scalars(
            select(ReviewQueueItem).where(ReviewQueueItem.enrollment_id == enrollment_id)
        ).all()
        == []
    )
    assert (
        db.scalars(
            select(UnsubscribeToken).where(UnsubscribeToken.lead_email == email)
        ).all()
        == []
    )

    # kept: audit rows survive, anonymized (human decision 2026-07-21: keep the data)
    audits = db.scalars(select(AuditLog).where(AuditLog.event == "RESEARCH_START")).all()
    assert len(audits) == 1
    assert audits[0].enrollment_id is None
    detail_text = str(audits[0].detail)
    assert "Dana" not in detail_text and "Lopez" not in detail_text and email not in detail_text

    # kept: the company and its facts; scrubbed: the person
    fresh_company = db.get(Company, company_id)
    assert fresh_company is not None
    brief_text = str(fresh_company.research_brief)
    assert "Dana" not in brief_text
    assert "Lopez" not in brief_text
    assert email not in brief_text
    assert "Austin facility" in brief_text  # company facts stay
    assert "[redacted]" in brief_text

    # kept: the suppression entry — it IS the do-not-contact record
    assert is_suppressed(db, email)
    entry = db.scalar(select(SuppressionEntry).where(SuppressionEntry.email == email))
    assert entry is not None and entry.reason == "gdpr"


def test_multi_lead_isolation(db):
    """Erasing one lead leaves a colleague at the same company untouched."""
    lead_a, company, _ = _full_history_lead(db, email="erase.me@acme-erase.com")
    lead_b = Lead(
        email="keep.me@acme-erase.com", company_id=company.id,
        first_name="Raj", last_name="Patel", status="verified", email_verified=True,
    )
    db.add(lead_b)
    db.flush()
    lead_b_id = lead_b.id

    erase_lead(db, lead_a)
    db.flush()
    db.expire_all()

    survivor = db.get(Lead, lead_b_id)
    assert survivor is not None and survivor.first_name == "Raj"
    assert db.get(Company, company.id) is not None
    assert not is_suppressed(db, "keep.me@acme-erase.com")


def test_queued_tasks_noop_after_erase(db, monkeypatch):
    """A Celery payload referencing an erased lead must be a safe no-op —
    queued sends cannot resurrect an erased person. Task functions are exercised
    directly (payloads are IDs; the broker holds no PII)."""
    from craftsman.workers import tasks as task_mod

    lead, _, enrollment = _full_history_lead(db, email="queued@acme-erase.com")
    lead_id, enrollment_id = str(lead.id), str(enrollment.id)

    erase_lead(db, lead)
    db.flush()

    # route the task functions' session at this test's transaction
    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(task_mod, "session_scope", fake_scope)

    # each returns without raising and without creating rows
    task_mod.enrich_lead(lead_id)
    task_mod.research_enrollment.run(enrollment_id)
    task_mod.generate_and_send.run(enrollment_id)
    db.flush()
    assert db.scalars(select(Message).where(Message.enrollment_id == enrollment_id)).all() == []
