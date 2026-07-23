"""Send Engine: aiosmtplib against the mailbox pool, with every pre-send check.

Order of checks: suppression → campaign daily cap → mailbox cap (warmup-adjusted)
→ rate limiter. Plain-text bodies, List-Unsubscribe headers, correct threading.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import aiosmtplib
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from craftsman.compliance.suppression import is_suppressed, make_unsubscribe_token
from craftsman.core.config import get_settings
from craftsman.core.crypto import decrypt
from craftsman.core.models import Campaign, Enrollment, Lead, Mailbox, Message
from craftsman.sender.limiter import acquire_send_slot
from craftsman.sender.warmup import effective_daily_limit


class SendBlocked(Exception):
    """Raised when a pre-send check fails. .reason says which."""

    def __init__(self, reason: str, retry_in: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_in = retry_in


@dataclass
class PreparedEmail:
    subject: str
    body: str
    to_email: str


def pick_mailbox(db: Session) -> Mailbox | None:
    """Healthy mailbox with remaining capacity, least-used-today first."""
    boxes = db.scalars(
        select(Mailbox).where(Mailbox.health != "paused").order_by(Mailbox.sent_today)
    ).all()
    for box in boxes:
        limit = effective_daily_limit(box.daily_limit, box.warmup_stage)
        if box.health == "degraded":
            limit = max(1, limit // 2)
        if box.sent_today < limit:
            return box
    return None


def campaign_sent_today(db: Session, campaign_id) -> int:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count(Message.id))
            .join(Enrollment, Message.enrollment_id == Enrollment.id)
            .where(
                Enrollment.campaign_id == campaign_id,
                Message.direction == "outbound",
                Message.sent_at >= today_start,
            )
        )
        or 0
    )


def run_presend_checks(db: Session, lead: Lead, campaign: Campaign) -> Mailbox:
    """Suppression → mailbox capacity → per-mailbox rate limit. The per-campaign cap
    is a separate atomic reserve (reserve_campaign_slot), evaluated last by the caller
    so a slot is only taken when a send is actually imminent."""
    if is_suppressed(db, lead.email):
        raise SendBlocked("suppressed")
    mailbox = pick_mailbox(db)
    if mailbox is None:
        raise SendBlocked("no_mailbox_capacity")
    wait = acquire_send_slot(str(mailbox.id))
    if wait > 0:
        raise SendBlocked("rate_limited", retry_in=wait)
    return mailbox


def reserve_campaign_slot(db: Session, campaign: Campaign) -> bool:
    """Atomically claim one send against the campaign's daily cap.

    A single conditional UPDATE — the row lock serializes concurrent workers, so no
    two can both cross the cap (fixes the read-then-act race). Returns True if a slot
    was reserved, False if the cap is already reached. Commit promptly to release the
    row lock; never hold it across the SMTP send.
    """
    result = db.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id, Campaign.sent_today < Campaign.daily_cap)
        .values(sent_today=Campaign.sent_today + 1)
    )
    return result.rowcount == 1


def release_campaign_slot(db: Session, campaign_id) -> None:
    """Return a reserved slot after a send is abandoned (duplicate claim or a failed
    delivery), so a transient failure doesn't permanently burn a slot."""
    db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id, Campaign.sent_today > 0)
        .values(sent_today=Campaign.sent_today - 1)
    )


def build_email(
    *,
    db: Session,
    mailbox: Mailbox,
    lead: Lead,
    prepared: PreparedEmail,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> tuple[EmailMessage, str]:
    """Plain-text email with compliance + threading headers. Returns (msg, message_id)."""
    settings = get_settings()
    token = make_unsubscribe_token(db, lead.email)
    unsub_url = f"{settings.unsubscribe_base_url}/u/{token}"

    msg = EmailMessage()
    message_id = make_msgid(domain=mailbox.email.rsplit("@", 1)[-1])
    msg["Message-ID"] = message_id
    msg["Date"] = formatdate(localtime=False)
    msg["From"] = mailbox.email
    msg["To"] = prepared.to_email
    msg["Subject"] = prepared.subject
    msg["List-Unsubscribe"] = f"<mailto:{mailbox.email}?subject=unsubscribe>, <{unsub_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = " ".join((references or []) + [in_reply_to])

    footer = f"\n\n--\n{settings.physical_address}\nUnsubscribe: {unsub_url}"
    msg.set_content(prepared.body + footer)
    return msg, message_id


async def deliver(mailbox: Mailbox, msg: EmailMessage) -> None:
    kwargs: dict = {
        "hostname": mailbox.smtp_host,
        "port": mailbox.smtp_port or 587,
    }
    if mailbox.smtp_user and mailbox.smtp_pass_enc:
        kwargs["username"] = mailbox.smtp_user
        kwargs["password"] = decrypt(mailbox.smtp_pass_enc)
        kwargs["start_tls"] = (mailbox.smtp_port or 587) == 587
    await aiosmtplib.send(msg, **kwargs)


def record_bounce(db: Session, mailbox: Mailbox) -> None:
    """Two hard bounces in a day → health='degraded' (cap halves via pick_mailbox)."""
    mailbox.hard_bounces_today += 1
    if mailbox.hard_bounces_today >= 2:
        mailbox.health = "degraded"
    db.add(mailbox)


def last_outbound_in_thread(db: Session, enrollment_id: uuid.UUID) -> Message | None:
    return db.scalar(
        select(Message)
        .where(
            Message.enrollment_id == enrollment_id,
            Message.direction == "outbound",
        )
        .order_by(Message.sent_at.desc())
        .limit(1)
    )
