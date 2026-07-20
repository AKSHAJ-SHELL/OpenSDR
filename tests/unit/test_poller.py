"""Poller: IMAP connect failures stay soft; Mailpit HTTP path ingests replies."""

from email.message import EmailMessage
from unittest.mock import MagicMock

import httpx

from craftsman.inbox.poller import fetch_mailpit, fetch_unseen, parse_raw_email


def test_parse_raw_email_headers():
    msg = EmailMessage()
    msg["From"] = "Lead <lead@stripe.com>"
    msg["Subject"] = "Re: hello"
    msg["In-Reply-To"] = "<out-1@flowbot.io>"
    msg["References"] = "<out-1@flowbot.io>"
    msg["Message-ID"] = "<in-1@stripe.com>"
    msg.set_content("Sounds good, send details.")
    inbound = parse_raw_email(msg.as_bytes())
    assert inbound.from_addr == "lead@stripe.com"
    assert inbound.in_reply_to == "<out-1@flowbot.io>"
    assert inbound.message_id == "<in-1@stripe.com>"


def test_fetch_unseen_connect_eof_does_not_raise():
    import imaplib

    class Boom(imaplib.IMAP4):
        def __init__(self, host, port):
            raise imaplib.IMAP4.abort("socket error: EOF")

    mailbox = MagicMock()
    mailbox.imap_host = "127.0.0.1"
    mailbox.imap_port = 1143
    mailbox.email = "sam@example.com"
    mailbox.imap_pass_enc = None

    assert fetch_unseen(mailbox, imap_cls=Boom) == []


def test_fetch_unseen_skips_empty_imap_host():
    mailbox = MagicMock()
    mailbox.imap_host = None
    assert fetch_unseen(mailbox) == []
    mailbox.imap_host = ""
    assert fetch_unseen(mailbox) == []


def test_fetch_mailpit_parses_replies(monkeypatch):
    raw = EmailMessage()
    raw["From"] = "dana@stripe.com"
    raw["Subject"] = "Re: warehouse"
    raw["In-Reply-To"] = "<sent-1@example.com>"
    raw["Message-ID"] = "<reply-1@stripe.com>"
    raw.set_content("Interested — let's talk.")
    raw_bytes = raw.as_bytes()

    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "err", request=MagicMock(), response=MagicMock()
                )

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            if url.endswith("/api/v1/messages"):
                return FakeResponse(
                    200,
                    {
                        "messages": [
                            {
                                "ID": "abc",
                                "MessageID": "<reply-1@stripe.com>",
                                "Subject": "Re: warehouse",
                            }
                        ]
                    },
                )
            if url.endswith("/raw"):
                return FakeResponse(200, content=raw_bytes)
            return FakeResponse(404)

    monkeypatch.setattr(httpx, "Client", FakeClient)

    db = MagicMock()
    db.scalar.return_value = None
    results = fetch_mailpit(db, "http://mailpit:8025")
    assert len(results) == 1
    assert results[0].from_addr == "dana@stripe.com"
    assert results[0].in_reply_to == "<sent-1@example.com>"


def test_fetch_mailpit_skips_outbound_without_reply_headers(monkeypatch):
    raw = EmailMessage()
    raw["From"] = "sam@example.com"
    raw["Subject"] = "warehouse"
    raw["Message-ID"] = "<out-1@example.com>"
    raw.set_content("Hi Dana…")
    raw_bytes = raw.as_bytes()

    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            if url.endswith("/api/v1/messages"):
                return FakeResponse(
                    200,
                    {"messages": [{"ID": "out1", "MessageID": "<out-1@example.com>"}]},
                )
            return FakeResponse(200, content=raw_bytes)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    db = MagicMock()
    db.scalar.return_value = None
    assert fetch_mailpit(db, "http://localhost:8025") == []
