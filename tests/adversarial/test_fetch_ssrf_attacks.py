"""Adversarial SSRF tests — the four named vectors from the A2 finding, plus the
redirect bound. Predict-then-run; fully offline via a fake httpx client.

Every prediction is stated before the assertion. The guard must block/skip; nothing
unsafe may be fetched.
"""

import asyncio

import httpx
import pytest

from craftsman.research import fetch
from craftsman.research.fetch import MAX_REDIRECTS, _safe_get


class FakeClient:
    """Records every URL fetched and returns pre-scripted responses."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        return self.responses[url]


def _resp(url: str, status: int, *, location: str | None = None, body: str = "") -> httpx.Response:
    headers = {}
    if location is not None:
        headers["location"] = location
    if body:
        headers["content-type"] = "text/html"
    return httpx.Response(status, headers=headers, text=body, request=httpx.Request("GET", url))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def public_dns(monkeypatch):
    """Make every hostname resolve to a public IP, so only scheme/port/literal-IP/
    redirect logic decides the outcome."""
    monkeypatch.setattr(fetch, "_resolve_ips", lambda h: ["93.184.216.34"])


# ------------------------------------------------------ the four named vectors


def test_literal_metadata_ip_never_fetched(public_dns):
    # Predict: BLOCKED before any request — 169.254.169.254 is link-local.
    client = FakeClient({})
    result = _run(_safe_get(client, "https://169.254.169.254/latest/meta-data/"))
    assert result is None
    assert client.calls == []  # guard fired before the network


def test_localhost_redis_port_never_fetched(public_dns):
    # Predict: BLOCKED — port 6379 is not in the allowlist.
    client = FakeClient({})
    result = _run(_safe_get(client, "https://localhost:6379/"))
    assert result is None
    assert client.calls == []


def test_file_scheme_redirect_target_blocked(public_dns):
    # Predict: evil.com is fetched once (public), returns 302 → file:///etc/passwd,
    # which fails the scheme check on the next hop. The file URL is never fetched.
    start = "https://evil.example/"
    client = FakeClient({start: _resp(start, 302, location="file:///etc/passwd")})
    result = _run(_safe_get(client, start))
    assert result is None
    assert client.calls == [start]  # only the first, safe hop


def test_redirect_to_private_ip_blocked(public_dns):
    # Predict: evil.com fetched once, redirects to http://169.254… — blocked on both
    # scheme (http) and IP (link-local). The metadata host is never fetched.
    start = "https://evil.example/"
    client = FakeClient({start: _resp(start, 302, location="http://169.254.169.254/")})
    result = _run(_safe_get(client, start))
    assert result is None
    assert client.calls == [start]


# ------------------------------------------------------------- redirect bound


def test_too_many_redirects_stops(public_dns):
    # Predict: a public->public redirect chain longer than MAX_REDIRECTS returns None.
    hops = [f"https://hop{i}.example/" for i in range(MAX_REDIRECTS + 3)]
    responses = {
        hops[i]: _resp(hops[i], 302, location=hops[i + 1]) for i in range(len(hops) - 1)
    }
    client = FakeClient(responses)
    result = _run(_safe_get(client, hops[0]))
    assert result is None
    assert len(client.calls) <= MAX_REDIRECTS + 1  # bounded, didn't loop forever


# --------------------------------------------------------- happy path still works


def test_public_host_success(public_dns):
    url = "https://acme.example/about"
    body = "<html><body>" + ("Acme builds warehouse robots. " * 20) + "</body></html>"
    client = FakeClient({url: _resp(url, 200, body=body)})
    result = _run(_safe_get(client, url))
    assert result is not None
    assert result.status_code == 200
    assert client.calls == [url]


def test_one_safe_redirect_is_followed(public_dns):
    # A single legitimate redirect to another public URL is allowed and followed.
    start = "https://acme.example/"
    final = "https://acme.example/home"
    body = "<html><body>" + ("real content " * 30) + "</body></html>"
    client = FakeClient({
        start: _resp(start, 301, location=final),
        final: _resp(final, 200, body=body),
    })
    result = _run(_safe_get(client, start))
    assert result is not None and result.status_code == 200
    assert client.calls == [start, final]
