"""Signed SSO state + one-time login codes (M5.1b).

The login code is the only artifact the browser carries out of the SSO flow —
its signature, expiry, and one-time redemption are the whole security story.
"""

import time
import uuid

import fakeredis
import pytest

from craftsman.sso import oidc as sso


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("CRAFTSMAN_SECRET_KEY", "test-secret-key")
    from craftsman.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_state_round_trip_binds_nonce():
    state = sso.make_state("nonce-abc")
    assert sso.check_state(state) == "nonce-abc"


def test_state_tamper_and_garbage_rejected():
    state = sso.make_state("n")
    assert sso.check_state(state[:-2] + "zz") is None
    assert sso.check_state("not-a-state") is None
    assert sso.check_state("") is None


def test_state_expires(monkeypatch):
    state = sso.make_state("n")
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999.0)
    assert sso.check_state(state) is None


def test_login_code_redeems_exactly_once():
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    uid = uuid.uuid4()
    code = sso.mint_login_code(uid)
    assert sso.redeem_login_code(code, r=r) == uid
    # the second redemption hits the jti tombstone
    assert sso.redeem_login_code(code, r=r) is None


def test_login_code_tamper_rejected():
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    code = sso.mint_login_code(uuid.uuid4())
    assert sso.redeem_login_code(code[:-2] + "zz", r=r) is None


def test_state_is_not_a_login_code():
    """A signed blob of the wrong kind must not cross-redeem."""
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    assert sso.redeem_login_code(sso.make_state("n"), r=r) is None
