"""Intent-signal collectors (M2.3). Each is optional and independently disableable via
`signal_collectors` config; each is failure-isolated per company (one bad fetch never
sinks the sweep) and reuses the M0.5 SSRF-guarded fetcher. Results come from *your* own
watched sources — no proprietary intent database.

Mechanisms (covering the three roadmap signal categories):
- diff collectors (homepage → tech_stack_change, careers → job_posting): fetch a page,
  fingerprint cleaned text, compare to `collector_state`; first sight seeds a baseline
  (no signal), a changed fingerprint emits one. Coarse "the page changed" signals — not
  semantic extraction (honest labeling).
- RSS funding: fetch a news/RSS feed, match entries to a company by name/domain, emit a
  `funding` signal per new entry (deduped against existing signals by link).
"""

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import Company, Signal
from craftsman.research.fetch import clean_html, fetch_url_body

log = logging.getLogger(__name__)

MAX_COMPANIES_PER_SWEEP = 200  # bound the network per beat tick


@dataclass
class CollectedSignal:
    company_id: object
    type: str
    payload: dict
    source: str


class SignalCollector(Protocol):
    name: str

    async def collect(self, db: Session) -> list[CollectedSignal]: ...


def _companies_with_domain(db: Session) -> list[Company]:
    return list(
        db.scalars(
            select(Company).where(Company.domain.isnot(None), Company.domain != "")
            .limit(MAX_COMPANIES_PER_SWEEP)
        ).all()
    )


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class PageDiffCollector:
    """Fetch the first resolving page among `paths`, fingerprint its cleaned text, and emit
    `signal_type` when the fingerprint changes vs the stored baseline."""

    def __init__(self, name: str, paths: list[str], signal_type: str):
        self.name = name
        self.paths = paths
        self.signal_type = signal_type

    async def _fetch(self, domain: str) -> str | None:
        """First path that returns HTML → cleaned text. Only network seam (tests patch)."""
        for path in self.paths:
            body = await fetch_url_body(f"https://{domain}{path}")
            if body:
                text = clean_html(body)
                if len(text) > 200:  # skip empty shells, matching the research fetcher
                    return text
        return None

    async def collect(self, db: Session) -> list[CollectedSignal]:
        from craftsman.core.models import CollectorState

        out: list[CollectedSignal] = []
        for company in _companies_with_domain(db):
            try:
                text = await self._fetch(company.domain)
                if text is None:
                    continue
                fp = _fingerprint(text)
                state = db.scalar(
                    select(CollectorState).where(
                        CollectorState.company_id == company.id,
                        CollectorState.collector == self.name,
                    )
                )
                if state is None:
                    db.add(CollectorState(company_id=company.id, collector=self.name, fingerprint=fp))
                    db.flush()
                    continue  # first sight → baseline, never a signal
                if state.fingerprint != fp:
                    state.fingerprint = fp
                    from datetime import datetime, timezone

                    state.updated_at = datetime.now(timezone.utc)
                    db.add(state)
                    out.append(
                        CollectedSignal(
                            company_id=company.id,
                            type=self.signal_type,
                            payload={"domain": company.domain},
                            source=self.name,
                        )
                    )
            except Exception as e:  # noqa: BLE001 — one company never sinks the sweep
                log.warning("collector %s failed for %s: %s", self.name, company.domain, e)
        return out


def parse_rss(body: str) -> list[dict]:
    """Minimal RSS 2.0 parse (stdlib, no new dep): title / link / guid / description per
    <item>. Returns [] on malformed XML (never raises)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(tag) or "").strip()  # noqa: E731
        items.append(
            {
                "title": get("title"),
                "link": get("link"),
                "guid": get("guid") or get("link"),
                "description": get("description"),
            }
        )
    return items


class RssFundingCollector:
    """Watch a funding/news RSS feed; emit a `funding` signal when an entry matches a
    company by name or domain. Deduped against existing signals by entry link."""

    name = "rss_funding"

    def __init__(self, url: str):
        self.url = url

    async def _fetch(self) -> str | None:
        """Only network seam (tests patch). SSRF-guarded via fetch_url_body."""
        return await fetch_url_body(self.url)

    def _matches(self, company: Company, hay: str) -> bool:
        name = (company.name or "").strip().lower()
        domain = (company.domain or "").strip().lower()
        return bool((name and len(name) >= 3 and name in hay) or (domain and domain in hay))

    async def collect(self, db: Session) -> list[CollectedSignal]:
        try:
            body = await self._fetch()
        except Exception as e:  # noqa: BLE001
            log.warning("rss_funding fetch failed: %s", e)
            return []
        if not body:
            return []
        entries = parse_rss(body)
        companies = _companies_with_domain(db)
        out: list[CollectedSignal] = []
        for entry in entries:
            hay = f"{entry['title']} {entry['description']}".lower()
            link = entry["link"] or entry["guid"]
            for company in companies:
                if not self._matches(company, hay):
                    continue
                # dedupe: same company + same link already recorded → skip
                exists = db.scalar(
                    select(Signal.id).where(
                        Signal.company_id == company.id,
                        Signal.type == "funding",
                        Signal.payload["link"].astext == link,
                    )
                )
                if exists is not None:
                    continue
                out.append(
                    CollectedSignal(
                        company_id=company.id,
                        type="funding",
                        payload={"link": link, "title": entry["title"]},
                        source=self.name,
                    )
                )
        return out


def build_collectors(settings) -> list[SignalCollector]:
    """Enabled collectors from `signal_collectors` config. Unknown names / a keyless RSS
    collector are skipped (logged). Empty ⇒ no collection."""
    names = [n.strip().lower() for n in settings.signal_collectors.split(",") if n.strip()]
    collectors: list[SignalCollector] = []
    for name in names:
        if name == "homepage_diff":
            collectors.append(PageDiffCollector("homepage_diff", [""], "tech_stack_change"))
        elif name == "careers_diff":
            collectors.append(
                PageDiffCollector("careers_diff", ["/careers", "/jobs", "/about/careers"], "job_posting")
            )
        elif name == "rss_funding" and settings.signal_funding_rss_url:
            collectors.append(RssFundingCollector(settings.signal_funding_rss_url))
        else:
            log.warning("signal collector %r skipped (unknown or not configured)", name)
    return collectors
