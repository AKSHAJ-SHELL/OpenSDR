"""Per-domain health units (M5.3): score formula (table-driven), DNSBL parsing
through the `_dns_query` seam, bounce classification, sending-IP resolution.
No live DNS anywhere — every lookup goes through a monkeypatched seam.
"""

import pytest

from craftsman.deliverability import health
from craftsman.deliverability.health import (
    BlocklistVerdict,
    SevenDayStats,
    check_blocklists,
    classify_bounce,
    health_score,
    resolve_sending_ips,
)
from craftsman.ingest.verify import domain_of


def _patch_dns(monkeypatch, table: dict, errors: set | None = None):
    """Seam fixture: (name, rdtype) → records; names in `errors` raise."""
    import dns.resolver

    def fake(name, rdtype="A", timeout=5.0):
        if errors and name in errors:
            raise dns.resolver.LifetimeTimeout
        return list(table.get((name, rdtype), []))

    monkeypatch.setattr(health, "_dns_query", fake)


# ---------------------------------------------------------------- score formula

# (spf, dkim, dmarc, listings, sends, hard, spam) → expected score
SCORE_TABLE = [
    # clean domain, no traffic: perfect
    (("pass", "pass", "pass", 0, 0, 0, 0), 100),
    # each missing DNS record costs 15
    (("missing", "pass", "pass", 0, 0, 0, 0), 85),
    (("missing", "missing", "pass", 0, 0, 0, 0), 70),
    (("missing", "missing", "missing", 0, 0, 0, 0), 55),
    # `error` (couldn't check) is never penalized
    (("error", "error", "error", 0, 0, 0, 0), 100),
    # a blocklist listing costs 40; two cost 80
    (("pass", "pass", "pass", 1, 0, 0, 0), 60),
    (("pass", "pass", "pass", 2, 0, 0, 0), 20),
    # bounce rate: 100 sends — 2% is NOT over the warn line, 3% is, 6% is bad
    (("pass", "pass", "pass", 0, 100, 2, 0), 100),
    (("pass", "pass", "pass", 0, 100, 3, 0), 90),
    (("pass", "pass", "pass", 0, 100, 6, 0), 70),
    # complaint proxy: 1 spam bounce in 100 sends = 1% > 0.1% → -20
    (("pass", "pass", "pass", 0, 100, 0, 1), 80),
    # 1 spam bounce in 1000 sends = 0.1% — not OVER the line
    (("pass", "pass", "pass", 0, 1000, 0, 1), 100),
    # zero sends → rates are 0, never penalized (no data is not bad data)
    (("pass", "pass", "pass", 0, 0, 50, 50), 100),
    # everything wrong at once clamps at 0
    (("missing", "missing", "missing", 2, 100, 10, 10), 0),
]


@pytest.mark.parametrize("inputs,expected", SCORE_TABLE)
def test_health_score_table(inputs, expected):
    spf, dkim, dmarc, listings, sends, hard, spam = inputs
    score, components = health_score(
        spf_status=spf,
        dkim_status=dkim,
        dmarc_status=dmarc,
        blocklist_listings=listings,
        stats=SevenDayStats(sends=sends, hard_bounces=hard, spam_bounces=spam),
    )
    assert score == expected
    # the breakdown always reconciles with the score (pre-clamp)
    assert score == max(0, min(100, 100 - sum(components.values())))
    assert set(components) == {"dns_auth", "blocklist", "bounce_rate", "complaint_rate"}


# ---------------------------------------------------------------- bounce classification


@pytest.mark.parametrize(
    "diagnostic,expected",
    [
        ("550 5.1.1 user unknown", "hard"),
        ("554 message rejected as spam", "spam"),
        ("Connection Blocked by policy", "spam"),  # case-insensitive
        ("550 poor sender REPUTATION", "spam"),
        ("your IP is on a blocklist", "spam"),  # "block" substring
        (None, "hard"),
        ("", "hard"),
    ],
)
def test_classify_bounce(diagnostic, expected):
    assert classify_bounce(diagnostic) == expected


# ---------------------------------------------------------------- domain extraction


