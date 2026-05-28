"""Walk data/raw/, tidy each CSV, write one parquet per logical table.

For each data/raw/{survey}/{table_id}/, combines all per-geography CSVs into
one data/clean/{survey}/{table_id}.parquet with a `geography` column added.

Idempotent: skips a table whose parquet is newer than all its source CSVs.

Run from project root:
    uv run python scripts/build_parquet.py
"""

from pathlib import Path

import polars as pl

from cmhc.catalogue import CATALOGUE
from cmhc.config import CLEAN_DIR, RAW_DIR
from cmhc.tidy import tidy

# table_id → breakdown, so we can tell tidy() what the first CSV column means.
_BREAKDOWN_BY_TABLE: dict[str, str] = {t.table_id: t.breakdown for t in CATALOGUE}


def _is_stale(parquet: Path, sources: list[Path]) -> bool:
    if not parquet.exists():
        return True
    parquet_mtime = parquet.stat().st_mtime
    return any(src.stat().st_mtime > parquet_mtime for src in sources)


def build_one_table(survey: str, table_id: str, csv_paths: list[Path]) -> tuple[str, int]:
    """Tidy all per-geo CSVs for one table, concat, write parquet.
    Returns (status, n_rows) where status is 'ok' or 'error: ...'."""
    out_path = CLEAN_DIR / survey / f"{table_id}.parquet"
    if not _is_stale(out_path, csv_paths):
        return "skipped", 0

    breakdown = _BREAKDOWN_BY_TABLE.get(table_id)
    frames: list[pl.DataFrame] = []
    for csv_path in csv_paths:
        geo_name = csv_path.stem.replace("_", " ")
        try:
            df = tidy(csv_path.read_bytes(), breakdown=breakdown)
        except Exception as e:
            print(f"  ! {csv_path}: {type(e).__name__}: {e}")
            continue
        df = df.with_columns(
            pl.lit(survey).alias("survey"),
            pl.lit(table_id).alias("table_id"),
            pl.lit(geo_name).alias("geography"),
        )
        frames.append(df)

    if not frames:
        return "no data", 0

    combined = pl.concat(frames)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.write_parquet(out_path)
    return "ok", len(combined)


def main() -> None:
    if not RAW_DIR.exists():
        print(f"No raw data at {RAW_DIR}")
        return

    # Group CSVs by (survey, table_id)
    by_table: dict[tuple[str, str], list[Path]] = {}
    for csv_path in RAW_DIR.rglob("*.csv"):
        # data/raw/{survey}/{table_id}/{geo}.csv
        parts = csv_path.relative_to(RAW_DIR).parts
        if len(parts) != 3:
            continue
        survey, table_id, _ = parts
        by_table.setdefault((survey, table_id), []).append(csv_path)

    print(f"Found {len(by_table)} logical tables across {sum(len(v) for v in by_table.values())} CSVs")
    counts = {"ok": 0, "skipped": 0, "no data": 0}
    total_rows = 0

    for (survey, table_id), csvs in sorted(by_table.items()):
        status, n = build_one_table(survey, table_id, csvs)
        counts[status] = counts.get(status, 0) + 1
        total_rows += n
        marker = "·" if status == "skipped" else "✓" if status == "ok" else "—"
        print(f"  {marker} {survey}/{table_id} ({len(csvs)} geos, {n} rows): {status}")

    print()
    print(f"Done. {counts} — {total_rows:,} total rows written.")


if __name__ == "__main__":
    main()
