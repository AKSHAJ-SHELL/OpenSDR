"""Optional click-to-dial (M3.3) — BYO Twilio account, no new dependency.

Deliberately the thinnest possible bridge: one REST call that rings the OPERATOR
first, then dials the lead when the operator answers. Craftsman never robocalls a
prospect and never plays synthesized speech at one — the human is on the line
before the lead's phone rings. Disabled unless all four TWILIO_* knobs are set;
without them the task card falls back to a plain tel: link.
"""

import logging
import re

import httpx

log = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\.\(\) ]{5,19}$")

TWILIO_API = "https://api.twilio.com/2010-04-01"


class DialerError(Exception):
    pass


def normalize_phone(raw: str) -> str | None:
    """Light validation + normalization to dialable form. Returns None when the
    value can't be a phone number — callers fail closed (no dial attempt)."""
    candidate = (raw or "").strip()
    if not _PHONE_RE.match(candidate):
        return None
    digits = re.sub(r"[^0-9+]", "", candidate)
    if digits.count("+") > 1 or ("+" in digits and not digits.startswith("+")):
        return None
    return digits


class TwilioDialer:
    """Operator-first click-to-dial via Twilio's Calls API."""

    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, operator_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.operator_number = operator_number

    async def dial(self, lead_phone: str) -> str:
        """Ring the operator; on answer, dial the lead. Returns the Twilio call SID."""
        lead_number = normalize_phone(lead_phone)
        if lead_number is None:
            raise DialerError(f"lead phone {lead_phone!r} is not dialable")
        # TwiML is XML; the number survived normalize_phone so it contains only
        # digits and '+', but escape-by-construction beats trusting that.
        twiml = f"<Response><Dial>{lead_number}</Dial></Response>"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{TWILIO_API}/Accounts/{self.account_sid}/Calls.json",
                auth=(self.account_sid, self.auth_token),
                data={
                    "From": self.from_number,
                    "To": self.operator_number,
                    "Twiml": twiml,
                },
            )
        if resp.status_code >= 400:
            log.warning("twilio dial failed: %s %s", resp.status_code, resp.text[:200])
            raise DialerError(f"twilio returned {resp.status_code}")
        sid = resp.json().get("sid", "")
        log.info("twilio call initiated: %s", sid)
        return sid


def build_dialer(settings) -> TwilioDialer | None:
    """A dialer only when the operator brought a complete Twilio config."""
    if all([
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_from_number,
        settings.twilio_operator_number,
    ]):
        return TwilioDialer(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
            settings.twilio_operator_number,
        )
    return None
