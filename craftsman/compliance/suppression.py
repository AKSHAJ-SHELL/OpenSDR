"""Suppression list: the one check nothing may skip.

Checked at generation time AND again at send time.
Also home to GDPR erasure — the multi-store cascade that removes a person.
"""

import re
import secrets

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from craftsman.core.config import get_settings
from craftsman.core.models import (
    AuditLog,
    Company,
    DryRunItem,
    Enrollment,
    GlobalSuppressionEntry,
    Lead,
    LeadEnrichmentRecord,
    Meeting,
    Message,
    ReplyDraft,
    ReviewQueueItem,
    SuppressionEntry,
    TouchTask,
    UnsubscribeToken,
)

EU_TLDS = {
    ".at", ".be", ".bg", ".hr", ".cy", ".cz", ".dk", ".ee", ".fi", ".fr", ".de",
    ".gr", ".hu", ".ie", ".it", ".lv", ".lt", ".lu", ".mt", ".nl", ".pl", ".pt",
    ".ro", ".sk", ".si", ".es", ".se", ".eu",
}


def is_suppressed(db: Session, email: str) -> bool:
    """Org do-not-contact, UNION the optional cross-org overlay (⛔ Gate M5 Q1a).

    The org check is tenancy-scoped automatically; the overlay is additive and
    consulted only as a boolean, so no tenant learns anything about another."""
    email = email.lower()
    if db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == email)):
        return True
    settings = get_settings()
    if settings.global_suppression_enabled:
        return db.get(GlobalSuppressionEntry, email) is not None
    return False


def suppress(db: Session, email: str, reason: str) -> None:
    email = email.lower()
    if not db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == email)):
        db.add(SuppressionEntry(email=email, reason=reason))
    settings = get_settings()
    if (
        settings.global_suppression_enabled
        and settings.unsubscribe_propagate_global
        and reason in ("unsubscribe", "gdpr")
        and db.get(GlobalSuppressionEntry, email) is None
    ):
        # opt-in overlay propagation: this person is done hearing from ANY org
        # on this install; only consent-shaped reasons propagate (never bounce
        # or one org's manual list management)
        db.add(GlobalSuppressionEntry(email=email, reason=reason))
    lead = db.scalar(select(Lead).where(Lead.email == email))
    if lead is not None:
        lead.status = "suppressed"
        db.add(lead)


def gdpr_blocked(email: str, gdpr_mode: bool, list_is_opt_in: bool = False) -> bool:
    """In GDPR mode, EU-TLD leads can't enroll unless the list is marked opt-in."""
    if not gdpr_mode or list_is_opt_in:
        return False
    domain = "." + email.rsplit("@", 1)[-1].lower()
    return any(domain.endswith(tld) for tld in EU_TLDS)


def make_unsubscribe_token(db: Session, email: str) -> str:
    token = secrets.token_urlsafe(24)
    db.add(UnsubscribeToken(token=token, lead_email=email.lower()))
    return token


def _identifiers(lead: Lead) -> list[str]:
    """The strings that identify this person in free text. Names shorter than
    3 chars are skipped (initials would redact half the alphabet); false
    positives only redact a word in a cached brief, false negatives retain PII."""
    ids = [lead.email]
    first = (lead.first_name or "").strip()
    last = (lead.last_name or "").strip()
    if first and last:
        ids.append(f"{first} {last}")
    for name in (first, last):
        if len(name) >= 3:
            ids.append(name)
    return ids


def scrub_pii(obj, identifiers: list[str]):
    """Recursively replace identifier occurrences with '[redacted]' in any
    nested dict/list/str structure. Returns (scrubbed_copy, changed)."""
    if isinstance(obj, str):
        out = obj
        for ident in identifiers:
            out = re.sub(re.escape(ident), "[redacted]", out, flags=re.IGNORECASE)
        return out, out != obj
    if isinstance(obj, dict):
        changed = False
        new = {}
        for k, v in obj.items():
            new[k], c = scrub_pii(v, identifiers)
            changed = changed or c
        return new, changed
    if isinstance(obj, (list, tuple)):
        changed = False
        new = []
        for v in obj:
            sv, c = scrub_pii(v, identifiers)
            new.append(sv)
            changed = changed or c
        return new, changed
    return obj, False


