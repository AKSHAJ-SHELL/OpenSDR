"""Suppression list: the one check nothing may skip.

Checked at generation time AND again at send time.
"""

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Lead, SuppressionEntry, UnsubscribeToken

EU_TLDS = {
    ".at", ".be", ".bg", ".hr", ".cy", ".cz", ".dk", ".ee", ".fi", ".fr", ".de",
    ".gr", ".hu", ".ie", ".it", ".lv", ".lt", ".lu", ".mt", ".nl", ".pl", ".pt",
    ".ro", ".sk", ".si", ".es", ".se", ".eu",
}


def is_suppressed(db: Session, email: str) -> bool:
    return db.get(SuppressionEntry, email.lower()) is not None


def suppress(db: Session, email: str, reason: str) -> None:
    email = email.lower()
    if db.get(SuppressionEntry, email) is None:
        db.add(SuppressionEntry(email=email, reason=reason))
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


def erase_lead(db: Session, lead: Lead) -> None:
    """GDPR data-subject erasure: hard-delete the lead and suppress the address
    so it can never be re-imported."""
    email = lead.email
    db.delete(lead)
    suppress(db, email, reason="gdpr")
