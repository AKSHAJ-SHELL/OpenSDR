"""Adversarial: the DNS auth checker sits on a hostile network boundary. Predict-then-run
(TESTING.md §3) — a flaky resolver or a crafted TXT must never crash and must never read
as a false `pass`. The whole point of the feature is honest status; a false green here
would tell an operator their unprotected domain is fine.
"""

import dns.exception
import dns.resolver
import pytest

from craftsman.deliverability import dns_auth
from craftsman.deliverability.dns_auth import ERROR, MISSING, PASS, check_dkim, check_dmarc, check_spf


def _patch(monkeypatch, table):
    def fake(name, timeout=5.0):
        val = table.get(name)
        if isinstance(val, Exception):
            raise val
        return list(val or [])

    monkeypatch.setattr(dns_auth, "resolve_txt", fake)


@pytest.mark.parametrize(
    "exc",
    [
        dns.resolver.LifetimeTimeout(),
        dns.resolver.NoNameservers(),
        dns.exception.DNSException(),
        OSError("network unreachable"),
    ],
)
def test_resolver_failures_are_error_not_missing_not_crash(monkeypatch, exc):
    # SERVFAIL/timeout must read as "couldn't check", never "missing" (which would tell
    # the operator to add a record they may already have).
    _patch(monkeypatch, {"acme.com": exc, "_dmarc.acme.com": exc})
    assert check_spf("acme.com").status == ERROR
    assert check_dmarc("acme.com").status == ERROR


def test_nxdomain_is_missing_not_error(monkeypatch):
    def fake(name, timeout=5.0):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns_auth, "resolve_txt", fake)
    assert check_spf("acme.com").status == MISSING
    assert check_dmarc("acme.com").status == MISSING
    assert check_dkim("acme.com").status == MISSING


def test_spoofed_spf_substring_does_not_pass(monkeypatch):
    # a record engineered to contain the token without being an SPF record
    _patch(monkeypatch, {"acme.com": ["haha v=spf1 include:evil ~all trust me"]})
    assert check_spf("acme.com").status == MISSING


def test_malformed_dmarc_has_no_policy_but_still_passes_presence(monkeypatch):
    # starts with the version tag but the policy tag is junk → present, policy None
    _patch(monkeypatch, {"_dmarc.acme.com": ["v=DMARC1; garbage"]})
    r = check_dmarc("acme.com")
    assert r.status == PASS
    assert r.policy is None


def test_empty_txt_strings_do_not_crash(monkeypatch):
    _patch(monkeypatch, {"acme.com": ["", "   "], "_dmarc.acme.com": [""]})
    assert check_spf("acme.com").status == MISSING
    assert check_dmarc("acme.com").status == MISSING


def test_dkim_one_flaky_probe_still_finds_the_good_one(monkeypatch):
    # first probed selector errors, a later one resolves — must report pass, not error
    def fake(name, timeout=5.0):
        if name.startswith("google._domainkey"):
            raise dns.resolver.LifetimeTimeout()
        if name.startswith("default._domainkey"):
            return ["v=DKIM1; p=key"]
        return []

    monkeypatch.setattr(dns_auth, "resolve_txt", fake)
    r = check_dkim("acme.com")
    assert r.status == PASS and r.selector == "default"
