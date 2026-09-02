"""Rewrite a DuckDB mart into a fresh file to reclaim dead space.

DuckDB does not return freed pages to the OS in place. Any script that mutates
a mart — `backfill_province_timeseries.py` drops and recreates all 25
materialized metric tables — leaves the old pages in the file. One backfill run
grew `cmhc_rental.duckdb` from 40 MB to 63 MB with 884 rows added. Since the
mart is tracked in git, that bloat is committed and pushed as a new blob every
time.

`COPY FROM DATABASE` writes every table into a new database with no dead pages;
the result replaces the original. Read-only on failure: the new file is built
alongside and only swapped in once it is complete and verified to carry the same
table and row counts.

    uv run python scripts/compact_mart.py
    uv run python scripts/compact_mart.py --path data/marts/other.duckdb
"""

import argparse
from pathlib import Path

import duckdb

from cmhc.config import PROJECT_ROOT


DEFAULT_PATH = PROJECT_ROOT / "data" / "marts" / "cmhc_rental.duckdb"


def _table_counts(con: duckdb.DuckDBPyConnection, alias: str) -> dict[str, int]:
    names = [
        r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = ? "
            "ORDER BY table_name", [alias],
        ).fetchall()
    ]
    return {
        n: con.execute(f'SELECT count(*) FROM {alias}."{n}"').fetchone()[0]
        for n in names
    }


def compact(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"No mart at {path}")

    before = path.stat().st_size
    tmp = path.with_suffix(".compact.duckdb")
    tmp.unlink(missing_ok=True)

    con = duckdb.connect(str(path))
    try:
        source = _table_counts(con, "cmhc_rental")
        con.execute(f"ATTACH '{tmp}' AS compacted")
        con.execute("COPY FROM DATABASE cmhc_rental TO compacted")
        target = _table_counts(con, "compacted")
        con.execute("DETACH compacted")
    finally:
        con.close()

    if source != target:
        differing = {
            k: (source.get(k), target.get(k))
            for k in set(source) | set(target)
            if source.get(k) != target.get(k)
        }
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Copy did not round-trip; original left untouched: {differing}")

    path.unlink()
    tmp.rename(path)
    after = path.stat().st_size
    print(f"{path.name}: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB "
          f"({(before - after) / 1e6:.1f} MB reclaimed, "
          f"{len(target)} tables, {sum(target.values()):,} rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Mart to compact")
    compact(ap.parse_args().path)


if __name__ == "__main__":
    main()
