"""Outbound webhook delivery mechanics (M5.4): signing, backoff, the HTTP seam.

**Signature — the Cal.com scheme, outbound.** Craftsman verifies inbound
Cal.com webhooks with HMAC-SHA256 over the raw body (`meetings/providers.py`).
Outbound deliveries sign with the identical scheme, so a receiver verifies with
the same five lines of hmac code we run ourselves — symmetry is deliberate:

    expected = hmac.new(secret, raw_body, sha256).hexdigest()
    hmac.compare_digest(expected, header.removeprefix("sha256="))

Headers on every delivery POST:

- ``X-Craftsman-Event``          — the event type
- ``X-Craftsman-Delivery``       — the delivery row id (receiver-side dedupe key)
- ``X-Craftsman-Signature-256``  — ``sha256=<hmac-sha256 hex of the raw body>``

**SSRF.** The endpoint URL passed the M0.5 guard at registration, but DNS can
change between registration and delivery — so :func:`validate_url` runs again
immediately before every POST (https-only, allowed port, public-IP-only
resolution). A rebound hostname is refused, not fetched.
"""

import hashlib
import hmac
import json

import httpx

from craftsman.research.fetch import UnsafeURL, validate_url  # noqa: F401 — re-exported: the delivery task calls the guard through this module so tests can patch one seam

EVENT_HEADER = "X-Craftsman-Event"
DELIVERY_HEADER = "X-Craftsman-Delivery"
SIGNATURE_HEADER = "X-Craftsman-Signature-256"

REQUEST_TIMEOUT_S = 10.0
BACKOFF_BASE_S = 30.0
BACKOFF_CAP_S = 3600.0


def sign_body(secret: str, raw_body: bytes) -> str:
    """``sha256=<hex hmac>`` over the raw body — see the module docstring."""
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def backoff_seconds(attempt: int) -> float:
    """Exponential retry delay after failed attempt N (1-based):
    30s, 60s, 120s, ... capped at one hour."""
    return min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_CAP_S)


def build_body(delivery) -> bytes:
    """The canonical raw body — built exactly once per attempt and signed as-is,
    so the signature always matches the bytes on the wire."""
    return json.dumps(
        {
            "event": delivery.event_type,
            "delivery_id": str(delivery.id),
            "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
            "payload": delivery.payload,
        },
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()


def post_delivery(url: str, body: bytes, headers: dict) -> None:
    """The HTTP seam (tests monkeypatch this). Raises on network errors and
    non-2xx responses — the delivery task turns that into retry/backoff."""
    resp = httpx.post(url, content=body, headers=headers, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
