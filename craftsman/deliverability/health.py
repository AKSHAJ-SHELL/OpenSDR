"""Per-domain deliverability health (M5.3, G12): rollups, blocklists, one score.

The health score formula — documented here so it is inspectable, never a black
box. Start at 100, subtract:

- SPF missing: **-15**; DKIM missing: **-15**; DMARC missing: **-15**
  (dns_auth status ``missing`` only — ``error`` means "couldn't check" and is
  never penalized: a flaky resolver must not read as a misconfigured domain)
- each blocklist listing: **-40**
- trailing-7-day hard-bounce rate over 5%: **-30**; over 2%: **-10**
  (no penalty when the domain sent nothing — no data is not bad data)
- trailing-7-day complaint-proxy rate over 0.1%: **-20**

then clamp to 0–100.

Complaint proxy: self-hosters have no feedback-loop (FBL) feed, so bounces whose
diagnostic text mentions spam/block/reputation are counted as ``spam_bounces`` —
a best-effort stand-in for real complaint data (FBL ingestion is the future hook).

DNSBL checks query the A record of ``{reversed-ip}.{zone}`` for each of the
domain's MX/A IPs against the operator-configured ``blocklist_zones``. This is
**pure DNS — deliberately exempt from the SSRF guard**: no HTTP request is ever
made, the query names are derived from fixed config zones plus IPs we resolved
ourselves, and the only data received is an address-shaped yes/no.

``_dns_query`` is the single network seam (the ``resolve_txt`` pattern from
dns_auth.py); tests monkeypatch it so nothing here ever touches live DNS.

Auto-pause: ``record_domain_bounce`` also enforces the day's bounce budget —
once a domain's hard+spam bounces reach ``domain_pause_bounce_threshold``,
every mailbox on that domain is set health='paused', the event is audit-logged
(``domain_auto_paused``) and urgently notified. Un-pause is an explicit operator
action (PATCH /mailboxes health='ok'); nothing automatic revives a paused domain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import dns.exception
import dns.resolver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.core.config import get_settings
from craftsman.core.models import DomainStat, Mailbox
from craftsman.core.tenancy import require_org_id

log = logging.getLogger(__name__)

# statuses a blocklist verdict may carry
LISTED = "listed"
CLEAR = "clear"
ERROR = "error"  # couldn't check — never counted as a listing

# diagnostic substrings that mark a bounce as the complaint proxy ("block"
# also catches "blocked"/"blocklisted"); everything else is a plain hard bounce
SPAM_BOUNCE_TOKENS = ("spam", "block", "reputation")

# score deductions — the documented formula above, as data
DNS_AUTH_PENALTY = 15  # per missing SPF/DKIM/DMARC record
BLOCKLIST_PENALTY = 40  # per listing
BOUNCE_RATE_WARN = 0.02  # > 2% → -10
BOUNCE_RATE_BAD = 0.05  # > 5% → -30
BOUNCE_WARN_PENALTY = 10
BOUNCE_BAD_PENALTY = 30
COMPLAINT_RATE_BAD = 0.001  # > 0.1% → -20
COMPLAINT_PENALTY = 20


def _dns_query(name: str, rdtype: str = "A", timeout: float = 5.0) -> list[str]:
    """The single DNS seam: record values at `name`, [] on NXDOMAIN / no answer.
    Resolver errors propagate — callers map them to the `error` status."""
    try:
        answers = dns.resolver.resolve(name, rdtype, lifetime=timeout)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    return [str(r).rstrip(".") for r in answers]


def _query_or_error(name: str, rdtype: str = "A") -> tuple[list[str], bool]:
    """(records, errored) — same honesty contract as dns_auth._txt_or_error."""
    try:
        return _dns_query(name, rdtype), False
    except (dns.exception.DNSException, OSError):
        return [], True


def resolve_sending_ips(domain: str, max_ips: int = 8) -> list[str]:
    """The IPs mail for `domain` originates/lands on: A records of its MX hosts,
    falling back to the domain's own A record when it has no MX. Deduped, capped
    so a pathological MX set can't fan a blocklist check out indefinitely."""
    ips: list[str] = []
    mx_hosts, _ = _query_or_error(domain, "MX")
    hosts = [h.split()[-1] for h in mx_hosts] or [domain]
    for host in hosts:
        records, _ = _query_or_error(host, "A")
        for ip in records:
            if ip not in ips:
                ips.append(ip)
        if len(ips) >= max_ips:
            break
    return ips[:max_ips]


