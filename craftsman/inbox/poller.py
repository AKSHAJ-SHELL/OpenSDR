"""Inbox poller: per-mailbox IMAP fetch → thread-match → hand off to the pipeline."""

import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header, make_header

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.crypto import decrypt
from craftsman.core.models import Mailbox, Message

log = logging.getLogger(__name__)


@dataclass
class InboundEmail:
    from_addr: str
    subject: str
    body: str
    in_reply_to: str | None
    references: list[str]
    message_id: str | None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return str(msg.get_payload())


def parse_raw_email(raw: bytes) -> InboundEmail:
    msg = email.message_from_bytes(raw)
    refs = (msg.get("References") or "").split()
    return InboundEmail(
        from_addr=email.utils.parseaddr(msg.get("From", ""))[1].lower(),
        subject=_decode(msg.get("Subject")),
        body=_extract_body(msg),
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=refs,
        message_id=(msg.get("Message-ID") or "").strip() or None,
    )


def fetch_unseen(mailbox: Mailbox, imap_cls=imaplib.IMAP4) -> list[InboundEmail]:
    """Fetch unseen messages over IMAP. imap_cls injectable for tests/Mailpit."""
    if not mailbox.imap_host:
        return []
    out: list[InboundEmail] = []
    conn = imap_cls(mailbox.imap_host, mailbox.imap_port or 143)
    try:
        password = decrypt(mailbox.imap_pass_enc) if mailbox.imap_pass_enc else ""
        conn.login(mailbox.email, password)
        conn.select("INBOX")
        _, data = conn.search(None, "UNSEEN")
        for num in data[0].split():
            _, msg_data = conn.fetch(num, "(RFC822)")
            if msg_data and msg_data[0]:
                out.append(parse_raw_email(msg_data[0][1]))
    except (imaplib.IMAP4.error, OSError) as e:
        log.warning("IMAP fetch failed for %s: %s", mailbox.email, e)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def match_thread(db: Session, inbound: InboundEmail) -> Message | None:
    """Match an inbound email to the outbound message it replies to,
    via In-Reply-To / References against messages.smtp_message_id."""
    candidates = [m for m in ([inbound.in_reply_to] + inbound.references) if m]
    if not candidates:
        return None
    return db.scalar(
        select(Message)
        .where(Message.smtp_message_id.in_(candidates), Message.direction == "outbound")
        .order_by(Message.sent_at.desc())
        .limit(1)
    )
