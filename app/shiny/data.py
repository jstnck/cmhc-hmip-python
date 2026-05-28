"""Shared data loaders for the Shiny app.

Reads parquet + geojson off disk on first call and caches the result. Modules
in `app/` should call these instead of opening files themselves so the loaders
are the one place that knows the on-disk layout.
"""

import json
import math
from functools import lru_cache

import polars as pl

from cmhc.config import CLEAN_DIR
from cmhc.geographies import CMAS, CSDS_ONTARIO_CMA


BEDROOM_TYPES = ["Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom +", "Total"]


def _scrub_geojson_nans(gj: dict) -> dict:
    """Drop NaN-valued properties from every feature.

    StatCan boundary files leave some property columns empty (e.g. DGUIDP is
    only populated on CSD/CT, not CMA). Geopandas reads empties as NaN, which
    survives the topojson round-trip into the GeoJSON file as literal `NaN`
    tokens. Python's json.loads happily accepts those, but downstream JSON
    encoders (Plotly's shinywidgets path uses allow_nan=False) refuse them.
    Strip the offending keys on the way in.
    """
    for feat in gj.get("features", []):
        props = feat.get("properties", {})
        for k in [k for k, v in props.items() if isinstance(v, float) and math.isnan(v)]:
            del props[k]
    return gj


@lru_cache(maxsize=1)
def csd_name_to_uid() -> dict[str, str]:
    """Lookup so we can join rent parquet (keyed by 'Fort Erie (T)') to
    boundary geojson (keyed by CSDUID like '3526032').

    Normalizes '/' → '-' on the lookup side: `bulk._safe_name` turns
    'Guelph/Eramosa' into 'Guelph-Eramosa' when writing the raw CSV, and
    `build_parquet` can't reverse it from the filename alone. So both forms
    have to land on the same UID.
    """
    out: dict[str, str] = {}
    for g in CSDS_ONTARIO_CMA.values():
        out[g.name] = g.geography_id
        if "/" in g.name:
            out[g.name.replace("/", "-")] = g.geography_id
    return out


@lru_cache(maxsize=1)
def csd_rent_latest() -> pl.DataFrame:
    """CSD-level avg rent by bedroom, one row per (CSDUID, bedroom) — the
    most recent published period per CSD (HMIP publishes different CSDs on
    different schedules, so this isn't a single date across the board)."""
    df = pl.read_parquet(CLEAN_DIR / "Rms" / "2.1.11.4.parquet")
    name_to_uid = csd_name_to_uid()
    df = df.with_columns(
        pl.col("geography").replace_strict(name_to_uid, default=None).alias("csduid")
    )
    df = df.filter(pl.col("csduid").is_not_null())
    # One value per (CSDUID, bedroom): the most recent period
    return (
        df.sort("period", descending=True)
        .group_by(["csduid", "category"])
        .agg(
            pl.col("value").first(),
            pl.col("reliability").first(),
            pl.col("period").first(),
            pl.col("geography").first().alias("csd_name"),
        )
    )


@lru_cache(maxsize=1)
def csd_boundaries() -> dict:
    """Ontario CSD polygons in GeoJSON format. ~577 features keyed by CSDUID."""
    with open(CLEAN_DIR / "boundaries_csd_ontario.geojson") as f:
        return _scrub_geojson_nans(json.load(f))


@lru_cache(maxsize=1)
def cma_vacancy_timeseries() -> pl.DataFrame:
    """RMS vacancy rate by bedroom type, time series, per Ontario CMA (34 geos,
    1990–present, October each year)."""
    return pl.read_parquet(CLEAN_DIR / "Rms" / "2.2.1.parquet")


@lru_cache(maxsize=1)
def province_rent_bands() -> pl.DataFrame:
    """Vacancy rate by rent band, snapshot at province level. One period
    (the latest CMHC publication, typically October of the prior survey year)."""
    return pl.read_parquet(CLEAN_DIR / "Rms" / "2.1.4.1.parquet")


# --- CMA-level rent (national, 'Centres' breakdown queried at province scope) ---

@lru_cache(maxsize=1)
def cma_name_to_uid() -> dict[str, str]:
    """CMA name → 5-digit CMAPUID for joining rent data to the boundary file."""
    return {g.name: g.cma_uid for g in CMAS.values() if g.cma_uid}


@lru_cache(maxsize=1)
def cma_rent_latest() -> pl.DataFrame:
    """CMA-level avg rent by bedroom, most-recent published period per CMA.

    Source: 2.1.11.2 (Avg Rent by Bedroom × Centres breakdown), pulled at each
    province's scope. The 'sub_geography' column carries CMA names; we join
    to CMAS to get the CMAPUID (matches the boundary file's join key).

    Includes only the 151 CMAs that map onto our boundary file. The remaining
    ~61 'Centres' values in the source data are smaller Census Agglomerations
    that don't have CMA boundaries — they're surveyed by CMHC but not mapped
    here.
    """
    df = pl.read_parquet(CLEAN_DIR / "Rms" / "2.1.11.2.parquet")
    name_to_uid = cma_name_to_uid()
    df = df.with_columns(
        pl.col("sub_geography").replace_strict(name_to_uid, default=None).alias("cma_uid")
    ).filter(pl.col("cma_uid").is_not_null())
    return (
        df.sort("period", descending=True)
        .group_by(["cma_uid", "category"])
        .agg(
            pl.col("value").first(),
            pl.col("reliability").first(),
            pl.col("period").first(),
            pl.col("sub_geography").first().alias("cma_name"),
        )
    )


@lru_cache(maxsize=1)
def cma_boundaries() -> dict:
    """Ontario CMA polygons (43 features, CMAPUID join key).

    The on-disk file is national (156 features); we filter to PRUID='35' on
    load to keep the map scoped to Ontario.
    """
    with open(CLEAN_DIR / "boundaries_cma_canada.geojson") as f:
        gj = _scrub_geojson_nans(json.load(f))
    gj["features"] = [f for f in gj["features"] if f["properties"].get("PRUID") == "35"]
    return gj


def cma_names() -> list[str]:
    return sorted(cma_vacancy_timeseries()["geography"].unique().to_list())


def province_names() -> list[str]:
    return sorted(province_rent_bands()["sub_geography"].drop_nulls().unique().to_list())