def _reverse_ip(ip: str) -> str:
    return ".".join(reversed(ip.split(".")))


@dataclass
class BlocklistVerdict:
    zone: str
    status: str  # listed | clear | error
    listed_ips: list[str] = field(default_factory=list)


def check_blocklists(domain: str, zones: list[str]) -> list[BlocklistVerdict]:
    """DNSBL verdict per zone: an A answer for {reversed-ip}.{zone} means listed;
    NXDOMAIN means clear; a resolver failure is `error` ("couldn't check"), never
    a listing — the same honesty rule dns_auth applies to SPF/DKIM/DMARC."""
    ips = resolve_sending_ips(domain)
    verdicts: list[BlocklistVerdict] = []
    for zone in zones:
        listed: list[str] = []
        errored = False
        for ip in ips:
            records, err = _query_or_error(f"{_reverse_ip(ip)}.{zone}", "A")
            errored = errored or err
            if records:
                listed.append(ip)
        if listed:
            verdicts.append(BlocklistVerdict(zone=zone, status=LISTED, listed_ips=listed))
        elif errored or not ips:
            # no IPs resolved also means "couldn't check", not "clear"
            verdicts.append(BlocklistVerdict(zone=zone, status=ERROR))
        else:
            verdicts.append(BlocklistVerdict(zone=zone, status=CLEAR))
    return verdicts


def blocklist_zones() -> list[str]:
    return [z.strip() for z in get_settings().blocklist_zones.split(",") if z.strip()]


# ------------------------------------------------------------------ bounce rollup


def classify_bounce(diagnostic: str | None) -> str:
    """'spam' (complaint proxy) when the diagnostic mentions spam/block/reputation,
    else 'hard'. Best-effort text matching — the diagnostic is whatever the
    bounce message carried."""
    low = (diagnostic or "").lower()
    if any(tok in low for tok in SPAM_BOUNCE_TOKENS):
        return "spam"
    return "hard"


def _bump_domain_stat(
    db: Session, domain: str, *, sends: int = 0, hard: int = 0, spam: int = 0
) -> None:
    """Atomic upsert of today's (org, domain) row. A Core INSERT..ON CONFLICT so
    concurrent workers can't lose increments — Core statements bypass the ORM
    tenancy stamp, hence the explicit org_id."""
    import uuid as _uuid

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(DomainStat).values(
        id=_uuid.uuid4(),
        org_id=require_org_id(),
        domain=domain,
        day=date.today(),
        sends=sends,
        hard_bounces=hard,
        spam_bounces=spam,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_domain_stats_org_domain_day",
        set_={
            "sends": DomainStat.__table__.c.sends + sends,
            "hard_bounces": DomainStat.__table__.c.hard_bounces + hard,
            "spam_bounces": DomainStat.__table__.c.spam_bounces + spam,
        },
    )
    db.execute(stmt)


def record_domain_send(db: Session, domain: str) -> None:
    """One delivered message from `domain` — called where mailbox.sent_today is
    incremented, so the rollup and the mailbox counter can never disagree."""
    _bump_domain_stat(db, domain, sends=1)


def record_domain_bounce(db: Session, domain: str, diagnostic: str | None = None) -> None:
    """Roll a bounce into today's domain row, then enforce the day's bounce budget."""
    kind = classify_bounce(diagnostic)
    _bump_domain_stat(
        db, domain, hard=1 if kind == "hard" else 0, spam=1 if kind == "spam" else 0
    )
    _maybe_auto_pause(db, domain)


