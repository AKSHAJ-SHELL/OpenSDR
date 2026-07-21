"""Unit tests for the research-fetch SSRF guard (M0.5, finding A2).

Pure functions only — DNS is stubbed via the module's _resolve_ips, so no network.
"""

import pytest

from craftsman.research import fetch
from craftsman.research.fetch import UnsafeURL, _is_public_ip, validate_url


# ---------------------------------------------------------------- _is_public_ip

PUBLIC = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]
BLOCKED = [
    "10.0.0.1",         # private
    "172.16.0.1",       # private
    "192.168.1.1",      # private
    "127.0.0.1",        # loopback
    "169.254.169.254",  # link-local — the cloud metadata endpoint
    "0.0.0.0",          # unspecified
    "100.64.0.1",       # CGNAT
    "224.0.0.1",        # multicast
    "::1",              # loopback v6
    "fc00::1",          # unique-local v6
    "fe80::1",          # link-local v6
    "not-an-ip",        # garbage
]


@pytest.mark.parametrize("ip", PUBLIC)
def test_public_ips_pass(ip):
    assert _is_public_ip(ip) is True


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ips_fail(ip):
    assert _is_public_ip(ip) is False


# ---------------------------------------------------------------- validate_url


def test_rejects_non_https_schemes():
    for url in ["http://example.com/", "file:///etc/passwd", "ftp://example.com/",
                "gopher://example.com/", "data:text/plain,hi"]:
        with pytest.raises(UnsafeURL):
            validate_url(url)


def test_rejects_disallowed_port():
    # port check fires before any resolution — localhost:6379 (Redis) is dead on arrival
    with pytest.raises(UnsafeURL):
        validate_url("https://localhost:6379/")
    with pytest.raises(UnsafeURL):
        validate_url("https://example.com:8443/")


def test_rejects_literal_private_and_metadata_ips():
    for host in ["169.254.169.254", "127.0.0.1", "10.0.0.5", "192.168.1.1", "[::1]"]:
        with pytest.raises(UnsafeURL):
            validate_url(f"https://{host}/")


def test_rejects_host_resolving_to_private(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve_ips", lambda h: ["10.0.0.5"])
    with pytest.raises(UnsafeURL):
        validate_url("https://internal.corp/")


def test_rejects_host_with_any_private_record(monkeypatch):
    # one public + one private record → rejected wholesale (split-horizon defense)
    monkeypatch.setattr(fetch, "_resolve_ips", lambda h: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(UnsafeURL):
        validate_url("https://mixed.example/")


def test_rejects_unresolvable_host(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve_ips", lambda h: [])
    with pytest.raises(UnsafeURL):
        validate_url("https://nope.invalid/")


def test_accepts_public_host(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve_ips", lambda h: ["93.184.216.34"])
    validate_url("https://example.com/about")  # no raise


def test_accepts_literal_public_ip():
    validate_url("https://93.184.216.34/")  # no raise
