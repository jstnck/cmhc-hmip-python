"""Build the Ontario rental data mart.

Reads Rms + Srms parquets from data/clean/, filters to Ontario scope, and
writes data/marts/cmhc_rental.duckdb containing:
  - rental_observations (long fact)
  - metrics, geographies, dimension_values (dim lookups)
  - _meta (single-row provenance table)
  - ~15-17 materialized metric tables (denormalized projections)

Run from project root:
    uv run python scripts/build_dmt_rental.py

Idempotent. Overwrites the .duckdb file. No HMIP traffic.

See docs/DATAMART.md for schema and conventions.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl

from cmhc.catalogue import CATALOGUE
from cmhc.config import CLEAN_DIR, PROJECT_ROOT
from cmhc.geographies import normalize_name


OUT_PATH = PROJECT_ROOT / "data" / "marts" / "cmhc_rental.duckdb"

# Map catalogue Breakdown labels to the analyst-facing geo_level vocabulary.
# CMHC's "Centres" is the CMA breakdown; "Census Subdivision" is the CSD level.
_BREAKDOWN_TO_GEO_LEVEL = {
    "Provinces": "Province",
    "Centres": "CMA",
    "Survey Zones": "SurveyZone",
    "Census Subdivision": "CSD",
    "Neighbourhoods": "Neighbourhood",
    "Census Tracts": "CT",
    "Default": "CMA",   # Srms uses Default; in practice it's CMA-level
}

# Map (survey, series) to a clean metric_name + unit + market.
# Order in the list becomes metric_id (stable for reproducibility).
_METRICS = [
    # Rms (Primary)
    ("Rms",  "Vacancy Rate",          "Vacancy Rate",         "Primary",   "%"),
    ("Rms",  "Availability Rate",     "Availability Rate",    "Primary",   "%"),
    ("Rms",  "Average Rent",          "Average Rent",         "Primary",   "$"),
    ("Rms",  "Average Rent Change",   "Average Rent Change",  "Primary",   "%"),
    ("Rms",  "Median Rent",           "Median Rent",          "Primary",   "$"),
    ("Rms",  "Rental Universe",       "Rental Universe",      "Primary",   "units"),
    ("Rms",  "Summary Statistics",    "Summary Statistics",   "Primary",   "mixed"),
    # Srms (Secondary)
    ("Srms", "Condo Vacancy Rate",                     "Condo Vacancy Rate",            "Secondary", "%"),
    ("Srms", "Condo Average Rent",                     "Condo Average Rent",            "Secondary", "$"),
    ("Srms", "Condo Universe",                         "Condo Universe",                "Secondary", "units"),
    ("Srms", "Rental Condo Universe",                  "Rental Condo Universe",         "Secondary", "units"),
    ("Srms", "Percentage Condo used as Rental",        "Percent Condo Used as Rental",  "Secondary", "%"),
    ("Srms", "Other Secondary Rental Universe",        "Other Secondary Rental Universe",   "Secondary", "units"),
    ("Srms", "Other Secondary Rental Average Rent",    "Other Secondary Rental Average Rent","Secondary", "$"),
]


def _portal_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _build_metric_lookup() -> tuple[pl.DataFrame, dict[str, int]]:
    """metrics dim table + (survey, series) -> metric_id map."""
    rows = []
    by_series: dict[tuple[str, str], int] = {}
    for i, (survey, series, metric_name, market, unit) in enumerate(_METRICS, start=1):
        by_series[(survey, series)] = i
        rows.append({
            "metric_id":     i,
            "metric_name":   metric_name,
            "market":        market,
            "source_survey": survey,
            "unit":          unit,
            "description":   f"{metric_name} from CMHC {survey}",
            "source_table_ids": "",  # populated after we scan the catalogue
        })
    metrics_df = pl.DataFrame(rows)

    # Fill source_table_ids by scanning the catalogue
    table_ids_per_metric: dict[int, set[str]] = {i: set() for i in range(1, len(_METRICS) + 1)}
    for t in CATALOGUE:
        mid = by_series.get((t.survey, t.series))
        if mid is not None:
            table_ids_per_metric[mid].add(t.table_id)
    metrics_df = metrics_df.with_columns(
        pl.col("metric_id").map_elements(
            lambda i: ", ".join(sorted(table_ids_per_metric[i])),
            return_dtype=pl.String,
        ).alias("source_table_ids")
    )
    return metrics_df, by_series


def _table_id_to_metric_dimension() -> dict[str, tuple[int, str | None]]:
    """Build {table_id: (metric_id, dimension)} from the catalogue."""
    metrics_df, by_series = _build_metric_lookup()
    out: dict[str, tuple[int, str | None]] = {}
    for t in CATALOGUE:
        mid = by_series.get((t.survey, t.series))
        if mid is None:
            continue
        out[t.table_id] = (mid, t.dimension)
    return out, metrics_df


def _ontario_geography_universe() -> tuple[set[str], set[str], set[str]]:
    """Return (ontario_provinces, ontario_cmas, ontario_csd_labels).

    All labels are normalized via normalize_name so they match the form HMIP
    writes into our parquets. The `None (None)` entry from a NULL-CSDNAME row
    in the lookup CSV is filtered out defensively.
    """
    data_dir = Path(__file__).parent.parent / "src" / "cmhc" / "data"

    ontario_provinces = {"Ontario"}

    cmas = pl.read_csv(data_dir / "cmas.csv")
    on_csd_cma = pl.read_csv(data_dir / "csds_ontario_cma_members.csv")
    on_metcodes = set(on_csd_cma["METCODE"].cast(str).unique().to_list())
    ontario_cmas = {
        normalize_name(n)
        for n in cmas.filter(pl.col("METCODE").cast(str).is_in(list(on_metcodes)))["NAME_EN"].to_list()
    }

    on_csds = pl.read_csv(data_dir / "csds_ontario.csv")
    ontario_csd_labels: set[str] = set()
    for src in (on_csds, on_csd_cma):
        for r in src.iter_rows(named=True):
            if r["CSDNAME"] is None or r["CSDTYPE"] is None:
                continue
            ontario_csd_labels.add(normalize_name(f"{r['CSDNAME']} ({r['CSDTYPE']})"))

    return ontario_provinces, ontario_cmas, ontario_csd_labels


def _load_parquets() -> pl.DataFrame:
    """Read every Rms + Srms parquet into one long frame with updated_at."""
    frames = []
    for survey in ("Rms", "Srms"):
        for f in sorted((CLEAN_DIR / survey).glob("*.parquet")):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            df = pl.read_parquet(f).with_columns(
                pl.lit(mtime).alias("updated_at"),
            )
            frames.append(df)
    return pl.concat(frames, how="vertical_relaxed")


def _normalize_geo(df: pl.DataFrame) -> pl.DataFrame:
    """Resolve geo_name from parquet's geography / sub_geography pair.

    Source parquets carry both `geography` (the geo we queried at) and
    `sub_geography` (the geo within a breakdown). The analyst-facing row's
    geography is whichever is the lowest level: sub_geography if populated,
    geography otherwise.

    Normalization (slash → hyphen) happens at the matching step in
    _assign_geo_level so geo_name in the output stays in HMIP's native form
    (which is already the hyphen form in our parquets).
    """
    return df.with_columns(
        pl.when(pl.col("sub_geography").is_not_null())
          .then(pl.col("sub_geography"))
          .otherwise(pl.col("geography"))
          .alias("geo_name")
    )


def _assign_geo_level(df: pl.DataFrame, on_prov: set[str], on_cma: set[str], on_csd: set[str]) -> pl.DataFrame:
    """Tag each row with geo_level + Ontario province; filter to Ontario only.

    `cma` rollup is NOT set here — it's looked up canonically in
    _build_geographies_dim via CSDUID → METCODE → CMA name, since the parquet
    rows for CSD-level pulls have the CSD itself in `geography`, not its
    parent CMA.
    """
    # The lookup sets are already normalized; compare against normalized geo_name.
    return (
        df.with_columns(
            pl.col("geo_name").str.replace_all("/", "-").alias("_geo_name_norm")
        )
        .with_columns(
            pl.when(pl.col("_geo_name_norm").is_in(list(on_prov)))
              .then(pl.lit("Province"))
              .when(pl.col("_geo_name_norm").is_in(list(on_cma)))
              .then(pl.lit("CMA"))
              .when(pl.col("_geo_name_norm").is_in(list(on_csd)))
              .then(pl.lit("CSD"))
              .otherwise(pl.lit(None, dtype=pl.String))
              .alias("geo_level"),
        )
        .filter(pl.col("geo_level").is_not_null())
        # Canonicalize geo_name to the normalized (hyphen) form so the analyst
        # sees a consistent label across the mart.
        .with_columns(
            pl.col("_geo_name_norm").alias("geo_name"),
            pl.lit("Ontario").alias("province"),
        )
        .drop("_geo_name_norm")
    )


def _attach_metric_id(df: pl.DataFrame, table_id_map: dict[str, tuple[int, str | None]]) -> pl.DataFrame:
    """Look up metric_id + dimension via table_id."""
    metric_ids = []
    dimensions = []
    for tid in df["table_id"].to_list():
        mapping = table_id_map.get(tid)
        if mapping is None:
            metric_ids.append(None)
            dimensions.append(None)
        else:
            mid, dim = mapping
            metric_ids.append(mid)
            dimensions.append(dim)
    return df.with_columns(
        pl.Series("metric_id", metric_ids, dtype=pl.Int16),
        pl.Series("dimension", dimensions, dtype=pl.String),
    ).filter(pl.col("metric_id").is_not_null())


def _canonical_geo_lookups() -> dict:
    """Build the full StatCan-sourced cross-walk used to populate the geo dim.

    Returns a dict of lookups:
      cma_name_by_metcode  : METCODE -> CMA name (HMIP/StatCan form, normalized)
      cma_uid_by_name      : CMA name -> CMA_UID
      csd_to_metcode       : normalized CSD label -> METCODE
      csd_to_uid           : normalized CSD label -> CSDUID
      csd_to_name_type     : normalized CSD label -> (CSDNAME, CSDTYPE) raw
      all_cma_member_csds  : set of normalized CSD labels in the CMA-member subset

    All CSD/CMA name keys are normalized via normalize_name (slash -> hyphen)
    to match HMIP's display form.
    """
    data_dir = Path(__file__).parent.parent / "src" / "cmhc" / "data"
    cmas = pl.read_csv(data_dir / "cmas.csv")
    on_csd_cma = pl.read_csv(data_dir / "csds_ontario_cma_members.csv")

    cma_name_by_metcode = {
        str(r["METCODE"]): normalize_name(r["NAME_EN"])
        for r in cmas.iter_rows(named=True)
    }
    cma_uid_by_name = {
        normalize_name(r["NAME_EN"]): str(r["CMA_UID"])
        for r in cmas.iter_rows(named=True)
    }

    csd_to_metcode = {}
    csd_to_uid = {}
    csd_to_name_type = {}
    all_cma_member_csds: set[str] = set()
    for r in on_csd_cma.iter_rows(named=True):
        if r["CSDNAME"] is None or r["CSDTYPE"] is None:
            continue
        label = normalize_name(f"{r['CSDNAME']} ({r['CSDTYPE']})")
        csd_to_metcode[label] = str(r["METCODE"])
        csd_to_uid[label] = str(r["CSDUID"])
        csd_to_name_type[label] = (r["CSDNAME"], r["CSDTYPE"])
        all_cma_member_csds.add(label)

    return {
        "cma_name_by_metcode": cma_name_by_metcode,
        "cma_uid_by_name":     cma_uid_by_name,
        "csd_to_metcode":      csd_to_metcode,
        "csd_to_uid":          csd_to_uid,
        "csd_to_name_type":    csd_to_name_type,
        "all_cma_member_csds": all_cma_member_csds,
    }


def _build_geographies_dim(
    fact: pl.DataFrame,
    lookups: dict,
    add_placeholders: bool = True,
) -> tuple[pl.DataFrame, list[str]]:
    """Build the geographies dim.

    Three population paths:
      - Province / CMA / CSD rows that appear in the fact: emitted directly.
      - CMA-member Ontario CSDs absent from the fact: optionally added as
        placeholders (geo_id + canonical IDs + parent CMA filled in, but no
        rental_observations rows reference them). This is honest about
        CMHC's non-publication for small CSDs.

    Returns (geos_df, placeholder_labels).
    """
    cma_uid_by_name     = lookups["cma_uid_by_name"]
    cma_name_by_metcode = lookups["cma_name_by_metcode"]
    csd_to_metcode      = lookups["csd_to_metcode"]
    csd_to_uid          = lookups["csd_to_uid"]
    all_cma_member      = lookups["all_cma_member_csds"]

    def _cma_for_csd(label: str) -> str | None:
        mc = csd_to_metcode.get(label)
        return cma_name_by_metcode.get(mc) if mc else None

    def _csduid(level: str, name: str) -> str | None:
        return csd_to_uid.get(name) if level == "CSD" else None

    def _cmauid(level: str, name: str, parent_cma: str | None) -> str | None:
        if level == "CMA":
            return cma_uid_by_name.get(name)
        if level == "CSD" and parent_cma is not None:
            return cma_uid_by_name.get(parent_cma)
        return None

    def _geo_id(level: str, name: str, csd: str | None, cma_uid: str | None) -> str:
        if level == "Province": return "ON"
        if level == "CMA":      return f"CMA:{cma_uid}" if cma_uid else f"CMA:{name}"
        if level == "CSD":      return f"CSD:{csd}" if csd else f"CSD:{name}"
        return name

    # Rows present in the fact
    fact_geos = (
        fact.select(["geo_name", "geo_level", "province"])
            .unique()
            .sort(["geo_level", "geo_name"])
    )

    rows = []
    for r in fact_geos.iter_rows(named=True):
        level = r["geo_level"]
        name  = r["geo_name"]
        cma   = name if level == "CMA" else (_cma_for_csd(name) if level == "CSD" else None)
        csd   = _csduid(level, name)
        cmau  = _cmauid(level, name, cma)
        rows.append({
            "geo_id":      _geo_id(level, name, csd, cmau),
            "geo_name":    name,
            "geo_level":   level,
            "province":    r["province"],
            "cma":         cma,
            "csduid":      csd,
            "cma_uid":     cmau,
            "has_data":    True,
        })

    placeholder_labels: list[str] = []
    if add_placeholders:
        in_fact = set(fact_geos.filter(pl.col("geo_level") == "CSD")["geo_name"].to_list())
        missing = sorted(all_cma_member - in_fact)
        for label in missing:
            cma  = _cma_for_csd(label)
            csd  = _csduid("CSD", label)
            cmau = _cmauid("CSD", label, cma)
            rows.append({
                "geo_id":    _geo_id("CSD", label, csd, cmau),
                "geo_name":  label,
                "geo_level": "CSD",
                "province":  "Ontario",
                "cma":       cma,
                "csduid":    csd,
                "cma_uid":   cmau,
                "has_data":  False,
            })
            placeholder_labels.append(label)

    geos_df = pl.DataFrame(rows).sort(["geo_level", "geo_name"])
    return geos_df, placeholder_labels


def _attach_geo_id(fact: pl.DataFrame, geos: pl.DataFrame) -> pl.DataFrame:
    return fact.join(
        geos.select(["geo_name", "geo_level", "geo_id"]),
        on=["geo_name", "geo_level"], how="left",
    )


def _build_dimension_values(fact: pl.DataFrame) -> pl.DataFrame:
    return (
        fact.filter(pl.col("dimension").is_not_null())
            .select(["dimension", "category"])
            .unique()
            .sort(["dimension", "category"])
            .with_row_index("sort_order", offset=1)
            .with_columns(pl.col("sort_order").cast(pl.Int16))
            .select(["dimension", "category", "sort_order"])
    )


# Materialized metric table specs: (table_name, metric_name, dimension, value_col_rename)
# `dimension` of None means the table's `category` column is preserved as-is.
_METRIC_TABLES = [
    # Rms
    ("vacancy_rate_by_bedroom",                  "Vacancy Rate",       "Bedroom Type",         "bedroom_type",         "vacancy_pct"),
    ("vacancy_rate_by_year_of_construction",     "Vacancy Rate",       "Year of Construction", "year_of_construction", "vacancy_pct"),
    ("vacancy_rate_by_structure_size",           "Vacancy Rate",       "Structure Size",       "structure_size",       "vacancy_pct"),
    ("availability_rate_by_bedroom",             "Availability Rate",  "Bedroom Type",         "bedroom_type",         "availability_pct"),
    ("availability_rate_by_year_of_construction","Availability Rate",  "Year of Construction", "year_of_construction", "availability_pct"),
    ("availability_rate_by_structure_size",      "Availability Rate",  "Structure Size",       "structure_size",       "availability_pct"),
    ("average_rent_by_bedroom",                  "Average Rent",       "Bedroom Type",         "bedroom_type",         "avg_rent_dollars"),
    ("average_rent_by_year_of_construction",     "Average Rent",       "Year of Construction", "year_of_construction", "avg_rent_dollars"),
    ("average_rent_by_structure_size",           "Average Rent",       "Structure Size",       "structure_size",       "avg_rent_dollars"),
    ("average_rent_change_by_bedroom",           "Average Rent Change","Bedroom Type",         "bedroom_type",         "rent_change_pct"),
    ("median_rent_by_bedroom",                   "Median Rent",        "Bedroom Type",         "bedroom_type",         "median_rent_dollars"),
    ("median_rent_by_year_of_construction",      "Median Rent",        "Year of Construction", "year_of_construction", "median_rent_dollars"),
    ("median_rent_by_structure_size",            "Median Rent",        "Structure Size",       "structure_size",       "median_rent_dollars"),
    ("rental_universe_by_bedroom",               "Rental Universe",    "Bedroom Type",         "bedroom_type",         "n_units"),
    ("rental_universe_by_year_of_construction",  "Rental Universe",    "Year of Construction", "year_of_construction", "n_units"),
    ("rental_universe_by_structure_size",        "Rental Universe",    "Structure Size",       "structure_size",       "n_units"),
    ("vacancy_rate_by_rent_range",               "Vacancy Rate",       "Rent Ranges",          "rent_range",           "vacancy_pct"),
    ("vacancy_rate_by_rent_quartile",            "Vacancy Rate",       "Rent Quartiles",       "rent_quartile",        "vacancy_pct"),
    # Srms
    ("condo_vacancy_rate_by_structure_size",     "Condo Vacancy Rate",  "Structure Size", "structure_size", "vacancy_pct"),
    ("condo_average_rent_by_bedroom",            "Condo Average Rent",  "Bedroom Type",   "bedroom_type",   "avg_rent_dollars"),
    ("condo_universe_by_structure_size",         "Condo Universe",      "Structure Size", "structure_size", "n_units"),
    ("rental_condo_universe_by_structure_size",  "Rental Condo Universe","Structure Size","structure_size", "n_units"),
    ("percent_condo_used_as_rental_by_structure_size","Percent Condo Used as Rental","Structure Size","structure_size","pct_rental"),
    ("other_secondary_rental_universe_by_dwelling_type",     "Other Secondary Rental Universe",    "Dwelling Type","dwelling_type","n_units"),
    ("other_secondary_rental_average_rent_by_dwelling_type", "Other Secondary Rental Average Rent","Dwelling Type","dwelling_type","avg_rent_dollars"),
]


def _materialize_metric_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Create the denormalized metric tables; return the names actually built."""
    created = []
    for table_name, metric_name, dimension, dim_col, value_col in _METRIC_TABLES:
        sql = f"""
        CREATE TABLE {table_name} AS
        SELECT
            g.geo_level,
            g.geo_name,
            g.province,
            g.cma,
            o.period,
            CAST(extract(year FROM o.period) AS SMALLINT) AS period_year,
            o.category   AS {dim_col},
            o.value      AS {value_col},
            o.reliability,
            o.is_suppressed,
            o.source_survey,
            o.table_id,
            o.updated_at
        FROM rental_observations o
        JOIN metrics      m ON o.metric_id = m.metric_id
        JOIN geographies  g ON o.geo_id    = g.geo_id
        WHERE m.metric_name = '{metric_name}'
          AND o.dimension   = '{dimension}'
        """
        con.execute(sql)
        n = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        if n == 0:
            con.execute(f"DROP TABLE {table_name}")
            continue
        created.append(table_name)
    return created


