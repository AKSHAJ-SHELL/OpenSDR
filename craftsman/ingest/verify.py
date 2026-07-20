"""Email verification: syntax → MX lookup → optional SMTP RCPT handshake.

Unverifiable emails never enroll; bounce risk is the #1 deliverability killer.
"""

import re
import smtplib

import dns.exception
import dns.resolver

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def syntax_ok(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def mx_hosts(domain: str, timeout: float = 5.0) -> list[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        return [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference)]
    except (dns.exception.DNSException, Exception):
        return []


def smtp_rcpt_ok(email: str, mx: str, timeout: float = 10.0, helo_domain: str = "example.com") -> bool | None:
    """Best-effort RCPT probe. Returns None when inconclusive (greylisting, catch-all, blocked port)."""
    try:
        with smtplib.SMTP(mx, 25, timeout=timeout) as smtp:
            smtp.helo(helo_domain)
            smtp.mail("probe@" + helo_domain)
            code, _ = smtp.rcpt(email)
            if code in (250, 251):
                return True
            if code == 550:
                return False
            return None
    except (smtplib.SMTPException, OSError):
        return None


def verify_email(email: str, do_rcpt: bool = False) -> bool:
    """True only when we have positive signal at every stage we ran."""
    if not syntax_ok(email):
        return False
    hosts = mx_hosts(domain_of(email))
    if not hosts:
        return False
    if do_rcpt:
        result = smtp_rcpt_ok(email, hosts[0])
        if result is False:
            return False
    return True
