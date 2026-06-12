"""Discover and catalogue every static-data-table .xlsx on cmhc-schl.gc.ca.

Walks the four section pages under housing-data/data-tables/, enumerates
leaf pages, scrapes each leaf for asset .xlsx URLs, HEADs each asset for
size + last-modified, and writes the result to data/static_catalogue.json.

Two discovery modes per leaf page:

  - "html"   : the asset URL is present in the server-rendered HTML. A plain
               httpx GET + regex finds it. Covers ~half the pages.
  - "render" : the asset URL is injected into the DOM by client-side JS and is
               absent from the server HTML (e.g. the Equifax-sourced
               mortgage/credit tables). Only a headless browser sees it.

The httpx pass always runs. The render fallback runs only with --render, and
only for pages the httpx pass found zero assets on — so the common case needs
no browser. This is the *static-data-tables* scraper; it is deliberately
independent of the HMIP library (src/cmhc/) so the headless-browser dependency
never touches the HMIP scraper path.

Run from project root:
    uv run python scripts/build_static_catalogue.py              # httpx only
    uv run --group scrape python scripts/build_static_catalogue.py --render
"""

import argparse
import json
import re
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
    asset_info = [_head(a) for a in assets]
    if assets:
        time.sleep(DELAY * len(assets))
    return {
        "page_url": page_url,
        "title": _title(html),
        "assets": asset_info,
        "discovery": "html" if assets else None,
    }


def _render_assets(page_urls: list[str]) -> dict[str, list[str]]:
    """Render each page in a headless browser and pull asset URLs from the DOM.

    For pages whose download links are injected by client-side JS, the server
    HTML carries no asset URL — only the rendered DOM does. We load the page,
    wait for network-idle, and regex the live DOM (page + any iframes) for the
    asset links. Returns {page_url: [asset_url, ...]}.
    """
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    found: dict[str, list[str]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for i, url in enumerate(page_urls, 1):
            try:
                page.goto(url, wait_until="networkidle", timeout=45_000)
            except PWTimeout:
                # networkidle can hang on pages with long-poll widgets; the DOM
                # is usually settled by the 'load' event anyway, so carry on.
                pass
            html = page.content()
            for frame in page.frames:
                try:
                    html += frame.content()
                except Exception:
                    pass
            assets = sorted(set(_ASSET_RE.findall(html)))
            found[url] = assets
            print(f"  [render {i}/{len(page_urls)}] {len(assets)} asset(s) — "
                  f"{url.rsplit('/', 1)[-1]}", flush=True)
            time.sleep(DELAY)
        browser.close()
    return found


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--render", action="store_true",
                   help="render pages the httpx pass found no assets on with a "
                        "headless browser (needs the 'scrape' dep group + "
                        "`playwright install chromium`)")
    p.add_argument("--sections", help="comma-separated subset of sections to scrape "
                   f"(default: all — {', '.join(SECTIONS)})")
    p.add_argument("--limit", type=int,
                   help="cap the number of leaf pages per section (for testing)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    sections = args.sections.split(",") if args.sections else SECTIONS

    tables: list[dict] = []
    for section in sections:
        leaves = _leaf_urls(section)
        if args.limit:
            leaves = leaves[:args.limit]
        print(f"[{section}] {len(leaves)} leaf pages", flush=True)
        for leaf in leaves:
            entry = _scrape_leaf(leaf)
            entry["section"] = section
            tables.append(entry)
            slug = leaf.rsplit("/", 1)[-1]
            print(f"  {slug}: {len(entry['assets'])} asset(s) — {entry['title']}", flush=True)
            time.sleep(DELAY)

    if args.render:
        empties = [t for t in tables if not t["assets"]]
        print(f"\n[render fallback] {len(empties)} page(s) had no assets in HTML; "
              f"rendering with headless browser", flush=True)
        rendered = _render_assets([t["page_url"] for t in empties])
        for t in empties:
            assets = rendered.get(t["page_url"], [])
            if assets:
                t["assets"] = [_head(a) for a in assets]
                t["discovery"] = "render"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"tables": tables}, indent=2))

    n_files = sum(len(t["assets"]) for t in tables)
    n_with_files = sum(1 for t in tables if t["assets"])
    n_render = sum(1 for t in tables if t.get("discovery") == "render")
    print()
    print(f"Wrote {len(tables)} pages ({n_with_files} with downloadable files, "
          f"{n_files} total assets; {n_render} via render) to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        _client.close()