def _maybe_auto_pause(db: Session, domain: str) -> None:
    """domain_pause_bounce_threshold hard+spam bounces today ⇒ pause every mailbox
    on the domain. Fires once per crossing: already-paused domains (no unpaused
    mailbox left) don't re-notify on each further bounce."""
    threshold = get_settings().domain_pause_bounce_threshold
    if threshold <= 0:  # explicit off switch
        return
    total = db.scalar(
        select(func.coalesce(func.sum(DomainStat.hard_bounces + DomainStat.spam_bounces), 0)).where(
            DomainStat.domain == domain, DomainStat.day == date.today()
        )
    )
    if total is None or total < threshold:
        return
    boxes = list(
        db.scalars(
            select(Mailbox).where(
                func.lower(Mailbox.email).like(f"%@{domain}"), Mailbox.health != "paused"
            )
        )
    )
    if not boxes:
        return
    for box in boxes:
        box.health = "paused"
        db.add(box)
    from craftsman.core.models import AuditLog

    db.add(
        AuditLog(
            event="domain_auto_paused",
            detail={
                "domain": domain,
                "bounces_today": int(total),
                "threshold": threshold,
                "mailboxes": [b.email for b in boxes],
            },
        )
    )
    _urgent_notify(
        f":rotating_light: *Domain auto-paused: {domain}*\n"
        f"{int(total)} hard/spam bounces today (threshold {threshold}). "
        f"Paused {len(boxes)} mailbox(es). Un-pause via PATCH /mailboxes when fixed."
    )
    log.warning(
        "domain %s auto-paused: %s bounces today >= %s (%d mailboxes)",
        domain, total, threshold, len(boxes),
    )


def _urgent_notify(text: str) -> None:
    """Slack webhook if configured — the existing notifier pattern, local to avoid
    an import cycle with inbox/pipeline (which imports the send engine)."""
    url = get_settings().slack_webhook_url
    if not url:
        log.info("urgent notify skipped (no webhook configured): %s", text.splitlines()[0])
        return
    try:
        import httpx

        httpx.post(url, json={"text": text}, timeout=10)
    except Exception as e:  # noqa: BLE001 — a notify failure never blocks bounce handling
        log.warning("Slack notify failed: %s", e)


# ------------------------------------------------------------------ score


@dataclass
class SevenDayStats:
    sends: int = 0
    hard_bounces: int = 0
    spam_bounces: int = 0

    @property
    def bounce_rate(self) -> float:
        return self.hard_bounces / self.sends if self.sends else 0.0

    @property
    def complaint_rate(self) -> float:
        return self.spam_bounces / self.sends if self.sends else 0.0


def seven_day_stats(db: Session, domain: str, today: date | None = None) -> SevenDayStats:
    today = today or date.today()
    row = db.execute(
        select(
            func.coalesce(func.sum(DomainStat.sends), 0),
            func.coalesce(func.sum(DomainStat.hard_bounces), 0),
            func.coalesce(func.sum(DomainStat.spam_bounces), 0),
        ).where(DomainStat.domain == domain, DomainStat.day >= today - timedelta(days=6))
    ).one()
    return SevenDayStats(sends=int(row[0]), hard_bounces=int(row[1]), spam_bounces=int(row[2]))


def health_score(
    *,
    spf_status: str,
    dkim_status: str,
    dmarc_status: str,
    blocklist_listings: int,
    stats: SevenDayStats,
) -> tuple[int, dict[str, int]]:
    """(score 0-100, deductions per component) — exactly the documented formula.
    Components carry the points DEDUCTED (0 = clean) so the breakdown always
    reconciles: 100 - sum(components), clamped."""
    missing = sum(1 for s in (spf_status, dkim_status, dmarc_status) if s == "missing")
    components = {
        "dns_auth": missing * DNS_AUTH_PENALTY,
        "blocklist": blocklist_listings * BLOCKLIST_PENALTY,
        "bounce_rate": (
            BOUNCE_BAD_PENALTY
            if stats.bounce_rate > BOUNCE_RATE_BAD
            else BOUNCE_WARN_PENALTY if stats.bounce_rate > BOUNCE_RATE_WARN else 0
        ),
        "complaint_rate": (
            COMPLAINT_PENALTY if stats.complaint_rate > COMPLAINT_RATE_BAD else 0
        ),
    }
    score = max(0, min(100, 100 - sum(components.values())))
    return score, components
