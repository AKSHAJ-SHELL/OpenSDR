"""M3.3: optional Twilio click-to-dial — operator-first, fail-closed, keyless-off."""

import httpx
import pytest

from craftsman.sender.dialer import DialerError, TwilioDialer, build_dialer, normalize_phone


class _Settings:
    twilio_account_sid = ""
    twilio_auth_token = ""
    twilio_from_number = ""
    twilio_operator_number = ""


def test_no_dialer_without_complete_config():
    s = _Settings()
    assert build_dialer(s) is None
    s.twilio_account_sid = "AC123"
    s.twilio_auth_token = "tok"
    s.twilio_from_number = "+15550001111"
    assert build_dialer(s) is None  # operator number still missing
    s.twilio_operator_number = "+15550002222"
    assert build_dialer(s) is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (415) 555-0123", "+14155550123"),
        ("415.555.0123", "4155550123"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone_accepts_real_numbers(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "not a phone", "call me maybe", "123", "+1+2345678", "<script>1</script>",
     "5551234567;ext=99"],
)
def test_normalize_phone_rejects_garbage(raw):
    """Fail closed: anything that isn't clearly a phone number never reaches Twilio
    (and can never smuggle characters into the TwiML)."""
    assert normalize_phone(raw) is None


async def test_dial_rings_operator_and_dials_lead(monkeypatch):
    captured = {}

    async def fake_post(self, url, auth=None, data=None):
        captured.update({"url": url, "auth": auth, "data": data})
        return httpx.Response(201, json={"sid": "CA_test123"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dialer = TwilioDialer("AC123", "tok", "+15550001111", "+15550002222")
    sid = await dialer.dial("+1 (415) 555-0123")

    assert sid == "CA_test123"
    assert "AC123/Calls.json" in captured["url"]
    assert captured["auth"] == ("AC123", "tok")
    # operator-first: To is the OPERATOR; the lead is inside the Dial verb
    assert captured["data"]["To"] == "+15550002222"
    assert captured["data"]["From"] == "+15550001111"
    assert captured["data"]["Twiml"] == "<Response><Dial>+14155550123</Dial></Response>"


async def test_dial_undialable_number_fails_closed(monkeypatch):
    async def fake_post(self, url, auth=None, data=None):  # pragma: no cover
        raise AssertionError("network reached with an undialable number")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dialer = TwilioDialer("AC123", "tok", "+15550001111", "+15550002222")
    with pytest.raises(DialerError):
        await dialer.dial("not a phone")


async def test_twilio_error_surfaces_as_dialer_error(monkeypatch):
    async def fake_post(self, url, auth=None, data=None):
        return httpx.Response(401, text="auth failed", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dialer = TwilioDialer("AC123", "bad", "+15550001111", "+15550002222")
    with pytest.raises(DialerError, match="401"):
        await dialer.dial("+14155550123")