def main() -> None:
    print(f"Building rental data mart -> {OUT_PATH}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. Catalogue lookup + metrics dim
    table_id_map, metrics_df = _table_id_to_metric_dimension()
    print(f"  Catalogue: {len(_METRICS)} metrics, {len(table_id_map)} table_ids mapped")

    # 2. Ontario geography universe
    on_prov, on_cma, on_csd = _ontario_geography_universe()
    print(f"  Ontario universe: 1 province, {len(on_cma)} CMAs, {len(on_csd)} CSD labels")

    # 3. Load parquets + tag updated_at
    raw = _load_parquets()
    print(f"  Loaded parquets: {len(raw):,} rows")

    # 4. Normalize geo + filter to Ontario
    fact = _normalize_geo(raw)
    fact = _assign_geo_level(fact, on_prov, on_cma, on_csd)
    print(f"  After Ontario filter: {len(fact):,} rows")

    # 5. Attach metric_id (drops rows we can't map)
    fact = _attach_metric_id(fact, table_id_map)
    print(f"  After metric mapping: {len(fact):,} rows")

    # 6. Compute is_suppressed
    fact = fact.with_columns(
        (pl.col("value").is_null() & pl.col("reliability").is_null()).alias("is_suppressed")
    )

    # 7. Build geographies dim (with placeholders for CMA-member CSDs absent
    # from the fact) + attach geo_id to the fact
    lookups = _canonical_geo_lookups()
    geos_df, placeholders = _build_geographies_dim(fact, lookups, add_placeholders=True)
    fact = _attach_geo_id(fact, geos_df)
    n_csd_with = int(((geos_df["geo_level"] == "CSD") & geos_df["has_data"]).sum())
    n_csd_without = int(((geos_df["geo_level"] == "CSD") & ~geos_df["has_data"]).sum())
    n_cma = int((geos_df["geo_level"] == "CMA").sum())
    print(f"  Geographies: {len(geos_df)} unique "
          f"(1 province, {n_cma} CMAs, {n_csd_with} CSDs with data + "
          f"{n_csd_without} CSDs placeholder-only)")

    # 8. Build dimension_values dim
    dim_vals_df = _build_dimension_values(fact)
    print(f"  Dimension values: {len(dim_vals_df)}")

    # 9. Shape the fact for storage
    obs_df = fact.select([
        pl.col("metric_id"),
        pl.col("geo_id"),
        pl.col("period"),
        pl.col("dimension"),
        pl.col("category"),
        pl.col("value"),
        pl.col("reliability"),
        pl.col("is_suppressed"),
        pl.col("survey").alias("source_survey"),
        pl.col("table_id"),
        pl.col("updated_at"),
    ])

    # 10. Build _meta. (Rename frames to plain names so the DuckDB FROM-by-locals
    # mechanism picks them up.)
    geos_df = geos_df
    dim_vals_df = dim_vals_df
    source_newest = max(
        (f.stat().st_mtime for f in (CLEAN_DIR).rglob("*.parquet")),
        default=0,
    )
    coverage = (
        f"Ontario: 1 province + {n_cma} CMAs + "
        f"{n_csd_with} CSDs with data + {n_csd_without} CSDs with no CMHC "
        f"publication (placeholder rows in `geographies`, no rental_observations). "
        f"No Census Tracts (not yet pulled; ~2,382 Ontario CTs would unlock "
        f"neighbourhood-level rental — see docs/PROGRESS.md)."
    )
    meta_df = pl.DataFrame([{
        "built_at_utc":          datetime.now(tz=timezone.utc),
        "source_parquet_newest": datetime.fromtimestamp(source_newest, tz=timezone.utc),
        "portal_commit":         _portal_commit(),
        "n_observations":        len(obs_df),
        "n_suppressed":          int(obs_df["is_suppressed"].sum()),
        "n_csd_with_data":       n_csd_with,
        "n_csd_no_data":         n_csd_without,
        "n_cma":                 n_cma,
        "coverage_summary":      coverage,
    }])

    # 11. Write to DuckDB
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    con = duckdb.connect(str(OUT_PATH))

    # DuckDB reads polars DataFrames directly via the in-scope variable name.
    con.execute("CREATE TABLE metrics             AS SELECT * FROM metrics_df")
    con.execute("CREATE TABLE geographies         AS SELECT * FROM geos_df")
    con.execute("CREATE TABLE dimension_values    AS SELECT * FROM dim_vals_df")
    con.execute("CREATE TABLE rental_observations AS SELECT * FROM obs_df")
    con.execute("CREATE TABLE _meta               AS SELECT * FROM meta_df")
    print(f"  Star core written: {len(obs_df):,} observations")

    # 12. Materialize metric tables
    created = _materialize_metric_tables(con)
    print(f"  Materialized metric tables: {len(created)}")
    for t in created:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t:50s} {n:>8,} rows")

    con.close()

    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"\nDone. {OUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