def erase_lead(db: Session, lead: Lead) -> None:
    """GDPR data-subject erasure: a multi-store cascade, then permanent suppression.

    Deletes everything that identifies the person: review-queue items, messages
    (inbound reply text is prospect-authored PII), enrollments, unsubscribe tokens,
    enrichment provenance rows, and the lead row. Audit-log rows are KEPT but anonymized (enrollment link
    severed, identifiers scrubbed from detail) — anonymized rows no longer relate
    to an identifiable person, and the aggregate history stays useful. The cached
    company research brief is scrubbed of person mentions; company facts stay.
    The suppression entry survives deliberately: it IS the do-not-contact record.

    Celery payloads carry only IDs, so queued tasks referencing the erased lead
    no-op once these rows are gone (asserted by tests/e2e/test_erasure.py).
    """
    email = lead.email
    identifiers = _identifiers(lead)

    enrollment_ids = list(
        db.scalars(select(Enrollment.id).where(Enrollment.lead_id == lead.id)).all()
    )
    if enrollment_ids:
        message_ids = list(
            db.scalars(
                select(Message.id).where(Message.enrollment_id.in_(enrollment_ids))
            ).all()
        )

        review_conds = [ReviewQueueItem.enrollment_id.in_(enrollment_ids)]
        if message_ids:
            review_conds.append(ReviewQueueItem.message_id.in_(message_ids))
        for item in db.scalars(select(ReviewQueueItem).where(or_(*review_conds))).all():
            db.delete(item)

        # keep the audit rows, sever the person: link nulled, detail scrubbed
        for audit in db.scalars(
            select(AuditLog).where(AuditLog.enrollment_id.in_(enrollment_ids))
        ).all():
            audit.enrollment_id = None
            if audit.detail is not None:
                audit.detail, _ = scrub_pii(audit.detail, identifiers)
            db.add(audit)

        # reply drafts quote prospect-authored text (person PII) and FK-reference
        # messages — deleted first so the message deletes below don't violate FKs
        for draft in db.scalars(
            select(ReplyDraft).where(ReplyDraft.enrollment_id.in_(enrollment_ids))
        ).all():
            db.delete(draft)
        # meetings link the person's booked events (M4.3) — person-linked, erased
        for meeting in db.scalars(
            select(Meeting).where(Meeting.enrollment_id.in_(enrollment_ids))
        ).all():
            db.delete(meeting)
        for msg in db.scalars(
            select(Message).where(Message.enrollment_id.in_(enrollment_ids))
        ).all():
            db.delete(msg)
        # touch tasks hold personalized outreach content (LinkedIn note / call brief) — PII
        for task in db.scalars(
            select(TouchTask).where(TouchTask.enrollment_id.in_(enrollment_ids))
        ).all():
            db.delete(task)
        for enr in db.scalars(
            select(Enrollment).where(Enrollment.id.in_(enrollment_ids))
        ).all():
            db.delete(enr)

    for token in db.scalars(
        select(UnsubscribeToken).where(UnsubscribeToken.lead_email == email.lower())
    ).all():
        db.delete(token)

    # dry-run items hold the person's email and personalized copy
    for item in db.scalars(select(DryRunItem).where(DryRunItem.lead_id == lead.id)).all():
        db.delete(item)

    # enrichment provenance rows carry provider-sourced PII (title, phone, LinkedIn)
    for rec in db.scalars(
        select(LeadEnrichmentRecord).where(LeadEnrichmentRecord.lead_id == lead.id)
    ).all():
        db.delete(rec)

    # scrub the cached research brief; deliberately no re-fetch (a re-scrape of a
    # team/news page could pull the person's name right back into the cache)
    if lead.company_id is not None:
        company = db.get(Company, lead.company_id)
        if company is not None and company.research_brief is not None:
            scrubbed, changed = scrub_pii(company.research_brief, identifiers)
            if changed:
                company.research_brief = scrubbed
                db.add(company)

    db.delete(lead)
    db.flush()  # cascade order is explicit; surface FK problems here, not at commit
    suppress(db, email, reason="gdpr")
