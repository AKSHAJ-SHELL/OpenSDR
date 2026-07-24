"""Calendar providers (M4.3, G8): booked meetings as a first-class outcome.

Craftsman never controls a calendar. The rep's scheduling link (Cal.com page,
self-hosted or cloud) does the booking; Craftsman only *learns about it* through a
signed webhook and records the outcome. Same keyless-off pattern as the Twilio
dialer: no `CALCOM_WEBHOOK_SECRET`, no webhook — the scheduling link in drafts
still works without it.

Fork points: implement `CalendarProvider` for Google Calendar / MS Graph (BYO OAuth
app), register it in `build_provider`, point the webhook route at it. The protocol
is deliberately tiny — verify a webhook, parse it into a `MeetingEvent` — because
that is the entire integration surface Craftsman needs.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Cal.com triggerEvent → meetings.status. Unknown triggers are ignored (200, no-op)
# so Cal.com's webhook retries don't pile up on events we don't model.
CALCOM_STATUS_MAP = {
    "BOOKING_REQUESTED": "proposed",
    "BOOKING_CREATED": "booked",
    "BOOKING_RESCHEDULED": "booked",
    "BOOKING_CANCELLED": "cancelled",
    "BOOKING_REJECTED": "cancelled",
    "MEETING_ENDED": "completed",
    "BOOKING_NO_SHOW_UPDATED": "no_show",
}


@dataclass(frozen=True)
class MeetingEvent:
    provider: str
    provider_event_id: str
    status: str  # proposed | booked | completed | cancelled | no_show
    start_at: datetime | None
    attendee_emails: tuple[str, ...]


class CalendarProvider(Protocol):
    name: str

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool: ...

    def parse_webhook(self, payload: dict) -> MeetingEvent | None: ...


class CalComProvider:
    """Cal.com webhooks: HMAC-SHA256 of the raw body in `X-Cal-Signature-256`."""

    name = "calcom"

    def __init__(self, webhook_secret: str):
        self._secret = webhook_secret.encode()

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        expected = hmac.new(self._secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    def parse_webhook(self, payload: dict) -> MeetingEvent | None:
        status = CALCOM_STATUS_MAP.get(payload.get("triggerEvent", ""))
        body = payload.get("payload") or {}
        uid = body.get("uid") or body.get("bookingId")
        if status is None or not uid:
            return None
        start_raw = body.get("startTime")
        start_at = None
        if start_raw:
            try:
                start_at = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            except ValueError:
                start_at = None
        attendees = tuple(
            a["email"].lower()
            for a in (body.get("attendees") or [])
            if isinstance(a, dict) and a.get("email")
        )
        return MeetingEvent(
            provider=self.name,
            provider_event_id=str(uid),
            status=status,
            start_at=start_at,
            attendee_emails=attendees,
        )


def build_provider(settings) -> CalComProvider | None:
    """Keyless-off: returns None (webhook endpoint answers 503) until the operator
    sets CALCOM_WEBHOOK_SECRET. BYO — Craftsman ships no calendar credentials."""
    if not settings.calcom_webhook_secret:
        return None
    return CalComProvider(settings.calcom_webhook_secret)
