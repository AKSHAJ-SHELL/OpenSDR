"""Inbox poller: per-mailbox IMAP fetch → thread-match → hand off to the pipeline.

Also supports Mailpit's HTTP API for local sandboxes (Mailpit is not a reliable
IMAP peer — plain IMAP4 to :1143 typically EOF's on connect).
"""

import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header, make_header

import httpx
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


def _imap_client(host: str, port: int):
    """Pick plain IMAP4 vs SSL. Port 993 → SSL; everything else plain (+ optional STARTTLS)."""
    if port == 993:
        return imaplib.IMAP4_SSL(host, port)
    return imaplib.IMAP4(host, port)


def fetch_unseen(mailbox: Mailbox, imap_cls=None) -> list[InboundEmail]:
    """Fetch unseen messages over IMAP. Failures are logged, never raised.

    imap_cls injectable for tests. When omitted, SSL is used for port 993.
    """
    if not mailbox.imap_host:
        return []
    out: list[InboundEmail] = []
    conn = None
    port = mailbox.imap_port or 143
    try:
        if imap_cls is not None:
            conn = imap_cls(mailbox.imap_host, port)
        else:
            conn = _imap_client(mailbox.imap_host, port)
            # Mailpit and some sandboxes speak plain IMAP on non-993 with STARTTLS optional.
            if port not in (143, 993) and hasattr(conn, "starttls"):
                try:
                    conn.starttls()
                except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError):
                    pass  # plain-only server; continue without TLS
        password = decrypt(mailbox.imap_pass_enc) if mailbox.imap_pass_enc else ""
        conn.login(mailbox.email, password)
        conn.select("INBOX")
        _, data = conn.search(None, "UNSEEN")
        for num in data[0].split():
            _, msg_data = conn.fetch(num, "(RFC822)")
            if msg_data and msg_data[0]:
                out.append(parse_raw_email(msg_data[0][1]))
    except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError) as e:
        log.warning("IMAP fetch failed for %s: %s", mailbox.email, e)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
    return out


def _already_ingested(db: Session, message_id: str | None) -> bool:
    if not message_id:
        return False
    return (
        db.scalar(
            select(Message.id).where(
                Message.smtp_message_id == message_id,
                Message.direction == "inbound",
            ).limit(1)
        )
        is not None
    )


def fetch_mailpit(db: Session, base_url: str, *, limit: int = 50) -> list[InboundEmail]:
    """Pull recent messages from Mailpit's HTTP API and skip ones we already ingested."""
    base = base_url.rstrip("/")
    out: list[InboundEmail] = []
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base}/api/v1/messages", params={"limit": limit})
            resp.raise_for_status()
            messages = resp.json().get("messages") or []
            for summary in messages:
                mid = summary.get("ID")
                if not mid:
                    continue
                # Prefer RFC Message-ID for dedup when present on the list payload
                rfc_id = (summary.get("MessageID") or "").strip() or None
                if rfc_id and _already_ingested(db, rfc_id):
                    continue
                raw_resp = client.get(f"{base}/api/v1/message/{mid}/raw")
                if raw_resp.status_code != 200:
                    continue
                inbound = parse_raw_email(raw_resp.content)
                if inbound.message_id and _already_ingested(db, inbound.message_id):
                    continue
                # Skip our own outbound copies sitting in Mailpit (no reply headers)
                if not inbound.in_reply_to and not inbound.references:
                    continue
                out.append(inbound)
    except (httpx.HTTPError, OSError, ValueError) as e:
        log.warning("Mailpit fetch failed (%s): %s", base, e)
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
