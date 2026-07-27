"""CRM sync orchestration (M5.2): inbound list import, outbound activity push.

Inbound is request-triggered (a human maps list → campaign; nothing pulls
contacts ambiently) and flows through the SAME ingest gate as CSV — dedupe,
suppression, verification — plus the CRM-specific parts: CRM-owned field
updates on existing leads (CRM wins contact fields), CRMLink identity rows,
and optional score-and-enroll into an active campaign for the already-verified
subset (fresh imports enroll on the campaign's next activate, after verify).

Outbound is the beat task's push: everything Craftsman-side newer than the
connection's watermark — outbound sends, classified replies, meetings — lands
on the linked contact's CRM timeline. Push is at-most-once per activity: the
watermark advances even when individual activities fail (failures are counted
on the sync run, never retried forever against a poisoned record).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.crypto import decrypt, encrypt
from craftsman.core.models import (
    Campaign,
    CRMConnection,
    CRMLink,
    CRMSyncRun,
    Enrollment,
    Lead,
    Meeting,
    Message,
)
from craftsman.crm.mapping import (
    PreviewRow,
    diff_existing,
    effective_map,
    to_lead_row,
)
from craftsman.crm.provider import CRMActivity, CRMContact, CRMProvider
from craftsman.ingest.gate import LeadRow, ingest_leads

log = logging.getLogger(__name__)

PROVIDERS = ("hubspot", "salesforce")


def encrypt_credentials(credentials: dict) -> str:
    import json

    return encrypt(json.dumps(credentials))


def build_crm_client(connection: CRMConnection) -> CRMProvider:
    """Decrypt the connection's credential blob and construct its adapter.
    The Salesforce constructor re-runs the SSRF guard on the instance URL."""
    import json

    credentials = json.loads(decrypt(connection.credentials_enc))
    if connection.provider == "hubspot":
        from craftsman.crm.hubspot_impl import HubSpotClient

        return HubSpotClient(credentials)
    if connection.provider == "salesforce":
        from craftsman.crm.salesforce_impl import SalesforceClient

        return SalesforceClient(credentials)
    raise ValueError(f"unknown CRM provider: {connection.provider!r}")


# ---------------------------------------------------------------- inbound


@dataclass
class ImportOutcome:
    stats: dict = field(default_factory=dict)
    preview: list[PreviewRow] = field(default_factory=list)


def _mapped_rows(
    connection: CRMConnection, contacts: list[CRMContact]
) -> list[tuple[CRMContact, LeadRow | None]]:
    fmap = effective_map(connection.provider, connection.field_map)
    return [(c, to_lead_row(c.fields, fmap)) for c in contacts]


def preview_import(
    db: Session, connection: CRMConnection, contacts: list[CRMContact]
) -> ImportOutcome:
    """Dry-run: what WOULD happen, row by row, with the field-level diff for
    existing leads. No writes of any kind."""
    from craftsman.compliance.suppression import is_suppressed

    pairs = _mapped_rows(connection, contacts)
    preview: list[PreviewRow] = []
    tally = {"create": 0, "update": 0, "unchanged": 0, "suppressed": 0, "no_email": 0}
    seen: set[str] = set()
    for contact, row in pairs:
        if row is None:
            tally["no_email"] += 1
            preview.append(PreviewRow(email="", action="no_email"))
            continue
        email = row.normalized_email()
        if email in seen:
            continue  # same address twice in one list: first occurrence wins
        seen.add(email)
        if is_suppressed(db, email):
            tally["suppressed"] += 1
            preview.append(PreviewRow(email=email, action="suppressed"))
            continue
        lead = db.scalar(select(Lead).where(Lead.email == email))
        if lead is None:
            tally["create"] += 1
            preview.append(PreviewRow(email=email, action="create"))
            continue
        changes = diff_existing(row, lead)
        action = "update" if changes else "unchanged"
        tally[action] += 1
        preview.append(PreviewRow(email=email, action=action, changes=changes))
    return ImportOutcome(stats=tally, preview=preview)


async def commit_import(
    db: Session,
    connection: CRMConnection,
    contacts: list[CRMContact],
    campaign: Campaign | None,
) -> ImportOutcome:
    """The real import. New contacts go through the ingest gate (same as CSV);
    existing leads get CRM-owned fields overwritten (CRM wins); every matched
    lead gets a CRMLink. If a campaign is given, the verified subset is scored
    and enrolled through the one enrollment path (scoring/enroll.py)."""
    pairs = [(c, r) for c, r in _mapped_rows(connection, contacts) if r is not None]
    no_email = len(contacts) - len(pairs)

    result, new_ids = ingest_leads(
        db, [r for _, r in pairs], source=f"crm:{connection.provider}"
    )
    db.flush()

    updated = 0
    linked = 0
    touched_leads: list[Lead] = []
    seen: set[str] = set()
    for contact, row in pairs:
        email = row.normalized_email()
        if email in seen:
            continue
        seen.add(email)
        lead = db.scalar(select(Lead).where(Lead.email == email))
        if lead is None:  # suppressed or gate-rejected — no link, no update
            continue
        touched_leads.append(lead)
        if lead.id not in new_ids:
            changes = diff_existing(row, lead)
            for f, change in changes.items():
                setattr(lead, f, change["to"])
            if changes:
                updated += 1
                db.add(lead)
        link = db.scalar(
            select(CRMLink).where(
                CRMLink.connection_id == connection.id, CRMLink.lead_id == lead.id
            )
        )
        if link is None:
            db.add(
                CRMLink(
                    connection_id=connection.id,
                    lead_id=lead.id,
                    remote_id=contact.remote_id,
                    remote_type=contact.remote_type,
                )
            )
            linked += 1
    db.flush()

    enrolled = 0
    if campaign is not None:
        from craftsman.scoring.enroll import score_and_enroll

        verified = [
            lead for lead in touched_leads
            if lead.email_verified and lead.status == "verified"
        ]
        if verified:
            enrolled = await score_and_enroll(db, campaign, verified)

    stats = {
        "imported": result.imported,
        "deduped": result.deduped,
        "suppressed": result.suppressed,
        "updated": updated,
        "linked": linked,
        "no_email": no_email,
        "enrolled": enrolled,
        "campaign_id": str(campaign.id) if campaign else None,
    }
    _enqueue_enrich(new_ids)
    return ImportOutcome(stats=stats)


def _enqueue_enrich(new_ids: list) -> None:
    """Verify+enrich exactly this batch (best-effort if no broker) — the same
    post-import hook the CSV path uses."""
    try:
        from craftsman.workers.tasks import enrich_lead

        for lead_id in new_ids:
            enrich_lead.delay(str(lead_id))
    except Exception:  # noqa: BLE001 — no broker is a legal dev state
        pass


# ---------------------------------------------------------------- outbound


def collect_activity(
    db: Session, connection: CRMConnection, until: datetime
) -> list[CRMActivity]:
    """Everything pushable in (watermark, until]: outbound sends, classified
    inbound replies, meetings — for leads linked on this connection."""
    since = connection.outbound_watermark or datetime(1970, 1, 1, tzinfo=timezone.utc)
    links = db.scalars(
        select(CRMLink).where(CRMLink.connection_id == connection.id)
    ).all()
    by_lead: dict[uuid.UUID, CRMLink] = {link.lead_id: link for link in links}
    if not by_lead:
        return []

    enrollment_lead = {
        e.id: e.lead_id
        for e in db.scalars(
            select(Enrollment).where(Enrollment.lead_id.in_(list(by_lead)))
        ).all()
    }
    out: list[CRMActivity] = []

    def _linked(enrollment_id) -> CRMLink | None:
        lead_id = enrollment_lead.get(enrollment_id)
        return by_lead.get(lead_id) if lead_id else None

    sends = db.scalars(
        select(Message).where(
            Message.direction == "outbound",
            Message.sent_at.is_not(None),
            Message.sent_at > since,
            Message.sent_at <= until,
        )
    ).all()
    for m in sends:
        link = _linked(m.enrollment_id)
        if link is None:
            continue
        out.append(
            CRMActivity(
                remote_id=link.remote_id,
                remote_type=link.remote_type or "contact",
                kind="send",
                summary=f"Outbound email sent: {m.subject or '(no subject)'}",
                detail=(m.body or "")[:2000],
                occurred_at=m.sent_at,
            )
        )

    replies = db.scalars(
        select(Message).where(
            Message.direction == "inbound",
            Message.created_at > since,
            Message.created_at <= until,
        )
    ).all()
    for m in replies:
        link = _linked(m.enrollment_id)
        if link is None:
            continue
        label = m.classification or "unclassified"
        confidence = (
            f" ({m.classification_confidence:.0%})"
            if m.classification_confidence is not None
            else ""
        )
        out.append(
            CRMActivity(
                remote_id=link.remote_id,
                remote_type=link.remote_type or "contact",
                kind="reply",
                summary=f"Reply received — classified {label}{confidence}",
                detail=(m.body or "")[:2000],
                occurred_at=m.created_at,
            )
        )

    meetings = db.scalars(
        select(Meeting).where(Meeting.created_at > since, Meeting.created_at <= until)
    ).all()
    for meeting in meetings:
        link = _linked(meeting.enrollment_id)
        if link is None:
            continue
        start = f" at {meeting.start_at.isoformat()}" if meeting.start_at else ""
        out.append(
            CRMActivity(
                remote_id=link.remote_id,
                remote_type=link.remote_type or "contact",
                kind="meeting",
                summary=f"Meeting {meeting.status}{start}",
                detail=f"Booked via {meeting.provider} (Craftsman)",
                occurred_at=meeting.created_at,
            )
        )

    out.sort(key=lambda a: a.occurred_at)
    return out


async def push_activity(db: Session, connection: CRMConnection) -> dict:
    """One outbound run: collect past the watermark, push, advance the
    watermark to the run boundary regardless of per-activity failures
    (at-most-once; failures are tallied, never retried forever)."""
    until = datetime.now(timezone.utc)
    activities = collect_activity(db, connection, until)
    client = build_crm_client(connection)
    pushed = 0
    failed = 0
    for activity in activities:
        try:
            if activity.kind == "meeting":
                await client.log_meeting(activity)
            else:
                await client.log_activity(activity)
            pushed += 1
        except Exception as e:  # noqa: BLE001 — one bad record must not stall the rest
            failed += 1
            log.warning(
                "CRM activity push failed (connection=%s, kind=%s, remote=%s): %s",
                connection.id, activity.kind, activity.remote_id, e,
            )
    connection.outbound_watermark = until
    db.add(connection)
    return {"activities": len(activities), "pushed": pushed, "failed": failed}


def record_run(
    db: Session,
    connection: CRMConnection,
    direction: str,
    stats: dict,
    error: str | None = None,
) -> CRMSyncRun:
    run = CRMSyncRun(
        connection_id=connection.id,
        direction=direction,
        status="failed" if error else "succeeded",
        stats=stats,
        error=error,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run