def test_domain_extraction():
    assert domain_of("sender@Outbound.Acme.COM") == "outbound.acme.com"
    assert domain_of("weird@@acme.com") == "acme.com"


# ---------------------------------------------------------------- sending IPs


def test_resolve_sending_ips_via_mx(monkeypatch):
    _patch_dns(
        monkeypatch,
        {
            ("acme.com", "MX"): ["10 mx1.acme.com", "20 mx2.acme.com"],
            ("mx1.acme.com", "A"): ["192.0.2.1"],
            ("mx2.acme.com", "A"): ["192.0.2.2", "192.0.2.1"],  # dupe collapses
        },
    )
    assert resolve_sending_ips("acme.com") == ["192.0.2.1", "192.0.2.2"]


def test_resolve_sending_ips_falls_back_to_a_record(monkeypatch):
    _patch_dns(monkeypatch, {("acme.com", "A"): ["198.51.100.7"]})
    assert resolve_sending_ips("acme.com") == ["198.51.100.7"]


# ---------------------------------------------------------------- DNSBL verdicts


ZONES = ["zen.spamhaus.org", "bl.spamcop.net"]


def test_blocklist_listed_and_clear(monkeypatch):
    _patch_dns(
        monkeypatch,
        {
            ("acme.com", "MX"): ["10 mx.acme.com"],
            ("mx.acme.com", "A"): ["192.0.2.9"],
            # reversed-IP query: an A answer means listed
            ("9.2.0.192.zen.spamhaus.org", "A"): ["127.0.0.2"],
            # spamcop: NXDOMAIN (no entry in the table) means clear
        },
    )
    verdicts = check_blocklists("acme.com", ZONES)
    assert verdicts == [
        BlocklistVerdict(zone="zen.spamhaus.org", status="listed", listed_ips=["192.0.2.9"]),
        BlocklistVerdict(zone="bl.spamcop.net", status="clear"),
    ]


def test_blocklist_resolver_error_is_error_not_clear(monkeypatch):
    """A timeout while querying a zone must read 'couldn't check' — the dns_auth
    honesty rule: flaky network never becomes a false verdict either way."""
    _patch_dns(
        monkeypatch,
        {("acme.com", "MX"): ["10 mx.acme.com"], ("mx.acme.com", "A"): ["192.0.2.9"]},
        errors={"9.2.0.192.zen.spamhaus.org"},
    )
    verdicts = check_blocklists("acme.com", ZONES)
    assert verdicts[0].status == "error"
    assert verdicts[1].status == "clear"


def test_blocklist_no_ips_is_error(monkeypatch):
    """No resolvable MX/A means we couldn't check anything — not 'clear'."""
    _patch_dns(monkeypatch, {})
    verdicts = check_blocklists("acme.com", ZONES)
    assert all(v.status == "error" for v in verdicts)


# ---------------------------------------------------------------- domain rate limiter


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = str(value)


def test_acquire_domain_slot_disabled_by_default(monkeypatch):
    """domain_min_interval_s=0 (the shipped default) skips Redis entirely."""
    from craftsman.sender.limiter import acquire_domain_slot

    assert acquire_domain_slot("acme.com", r=None) == 0.0  # r=None would explode if used


def test_acquire_domain_slot_enforces_interval(monkeypatch):
    from craftsman.core.config import get_settings
    from craftsman.sender.limiter import acquire_domain_slot

    monkeypatch.setenv("DOMAIN_MIN_INTERVAL_S", "30")
    get_settings.cache_clear()
    try:
        r = _FakeRedis()
        assert acquire_domain_slot("acme.com", r=r, now=1000.0) == 0.0
        wait = acquire_domain_slot("acme.com", r=r, now=1010.0)
        assert wait == pytest.approx(20.0)
        # a different domain has its own bucket
        assert acquire_domain_slot("other.com", r=r, now=1010.0) == 0.0
        # after the interval passes, clear again
        assert acquire_domain_slot("acme.com", r=r, now=1031.0) == 0.0
    finally:
        get_settings.cache_clear()
