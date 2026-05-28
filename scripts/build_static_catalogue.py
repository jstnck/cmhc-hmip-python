"""Discover and catalogue every static-data-table .xlsx on cmhc-schl.gc.ca.

Walks the four section pages under housing-data/data-tables/, enumerates
leaf pages, scrapes each leaf for asset .xlsx URLs (the actual downloads
aren't in the visible HTML — they're embedded in a Sitecore data island),
HEADs each asset for size + last-modified, and writes the result to
data/static_catalogue.json.

Run from project root:
    uv run python scripts/build_static_catalogue.py
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx


BASE = "https://www.cmhc-schl.gc.ca"
SECTIONS = [
    "household-characteristics",
    "housing-market-data",
    "mortgage-and-debt",
    "rental-market",
]
SECTION_URL = (
    f"{BASE}/professionals/housing-markets-data-and-research/housing-data/data-tables/{{section}}"
)

OUT_PATH = Path("data/static_catalogue.json")
DELAY = 0.2

_client = httpx.Client(timeout=30.0, follow_redirects=True,
                       headers={"User-Agent": "cmhc-data-portal/0.1 (catalogue scraper)"})


def _leaf_urls(section: str) -> list[str]:
    """All leaf data-table page URLs linked from a section landing page."""
    section_url = SECTION_URL.format(section=section)
    html = _client.get(section_url).text
    # A leaf is one path segment under the section — e.g.
    # .../data-tables/rental-market/urban-rental-market-survey-data-vacancy-rates
    pattern = rf'href="(/professionals/housing-markets-data-and-research/housing-data/data-tables/{re.escape(section)}/[^"/?#]+)"'
    paths = set(re.findall(pattern, html))
    return sorted(BASE + p for p in paths)


_TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ASSET_RE = re.compile(r'https://assets\.cmhc-schl\.gc\.ca/[^"\'<> ]+?\.(?:xlsx|xls|csv)', re.IGNORECASE)


def _title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    return re.sub(r"\s+", " ", _TAG_RE.sub("", m.group(1))).strip()


def _head(url: str) -> dict:
    try:
        r = _client.head(url)
    except httpx.HTTPError as e:
        return {"url": url, "status": f"error: {type(e).__name__}"}
    if r.status_code != 200:
        return {"url": url, "status": r.status_code}
    return {
        "url": url,
        "status": 200,
        "size_bytes": int(r.headers["content-length"]) if "content-length" in r.headers else None,
        "last_modified": r.headers.get("last-modified"),
    }


def _scrape_leaf(page_url: str) -> dict:
    html = _client.get(page_url).text
    assets = sorted(set(_ASSET_RE.findall(html)))
    asset_info = []
    for a in assets:
        asset_info.append(_head(a))
        time.sleep(DELAY)
    return {
        "page_url": page_url,
        "title": _title(html),
        "assets": asset_info,
    }


def main() -> None:
    tables: list[dict] = []
    for section in SECTIONS:
        leaves = _leaf_urls(section)
        print(f"[{section}] {len(leaves)} leaf pages", flush=True)
        for leaf in leaves:
            entry = _scrape_leaf(leaf)
            entry["section"] = section
            tables.append(entry)
            slug = leaf.rsplit("/", 1)[-1]
            n = len(entry["assets"])
            print(f"  {slug}: {n} asset(s) — {entry['title']}", flush=True)
            time.sleep(DELAY)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"tables": tables}, indent=2))

    n_files = sum(len(t["assets"]) for t in tables)
    n_with_files = sum(1 for t in tables if t["assets"])
    print()
    print(f"Wrote {len(tables)} pages ({n_with_files} with downloadable files, "
          f"{n_files} total assets) to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        _client.close()
