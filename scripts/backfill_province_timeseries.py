"""Fetch province-level RMS historical series from HMIP and merge them into the mart.

Until 2026-09-02 `validity.is_valid_for_geo` admitted only the "Centres"
breakdown at province level, so the Historical Time Periods tables were never
requested for a province. HMIP serves them (verified live — see
docs/DATA_DISCOVERY.md 2026-09-02), which left the mart holding a single
snapshot period for Ontario and no provincial history at all.

This script closes that gap without a full re-pull of the archive. It:

  1. fetches the requested (survey, series) Historical Time Periods tables at
     the province, writing the raw CSVs into data/raw/ under the usual
     `{survey}/{table_id}/{geo}.csv` layout so a later `build_parquet.py` +
     `build_dmt_rental.py` reproduces exactly these rows;
  2. tidies them with the same `cmhc.tidy` path the pipeline uses;
  3. replaces the province's rows for those table_ids in
     `rental_observations`, then rebuilds `dimension_values`, the materialized
     metric tables and `_meta` from the star.

Metric ids, dimension labels and the materialized-table specs are imported from
`build_dmt_rental` rather than restated, so the two cannot drift apart.

Run from project root:

    uv run python scripts/backfill_province_timeseries.py                # Ontario vacancy rates
    uv run python scripts/backfill_province_timeseries.py --series all   # all 7 Rms metrics
    uv run python scripts/backfill_province_timeseries.py --dry-run      # fetch + report, no write

Idempotent: re-running replaces the same rows rather than duplicating them.

NOTE ON SEASON: RMS runs an October and (2007-2015) an April survey. HMIP
honours only the *first* value of the `season` filter, and the catalogue lists
October first, so these are the October readings — consistent with every other
Rms row in the mart. The April series is a separate gap.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dmt_rental import (  # noqa: E402
    OUT_PATH,
    _add_is_canonical,
    _apply_reliability_sentinel,
    _build_dimension_values,
    _materialize_metric_tables,
    _table_id_to_metric_dimension,
)

from cmhc.bulk import _output_path  # noqa: E402
from cmhc.catalogue import CATALOGUE  # noqa: E402
from cmhc.geographies import PROVINCES, Geography  # noqa: E402
from cmhc.hmip import fetch_table, is_empty_response  # noqa: E402
from cmhc.tidy import tidy  # noqa: E402
from cmhc.validity import is_valid_for_geo  # noqa: E402


# Columns of `rental_observations`, in storage order. `is_canonical` is not set
# per-table on the way in: it is a property of the whole set of rows for a
# geography, so it is recomputed across the geography after the insert.
_OBS_COLUMNS = [
    "metric_id", "geo_id", "period", "dimension", "category", "value",
    "reliability", "is_suppressed", "is_canonical", "source_survey", "table_id",
    "updated_at",
]

_TS_TABLE_IDS = {t.table_id for t in CATALOGUE if t.breakdown == "Historical Time Periods"}


def _select_tables(survey: str, series: str | None) -> list:
    """Historical Time Periods tables for a survey, optionally one series."""
    tables = [
        t for t in CATALOGUE
        if t.survey == survey and t.breakdown == "Historical Time Periods"
        and (series is None or t.series == series)
    ]
    return sorted(tables, key=lambda t: t.table_id)


def _resolve_geo_id(con: duckdb.DuckDBPyConnection, province: str) -> str:
    """The mart's geo_id for a province row. Fails loudly if absent."""
    row = con.execute(
        "SELECT geo_id FROM geographies WHERE geo_level = 'Province' AND geo_name = ?",
        [province],
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"No Province row named {province!r} in the mart's geographies table. "
            "This script patches an existing mart; it does not add geographies."
        )
    return row[0]


