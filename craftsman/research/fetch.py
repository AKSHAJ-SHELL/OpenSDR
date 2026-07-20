"""Fetch and clean company web pages. httpx + selectolax; ~6k-token budget."""

import asyncio
import re

import httpx
from selectolax.parser import HTMLParser

USER_AGENT = "Mozilla/5.0 (compatible; CraftsmanResearch/0.1)"
MAX_CHARS = 24_000  # ~6k tokens
PATHS = ["", "/about", "/about-us", "/company"]

_STRIP_TAGS = ["script", "style", "nav", "footer", "header", "svg", "noscript", "form", "iframe"]


def clean_html(html: str) -> str:
    tree = HTMLParser(html)
    for tag in _STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    text = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


async def fetch_company_text(domain: str) -> dict[str, str]:
    """Return {url: cleaned_text} for the homepage + about pages that resolve."""
    results: dict[str, str] = {}
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=15,
    ) as client:

        async def _get(path: str) -> None:
            url = f"https://{domain}{path}"
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                    text = clean_html(resp.text)
                    if len(text) > 200:  # skip empty shells
                        results[url] = text
            except httpx.HTTPError:
                pass

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
