"""Parse each spec'd static table into data/clean/static/{table_id}.parquet.

For every table in cmhc.static.specs.SPECS, resolve its downloaded file from the
catalogue, parse it with the static engine, and write one parquet in the shared
long schema (the same shape + `source` column the HMIP clean/ tree uses). Only
tables that have a parse spec are built — the rest of data/raw/static/ is left
alone until it gets one.

Idempotent: skips a table whose parquet is newer than its source file. Re-parse
after a spec or engine change with `rm -rf data/clean/static/`.

Run from project root:
    uv run python scripts/build_static_parquet.py
"""

from pathlib import Path
from urllib.parse import urlsplit

from cmhc.config import CLEAN_DIR, STATIC_RAW_DIR
from cmhc.static import catalogue, parse
from cmhc.static.specs import SPECS

OUT_DIR = CLEAN_DIR / "static"


def _source_path(table: catalogue.StaticTable) -> Path | None:
    """Where download_static.py wrote this table's file, or None if no asset."""
    if not table.asset_url:
        return None
    filename = urlsplit(table.asset_url).path.rsplit("/", 1)[-1]
    return STATIC_RAW_DIR / table.section / filename


def _is_stale(parquet: Path, source: Path) -> bool:
    return not parquet.exists() or source.stat().st_mtime > parquet.stat().st_mtime


def build_one(slug: str) -> tuple[str, int]:
    """Build one table's parquet. Returns (status, n_rows)."""
    source = _source_path(catalogue.get(slug))
    if source is None or not source.exists():
        return "missing", 0
    out_path = OUT_DIR / f"{slug}.parquet"
    if not _is_stale(out_path, source):
        return "skipped", 0
    df = parse(slug, source)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    return "ok", len(df)


def main() -> None:
    counts = {"ok": 0, "skipped": 0, "missing": 0, "error": 0}
    total_rows = 0
    markers = {"ok": "✓", "skipped": "·", "missing": "—", "error": "✗"}

    print(f"{len(SPECS)} spec'd static tables")
    for slug in sorted(SPECS):
        try:
            status, n = build_one(slug)
        except Exception as e:
            counts["error"] += 1
            print(f"  ✗ {slug}: {type(e).__name__}: {e}")
            continue
        counts[status] += 1
        total_rows += n
        print(f"  {markers[status]} {slug} ({n} rows): {status}")

    print()
    print(f"Done. {counts} — {total_rows:,} total rows written to {OUT_DIR}")


if __name__ == "__main__":
    main()
