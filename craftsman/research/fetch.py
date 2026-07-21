"""Fetch and clean company web pages. httpx + selectolax; ~6k-token budget.

SSRF guard (M0.5): the domain that seeds these fetches comes from CSV import, so it
is attacker-controllable. Every URL — and every redirect hop — is validated to be
https, on an allowed port, resolving only to public IPs, before any request is made.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.parser import HTMLParser

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; CraftsmanResearch/0.1)"
MAX_CHARS = 24_000  # ~6k tokens
PATHS = ["", "/about", "/about-us", "/company"]
MAX_REDIRECTS = 3
ALLOWED_PORTS = {443}  # https default; no room for localhost:6379 and friends

_STRIP_TAGS = ["script", "style", "nav", "footer", "header", "svg", "noscript", "form", "iframe"]


class UnsafeURL(Exception):
    """Raised when a URL fails the SSRF guard (bad scheme/port, or private IP)."""


def _is_public_ip(ip_str: str) -> bool:
    """True only for globally-routable unicast addresses.

    Rejects private, loopback, link-local (incl. the 169.254 cloud-metadata range),
    CGNAT (100.64/10), multicast, reserved, and unspecified addresses.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    # CGNAT 100.64.0.0/10 is not flagged is_private by ipaddress; block it explicitly.
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return ip.is_global


def _resolve_ips(host: str) -> list[str]:
    """All A/AAAA records for host. Empty list if it doesn't resolve."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def validate_url(url: str) -> None:
    """Raise UnsafeURL unless url is https, on an allowed port, and resolves only
    to public IPs. A host with even one private resolved IP is rejected wholesale
    (defeats split-horizon / partial-poisoning tricks)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURL(f"scheme not allowed: {parts.scheme!r} (https only)")

    host = parts.hostname
    if not host:
        raise UnsafeURL("no host in URL")

    port = parts.port
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeURL(f"port not allowed: {port}")

    # If the host is an IP literal, check it directly; otherwise resolve every record.
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        candidates = _resolve_ips(host)
        if not candidates:
            raise UnsafeURL(f"host does not resolve: {host!r}")

    for ip in candidates:
        if not _is_public_ip(ip):
            raise UnsafeURL(f"host {host!r} resolves to non-public address {ip}")


def clean_html(html: str) -> str:
    tree = HTMLParser(html)
    for tag in _STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    text = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


async def _safe_get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """GET url, following redirects manually and re-validating every hop against the
    SSRF guard. Returns the final 200 response, or None if any hop is unsafe / fails."""
    loop = asyncio.get_running_loop()
    for _ in range(MAX_REDIRECTS + 1):
        try:
            # DNS resolution + IP checks are blocking; keep them off the event loop.
            await loop.run_in_executor(None, validate_url, url)
        except UnsafeURL as e:
            log.warning("SSRF guard blocked %s: %s", url, e)
            return None
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            return None
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return None
            url = urljoin(url, location)  # re-validated at the top of the next loop
            continue
        return resp
    log.warning("SSRF guard: too many redirects starting from %s", url)
    return None


async def fetch_company_text(domain: str) -> dict[str, str]:
    """Return {url: cleaned_text} for the homepage + about pages that resolve.

    Unsafe or unreachable URLs are skipped (logged); a fully-poisoned domain yields
    an empty dict, which upstream turns into a ResearchError → the lead never sends.
    """
    results: dict[str, str] = {}
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,  # we follow manually, validating each hop
        timeout=15,
    ) as client:

        async def _get(path: str) -> None:
            url = f"https://{domain}{path}"
            resp = await _safe_get(client, url)
            if resp is None:
                return
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                text = clean_html(resp.text)
                if len(text) > 200:  # skip empty shells
                    results[url] = text

        await asyncio.gather(*[_get(p) for p in PATHS])

    # budget: homepage first, then extras until MAX_CHARS
    budget = MAX_CHARS
    trimmed: dict[str, str] = {}
    for url, text in results.items():
        if budget <= 0:
            break
        take = text[:budget]
        trimmed[url] = take
        budget -= len(take)
    return trimmed
