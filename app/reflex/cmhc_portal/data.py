"""Data loaders for the Reflex app.

Duplicated from app/shiny/data.py — the two apps are intentionally independent
so divergence in framework needs doesn't force the other to change.

Reads parquet + geojson off disk on first call and caches the result.
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

    StatCan boundary files leave some property columns empty; geopandas reads
    them as NaN which Plotly's strict JSON encoder rejects.
    """
    for feat in gj.get("features", []):
        props = feat.get("properties", {})
        for k in [k for k, v in props.items() if isinstance(v, float) and math.isnan(v)]:
            del props[k]
    return gj


@lru_cache(maxsize=1)
def csd_name_to_uid() -> dict[str, str]:
    """Lookup so we can join rent parquet (keyed by name) to boundary geojson
    (keyed by CSDUID). Normalizes '/' → '-' since `bulk._safe_name` mangles
    names on the way to disk."""
    out: dict[str, str] = {}
    for g in CSDS_ONTARIO_CMA.values():
        out[g.name] = g.geography_id
        if "/" in g.name:
            out[g.name.replace("/", "-")] = g.geography_id
    return out


@lru_cache(maxsize=1)
def csd_rent_latest() -> pl.DataFrame:
    """One row per (CSDUID, bedroom), most recent published period per CSD."""
    df = pl.read_parquet(CLEAN_DIR / "Rms" / "2.1.11.4.parquet")
    name_to_uid = csd_name_to_uid()
    df = df.with_columns(
        pl.col("geography").replace_strict(name_to_uid, default=None).alias("csduid")
    ).filter(pl.col("csduid").is_not_null())
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
    """Ontario CSD polygons, keyed by CSDUID."""
    with open(CLEAN_DIR / "boundaries_csd_ontario.geojson") as f:
        return _scrub_geojson_nans(json.load(f))


@lru_cache(maxsize=1)
def cma_vacancy_timeseries() -> pl.DataFrame:
    """RMS vacancy rate by bedroom type, time series, per Ontario CMA."""
    return pl.read_parquet(CLEAN_DIR / "Rms" / "2.2.1.parquet")


@lru_cache(maxsize=1)
def province_rent_bands() -> pl.DataFrame:
    """Vacancy rate by rent band, snapshot at province level."""
    return pl.read_parquet(CLEAN_DIR / "Rms" / "2.1.4.1.parquet")


@lru_cache(maxsize=1)
def cma_name_to_uid() -> dict[str, str]:
    return {g.name: g.cma_uid for g in CMAS.values() if g.cma_uid}


@lru_cache(maxsize=1)
def cma_rent_latest() -> pl.DataFrame:
    """CMA-level avg rent by bedroom, most-recent published period per CMA.
    Joins on CMA name → CMAPUID."""
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
    """Ontario CMA polygons (filtered from a national source file)."""
    with open(CLEAN_DIR / "boundaries_cma_canada.geojson") as f:
        gj = _scrub_geojson_nans(json.load(f))
    gj["features"] = [f for f in gj["features"] if f["properties"].get("PRUID") == "35"]
    return gj


def cma_names() -> list[str]:
    return sorted(cma_vacancy_timeseries()["geography"].unique().to_list())


def province_names() -> list[str]:
    return sorted(province_rent_bands()["sub_geography"].drop_nulls().unique().to_list())
