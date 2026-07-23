"""SPF / DKIM / DMARC checks by live DNS lookup, plus the copy-paste values to fix
what's missing.

The honesty rules the whole product runs on apply here too:
- SPF and DMARC we can hand the operator a concrete value (DMARC fully; SPF as a
  template with a marked provider placeholder — we never fabricate a provider).
- DKIM keys are minted by the *sending provider*, not by us, so DKIM is verify-only:
  we check the stored selector if set, else probe common ones, and never invent a key.
- A resolver timeout / SERVFAIL is reported as `error` ("couldn't check"), never as a
  false `missing` — a flaky network must not read as a misconfigured domain.

`resolve_txt` is the single network seam; tests monkeypatch it (the `_resolve_ips`
pattern from the SSRF guard) so parser tests never touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import dns.exception
import dns.resolver

# Selectors we probe when the operator hasn't told us theirs. Ordered by prevalence:
# Google Workspace, then the common defaults from Microsoft 365 / SendGrid / Mailgun /
# Amazon SES / generic self-hosted.
COMMON_DKIM_SELECTORS = ("google", "default", "selector1", "selector2", "k1", "dkim", "s1", "mail")

# status values
PASS = "pass"
MISSING = "missing"
ERROR = "error"


def resolve_txt(name: str, timeout: float = 5.0) -> list[str]:
    """Return the TXT strings at `name`, joined per-record. Empty list on NXDOMAIN /
    no-answer; raises only stays internal — callers get [] for "not there" and rely on
    the `error` status for "couldn't check" (see `_txt_or_error`)."""
    answers = dns.resolver.resolve(name, "TXT", lifetime=timeout)
    out: list[str] = []
    for rdata in answers:
        # a TXT record is a sequence of strings; DKIM keys are split across 255-byte
        # chunks, so join them before matching.
        out.append(b"".join(rdata.strings).decode("utf-8", "replace"))
    return out


def _txt_or_error(name: str) -> tuple[list[str], bool]:
    """(records, errored). NXDOMAIN / empty answer → ([], False) = "not there".
    Timeout / SERVFAIL → ([], True) = "couldn't check"."""
    try:
        return resolve_txt(name), False
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return [], False
    except (dns.exception.DNSException, OSError):
        return [], True


@dataclass
class SpfResult:
    status: str
    record: str | None = None
    recommended: str = ""


@dataclass
class DmarcResult:
    status: str
    policy: str | None = None
    record: str | None = None
    recommended: str = ""


@dataclass
class DkimResult:
    status: str
    selector: str | None = None
    record: str | None = None


def check_spf(domain: str) -> SpfResult:
    recommended = "v=spf1 include:_spf.YOUR-PROVIDER.com ~all"
    records, errored = _txt_or_error(domain)
    if errored:
        return SpfResult(status=ERROR, recommended=recommended)
    for r in records:
        # must START with v=spf1 (a TXT that merely contains the token mid-string is not
        # an SPF record and must not read as a pass).
        if r.strip().lower().startswith("v=spf1"):
            return SpfResult(status=PASS, record=r.strip(), recommended=recommended)
    return SpfResult(status=MISSING, recommended=recommended)


def check_dmarc(domain: str) -> DmarcResult:
    recommended = f"v=DMARC1; p=none; rua=mailto:dmarc@{domain}"
    records, errored = _txt_or_error(f"_dmarc.{domain}")
    if errored:
        return DmarcResult(status=ERROR, recommended=recommended)
    for r in records:
        if r.strip().lower().startswith("v=dmarc1"):
            return DmarcResult(
                status=PASS, policy=_dmarc_policy(r), record=r.strip(), recommended=recommended
            )
    return DmarcResult(status=MISSING, recommended=recommended)


def _dmarc_policy(record: str) -> str | None:
    for tag in record.split(";"):
        k, _, v = tag.strip().partition("=")
        if k.strip().lower() == "p":
            return v.strip().lower() or None
    return None


def check_dkim(domain: str, selector: str | None = None) -> DkimResult:
    """Verify a DKIM key exists. A stored selector is authoritative; otherwise probe the
    common list and report whichever resolves first. If a probe hits a resolver error we
    report `error` only when *no* selector resolved (so one flaky lookup among probes
    doesn't mask a real hit)."""
    selectors = [selector] if selector else list(COMMON_DKIM_SELECTORS)
    saw_error = False
    for sel in selectors:
        records, errored = _txt_or_error(f"{sel}._domainkey.{domain}")
        saw_error = saw_error or errored
        for r in records:
            low = r.strip().lower()
            if "v=dkim1" in low or "p=" in low:  # DKIM TXT: v=DKIM1; k=rsa; p=<key>
                return DkimResult(status=PASS, selector=sel, record=r.strip())
    if saw_error and selector:
        # explicit selector we couldn't check → "couldn't check", not "missing"
        return DkimResult(status=ERROR, selector=selector)
    return DkimResult(status=MISSING, selector=selector)


def looks_like_primary_domain(domain: str) -> bool:
    """Soft signal: a bare registrable-looking domain (`acme.com`) rather than a
    dedicated sending subdomain (`outbound.acme.com`). Deliberately a heuristic — we do
    not ship a public-suffix list — so the UI treats it as an escalation of always-shown
    guidance, never a hard verdict. Two-label ccTLD combos (`acme.co.uk`) will read as a
    subdomain here; that's an acceptable false-negative for a soft nudge."""
    labels = [l for l in domain.strip(".").split(".") if l]
    return len(labels) <= 2
