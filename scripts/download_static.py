"""Download every discovered static-data-table asset into data/raw/static/.

Reads the asset URLs from data/static_catalogue.json (via cmhc.static.catalogue)
and writes each to data/raw/static/{section}/{filename}. Pages with no
discovered asset (the 8 absorption tables) are skipped.

Idempotent: skips a file that already exists unless --force. data/raw/ is
gitignored, so these stay local.

Run from project root:
    uv run python scripts/download_static.py
    uv run python scripts/download_static.py --force   # re-download everything
"""

import argparse
import time
from urllib.parse import urlsplit

import httpx

from cmhc.config import STATIC_RAW_DIR
from cmhc.static import catalogue

DELAY = 0.2


def _filename(asset_url: str) -> str:
    """Basename of the asset URL, query string (?rev=...) stripped."""
    return urlsplit(asset_url).path.rsplit("/", 1)[-1]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true",
                   help="re-download assets even if the file already exists")
    p.add_argument("--limit", type=int, help="cap the number of downloads (for testing)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    tables = [t for t in catalogue.all_tables() if t.asset_url]
    no_asset = sum(1 for t in catalogue.all_tables() if not t.asset_url)
    if args.limit:
        tables = tables[:args.limit]

    print(f"{len(tables)} assets to consider ({no_asset} pages have no asset, skipped)")
    counts = {"downloaded": 0, "skipped": 0, "error": 0}

    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers={"User-Agent": "cmhc-data-portal/0.1 (static download)"}) as client:
        for t in tables:
            dest = STATIC_RAW_DIR / t.section / _filename(t.asset_url)
            if dest.exists() and not args.force:
                counts["skipped"] += 1
                print(f"  · {t.section}/{dest.name} (exists)")
                continue
            try:
                r = client.get(t.asset_url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                counts["error"] += 1
                print(f"  ! {t.table_id}: {type(e).__name__}: {e}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            counts["downloaded"] += 1
            print(f"  ✓ {t.section}/{dest.name} ({len(r.content):,} bytes)")
            time.sleep(DELAY)

    print()
    print(f"Done. {counts}")


if __name__ == "__main__":
    main()
