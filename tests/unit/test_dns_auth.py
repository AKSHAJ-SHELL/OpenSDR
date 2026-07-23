"""SPF / DKIM / DMARC parsing (M1.4). No network: every test patches `resolve_txt`,
the single DNS seam."""

import dns.resolver
import pytest

from craftsman.deliverability import dns_auth
from craftsman.deliverability.dns_auth import (
    ERROR,
    MISSING,
    PASS,
    check_dkim,
    check_dmarc,
    check_spf,
    looks_like_primary_domain,
)


def _patch(monkeypatch, table):
    """table: name -> list[str] TXT records, or an Exception to raise."""

    def fake(name, timeout=5.0):
        val = table.get(name)
        if isinstance(val, Exception):
            raise val
        return list(val or [])

    monkeypatch.setattr(dns_auth, "resolve_txt", fake)


# ---- SPF ----------------------------------------------------------------


def test_spf_present(monkeypatch):
    _patch(monkeypatch, {"acme.com": ["v=spf1 include:_spf.google.com ~all"]})
    r = check_spf("acme.com")
    assert r.status == PASS
    assert r.record == "v=spf1 include:_spf.google.com ~all"


def test_spf_absent(monkeypatch):
    _patch(monkeypatch, {"acme.com": ["some-other-txt-verification=abc123"]})
    assert check_spf("acme.com").status == MISSING


def test_spf_ignores_token_not_at_start(monkeypatch):
    # a TXT that merely mentions v=spf1 mid-string is NOT an SPF record
    _patch(monkeypatch, {"acme.com": ["note: our v=spf1 lives elsewhere"]})
    assert check_spf("acme.com").status == MISSING


def test_spf_picks_the_spf_among_many(monkeypatch):
    _patch(monkeypatch, {"acme.com": ["google-site-verification=x", "v=spf1 -all"]})
    r = check_spf("acme.com")
    assert r.status == PASS
    assert r.record == "v=spf1 -all"


def test_spf_error_on_resolver_timeout(monkeypatch):
    _patch(monkeypatch, {"acme.com": dns.resolver.LifetimeTimeout()})
    r = check_spf("acme.com")
    assert r.status == ERROR
    assert r.recommended  # still hands over a fix-it value


# ---- DMARC --------------------------------------------------------------


def test_dmarc_present_with_policy(monkeypatch):
    _patch(monkeypatch, {"_dmarc.acme.com": ["v=DMARC1; p=reject; rua=mailto:d@acme.com"]})
    r = check_dmarc("acme.com")
    assert r.status == PASS
    assert r.policy == "reject"


def test_dmarc_absent(monkeypatch):
    _patch(monkeypatch, {"_dmarc.acme.com": []})
    r = check_dmarc("acme.com")
    assert r.status == MISSING
    assert "acme.com" in r.recommended  # generated starter names the domain


def test_dmarc_looks_up_the_dmarc_name(monkeypatch):
    # the record lives at _dmarc.<domain>, not <domain>
    _patch(monkeypatch, {"acme.com": ["v=DMARC1; p=none"]})
    assert check_dmarc("acme.com").status == MISSING


# ---- DKIM ---------------------------------------------------------------


def test_dkim_stored_selector_hit(monkeypatch):
    _patch(monkeypatch, {"s99._domainkey.acme.com": ["v=DKIM1; k=rsa; p=MIGf..."]})
    r = check_dkim("acme.com", selector="s99")
    assert r.status == PASS
    assert r.selector == "s99"


def test_dkim_probe_finds_common_selector(monkeypatch):
    _patch(monkeypatch, {"google._domainkey.acme.com": ["v=DKIM1; k=rsa; p=abc"]})
    r = check_dkim("acme.com")  # no stored selector → probe
    assert r.status == PASS
    assert r.selector == "google"


def test_dkim_all_miss(monkeypatch):
    _patch(monkeypatch, {})  # nothing resolves
    assert check_dkim("acme.com").status == MISSING


def test_dkim_stored_selector_error_reads_as_error(monkeypatch):
    _patch(monkeypatch, {"s1._domainkey.acme.com": dns.resolver.LifetimeTimeout()})
    r = check_dkim("acme.com", selector="s1")
    assert r.status == ERROR
    assert r.selector == "s1"


# ---- primary-domain heuristic -------------------------------------------


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("acme.com", True),
        ("acme.io", True),
        ("outbound.acme.com", False),
        ("mail.go.acme.com", False),
    ],
)
def test_looks_like_primary_domain(domain, expected):
    assert looks_like_primary_domain(domain) is expected
