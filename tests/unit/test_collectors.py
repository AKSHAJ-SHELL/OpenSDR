"""Signal collector fixtures (M2.3): diff baseline/change/no-change, RSS parse + dedupe.
Every network call is patched — no real fetch."""

from craftsman.core.config import Settings
from craftsman.core.models import Company, CollectorState, Signal
from craftsman.scoring.collectors import (
    PageDiffCollector,
    RssFundingCollector,
    build_collectors,
    parse_rss,
)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Acme Robotics raises $20M Series B</title>
        <link>https://news.test/acme-20m</link>
        <description>Acme Robotics, acme.com, closed funding.</description></item>
  <item><title>Unrelated company news</title>
        <link>https://news.test/other</link>
        <description>Nothing to see.</description></item>
</channel></rss>"""


def _company(db, domain="acme.com", name="Acme Robotics"):
    c = Company(domain=domain, name=name)
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------- page diff


async def test_page_diff_first_sight_seeds_baseline_no_signal(db, monkeypatch):
    _company(db)
    col = PageDiffCollector("homepage_diff", [""], "tech_stack_change")

    async def fake_fetch(domain):
        return "we build warehouse robots " * 30

    monkeypatch.setattr(col, "_fetch", fake_fetch)
    signals = await col.collect(db)
    assert signals == []  # baseline only
    assert db.scalar(select_state(db, "homepage_diff")) is not None


async def test_page_diff_emits_on_change_not_on_same(db, monkeypatch):
    _company(db)
    col = PageDiffCollector("homepage_diff", [""], "tech_stack_change")
    page = {"text": "original homepage content " * 30}

    async def fake_fetch(domain):
        return page["text"]

    monkeypatch.setattr(col, "_fetch", fake_fetch)
    await col.collect(db)  # baseline

    # unchanged → no signal
    assert await col.collect(db) == []

    # changed → one signal
    page["text"] = "brand new homepage with different stack " * 30
    signals = await col.collect(db)
    assert len(signals) == 1 and signals[0].type == "tech_stack_change"


async def test_page_diff_failure_isolated(db, monkeypatch):
    _company(db, domain="ok.com", name="OK")
    _company(db, domain="boom.com", name="Boom")
    col = PageDiffCollector("homepage_diff", [""], "tech_stack_change")

    async def fake_fetch(domain):
        if domain == "boom.com":
            raise RuntimeError("network go boom")
        return "fine content " * 40

    monkeypatch.setattr(col, "_fetch", fake_fetch)
    # one company raising doesn't sink the sweep; ok.com still gets its baseline
    assert await col.collect(db) == []


# ---------------------------------------------------------------- rss funding


def test_parse_rss_extracts_items():
    items = parse_rss(RSS)
    assert len(items) == 2 and items[0]["link"] == "https://news.test/acme-20m"


def test_parse_rss_malformed_is_empty():
    assert parse_rss("<not xml") == []


async def test_rss_matches_company_and_dedupes(db, monkeypatch):
    _company(db)
    col = RssFundingCollector("https://news.test/feed")

    async def fake_fetch():
        return RSS

    monkeypatch.setattr(col, "_fetch", fake_fetch)
    signals = await col.collect(db)
    assert len(signals) == 1  # only the matching item
    assert signals[0].type == "funding"
    assert signals[0].payload["link"] == "https://news.test/acme-20m"

    # persist it, then re-run → deduped by link, no new signal
    db.add(
        Signal(
            company_id=signals[0].company_id,
            type="funding",
            payload=signals[0].payload,
            source="rss_funding",
        )
    )
    db.flush()
    assert await col.collect(db) == []


# ---------------------------------------------------------------- factory


def test_build_collectors_gating():
    s = Settings(
        signal_collectors="homepage_diff,careers_diff,rss_funding",
        signal_funding_rss_url="https://news.test/feed",
        _env_file=None,
    )
    names = [c.name for c in build_collectors(s)]
    assert names == ["homepage_diff", "careers_diff", "rss_funding"]

    # rss listed but no URL → skipped; unknown name → skipped
    s2 = Settings(signal_collectors="rss_funding,bogus", _env_file=None)
    assert build_collectors(s2) == []

    assert build_collectors(Settings(signal_collectors="", _env_file=None)) == []


def select_state(db, collector):
    from sqlalchemy import select

    return select(CollectorState).where(CollectorState.collector == collector)