def _fetch_one(table, geo: Geography, write_raw: bool) -> pl.DataFrame | None:
    """Fetch + tidy one table at one geography. None when HMIP has no data."""
    raw = fetch_table(table, geo)
    if is_empty_response(raw):
        return None

    if write_raw:
        path = _output_path(table, geo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    df = tidy(raw, breakdown=table.breakdown)
    return df.with_columns(
        pl.lit(table.survey).alias("survey"),
        pl.lit(table.table_id).alias("table_id"),
        pl.lit(geo.name).alias("geography"),
    )


def _to_observations(
    df: pl.DataFrame,
    geo_id: str,
    table_id_map: dict[str, tuple[int, str | None]],
    fetched_at: datetime,
) -> pl.DataFrame:
    """Shape tidied rows into the mart's fact schema.

    `is_canonical` is written FALSE here as a placeholder; the real value is
    computed across the geography's whole row set after the insert, since the
    canonical row for a given cell may be one that already exists in the mart.
    """
    metric_id, dimension = table_id_map[df["table_id"][0]]
    shaped = (
        df.filter(pl.col("period").is_not_null())
        .with_columns(
            pl.lit(metric_id).cast(pl.Int16).alias("metric_id"),
            pl.lit(geo_id).alias("geo_id"),
            pl.lit(dimension).cast(pl.String).alias("dimension"),
            (pl.col("value").is_null() & pl.col("reliability").is_null()).alias("is_suppressed"),
            pl.col("survey").alias("source_survey"),
            pl.lit(fetched_at).alias("updated_at"),
        )
    )
    # Same NULL-reliability disambiguation the builder applies: NULL must mean
    # "suppressed" and nothing else, so unrated live rows get the 'n/a' sentinel.
    shaped = _apply_reliability_sentinel(shaped)
    return shaped.with_columns(pl.lit(False).alias("is_canonical")).select(_OBS_COLUMNS)


def _recompute_canonical(con: duckdb.DuckDBPyConnection, geo_id: str) -> tuple[int, int]:
    """Re-flag `is_canonical` across every row of one geography.

    The flag marks one row per (metric, geo, period, dimension, category) among
    the several table_id paths CMHC publishes the same value through. Inserting a
    time-series table adds a competing path for periods a snapshot table already
    covered, so the flag has to be recomputed — for the whole geography, not just
    the new rows. Scoped by geo_id because geo_id is part of the key, so no other
    geography's flags can change.

    Returns (rows_rewritten, canonical_after).
    """
    current = con.execute(
        "SELECT * FROM rental_observations WHERE geo_id = ?", [geo_id]
    ).pl()
    reflagged = _add_is_canonical(  # noqa: F841 — read by DuckDB replacement scan
        current.drop("is_canonical"), _TS_TABLE_IDS
    ).select(_OBS_COLUMNS)

    con.execute("DELETE FROM rental_observations WHERE geo_id = ?", [geo_id])
    con.execute(
        f"INSERT INTO rental_observations ({', '.join(_OBS_COLUMNS)}) "
        f"SELECT {', '.join(_OBS_COLUMNS)} FROM reflagged"
    )
    return len(reflagged), int(reflagged["is_canonical"].sum())


def _refresh_derived(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Rebuild dimension_values, the materialized metric tables, and _meta."""
    fact = con.execute(
        "SELECT dimension, category FROM rental_observations"
    ).pl()
    dim_vals_df = _build_dimension_values(fact)  # noqa: F841 — read by DuckDB replacement scan
    con.execute("DROP TABLE IF EXISTS dimension_values")
    con.execute("CREATE TABLE dimension_values AS SELECT * FROM dim_vals_df")

    existing = {
        r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE table_name NOT IN "
            "('rental_observations', 'metrics', 'geographies', 'dimension_values', '_meta')"
        ).fetchall()
    }
    for name in existing:
        con.execute(f"DROP TABLE {name}")
    created = _materialize_metric_tables(con)

    n_obs, n_canon, n_supp = con.execute(
        "SELECT count(*), "
        "       sum(CASE WHEN is_canonical  THEN 1 ELSE 0 END), "
        "       sum(CASE WHEN is_suppressed THEN 1 ELSE 0 END) "
        "FROM rental_observations"
    ).fetchone()
    con.execute(
        "UPDATE _meta SET built_at_utc = ?, n_observations = ?, n_canonical = ?, "
        "n_suppressed = ?",
        [datetime.now(tz=timezone.utc), int(n_obs), int(n_canon), int(n_supp)],
    )
    return created


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--province", default="Ontario", help="Province name (default: Ontario)")
    ap.add_argument("--survey", default="Rms", help="Survey code (default: Rms)")
    ap.add_argument(
        "--series", default="Vacancy Rate",
        help="Series to fetch, or 'all' for every series in the survey "
             "(default: 'Vacancy Rate')",
    )
    ap.add_argument("--dry-run", action="store_true", help="Fetch and report; write nothing")
    ap.add_argument("--no-raw", action="store_true", help="Skip writing CSVs into data/raw/")
    args = ap.parse_args()

    geo = PROVINCES.get(args.province)
    if geo is None:
        raise SystemExit(f"Unknown province: {args.province!r}")
    if not OUT_PATH.exists():
        raise SystemExit(f"Mart not found at {OUT_PATH}. Build it first.")

    series = None if args.series == "all" else args.series
    tables = _select_tables(args.survey, series)
    if not tables:
        raise SystemExit(f"No {args.survey} Historical Time Periods tables for series {args.series!r}")

    skipped = [t for t in tables if not is_valid_for_geo(t, geo)]
    if skipped:
        raise SystemExit(
            "validity.is_valid_for_geo rejects these at province level: "
            f"{[t.table_id for t in skipped]}. Fetching them would contradict the "
            "pull filter, so the mart would not match a rebuild from data/raw/. "
            "Fix validity.py first."
        )

    table_id_map, _ = _table_id_to_metric_dimension()
    fetched_at = datetime.now(tz=timezone.utc)

    print(f"{args.survey} / {args.series} / Historical Time Periods at {geo.name}")
    print(f"  {len(tables)} tables to fetch\n")

    frames: list[pl.DataFrame] = []
    for t in tables:
        if t.table_id not in table_id_map:
            print(f"  {t.table_id:8} SKIP   not mapped to a mart metric")
            continue
        df = _fetch_one(t, geo, write_raw=not (args.dry_run or args.no_raw))
        if df is None:
            print(f"  {t.table_id:8} empty  HMIP publishes nothing here")
            continue
        obs = _to_observations(df, "PENDING", table_id_map, fetched_at)
        span = f"{obs['period'].min()} .. {obs['period'].max()}"
        print(f"  {t.table_id:8} ok     {len(obs):>5} rows  {span}  ({t.dimension})")
        frames.append(df)

    if not frames:
        raise SystemExit("Nothing fetched.")

    con = duckdb.connect(str(OUT_PATH), read_only=args.dry_run)
    try:
        geo_id = _resolve_geo_id(con, args.province)
        obs_df = pl.concat(  # noqa: F841 — read by DuckDB replacement scan
            [_to_observations(f, geo_id, table_id_map, fetched_at) for f in frames]
        )
        table_ids = sorted(set(obs_df["table_id"].to_list()))

        before = con.execute(
            "SELECT count(*) FROM rental_observations WHERE geo_id = ? AND table_id IN "
            f"({','.join('?' * len(table_ids))})",
            [geo_id, *table_ids],
        ).fetchone()[0]

        print(f"\n  geo_id {geo_id!r}: {before} existing rows for these table_ids, "
              f"{len(obs_df)} to write")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return

        con.execute("BEGIN TRANSACTION")
        con.execute(
            "DELETE FROM rental_observations WHERE geo_id = ? AND table_id IN "
            f"({','.join('?' * len(table_ids))})",
            [geo_id, *table_ids],
        )
        con.execute(
            f"INSERT INTO rental_observations ({', '.join(_OBS_COLUMNS)}) "
            f"SELECT {', '.join(_OBS_COLUMNS)} FROM obs_df"
        )
        n_geo_rows, n_geo_canon = _recompute_canonical(con, geo_id)
        created = _refresh_derived(con)
        con.execute("COMMIT")

        total = con.execute("SELECT count(*) FROM rental_observations").fetchone()[0]
        print(f"  Re-flagged is_canonical over {n_geo_rows:,} rows at {geo_id!r} "
              f"({n_geo_canon:,} canonical)")
        print(f"  Rebuilt {len(created)} materialized metric tables")
        print(f"\nDone. rental_observations now holds {total:,} rows.")
        print("  NOTE: DuckDB does not reclaim space in place — run "
              "`scripts/compact_mart.py` to shrink the file before committing it.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
