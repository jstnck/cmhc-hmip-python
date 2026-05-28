"""Build simplified GeoJSON boundary files for Canadian geographies.

Downloads Statistics Canada 2021 Cartographic Boundary Files (CBF) — one per
geography level — and writes simplified GeoJSON to data/clean/. CSD and CT
layers are filtered to Ontario only (saves ~20× on size); CMA layer is kept
national because CMHC RMS publishes national CMA data.

Reprojects to WGS84 and runs topology-preserving simplification via the
`topojson` package (preserves shared edges between neighbouring polygons).

Idempotent — re-running skips downloads that are already on disk. Pass
--force to redownload, --no-simplify to skip simplification (for debugging).

    uv run python scripts/build_boundaries.py
    uv run python scripts/build_boundaries.py --only cma
"""

import argparse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import topojson as tp

from cmhc.config import CLEAN_DIR, RAW_DIR


BOUNDARY_BASE = "https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/files-fichiers"
ONTARIO_PRUID = "35"
BOUNDARIES_DIR = RAW_DIR / "boundaries"


@dataclass(frozen=True)
class BoundarySpec:
    label: str           # "csd" / "ct" / "cma"
    zip_name: str        # "lcsd000a21a_e.zip"
    key_column: str      # "CSDUID" / "CTUID" / "CMAPUID"
    output_name: str     # "boundaries_csd_ontario.geojson"
    simplify_epsilon: float  # in degrees; ~100 m at ON latitudes ≈ 0.001
    province_filter: str | None = None  # PRUID to filter to, or None for national


SPECS = [
    BoundarySpec(
        label="csd",
        zip_name="lcsd000a21a_e.zip",
        key_column="CSDUID",
        output_name="boundaries_csd_ontario.geojson",
        simplify_epsilon=0.001,
        province_filter=ONTARIO_PRUID,
    ),
    BoundarySpec(
        label="ct",
        zip_name="lct_000a21a_e.zip",
        key_column="CTUID",
        output_name="boundaries_ct_ontario.geojson",
        simplify_epsilon=0.0005,
        province_filter=ONTARIO_PRUID,
    ),
    BoundarySpec(
        label="cma",
        # StatCan CMA cartographic boundary file. Smaller than CSD (~150 features).
        zip_name="lcma000a21a_e.zip",
        key_column="CMAPUID",  # full 5-digit StatCan CMA UID (e.g. '35535' Toronto)
        output_name="boundaries_cma_canada.geojson",
        simplify_epsilon=0.005,  # CMAs are much larger features; tolerate coarser simplification
        province_filter=None,  # kept national — CMHC publishes RMS for CMAs Canada-wide
    ),
]


def _download(zip_name: str, force: bool) -> Path:
    BOUNDARIES_DIR.mkdir(parents=True, exist_ok=True)
    path = BOUNDARIES_DIR / zip_name
    if path.exists() and not force:
        print(f"  cached: {path.relative_to(BOUNDARIES_DIR.parent.parent)} ({path.stat().st_size:,} bytes)")
        return path
    url = f"{BOUNDARY_BASE}/{zip_name}"
    print(f"  downloading {url}")
    # Write to a .tmp sibling and atomically rename on success so an
    # interrupted download never leaves a corrupt file masquerading as cached.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"  wrote {path.stat().st_size:,} bytes")
    return path


def _process(spec: BoundarySpec, zip_path: Path, simplify: bool) -> Path:
    # geopandas reads directly from inside a zip via the zip:// prefix
    gdf = gpd.read_file(f"zip://{zip_path}")
    print(f"  read {len(gdf):,} features ({list(gdf.columns)[:6]}…)")

    if spec.province_filter is not None:
        if "PRUID" not in gdf.columns:
            raise RuntimeError(f"expected PRUID column in {zip_path.name}, got {list(gdf.columns)}")
        gdf = gdf[gdf["PRUID"] == spec.province_filter].copy()
        print(f"  filtered to PRUID={spec.province_filter}: {len(gdf):,} features")
    else:
        print(f"  no province filter (national): {len(gdf):,} features")

    gdf = gdf.to_crs(epsg=4326)

    out_path = CLEAN_DIR / spec.output_name
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    if simplify:
        topo = tp.Topology(gdf, prequantize=True).toposimplify(spec.simplify_epsilon)
        out_path.write_text(topo.to_geojson())
    else:
        gdf.to_file(out_path, driver="GeoJSON")

    print(f"  wrote {out_path.relative_to(CLEAN_DIR.parent.parent)} ({out_path.stat().st_size:,} bytes)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="Redownload zips even if cached.")
    parser.add_argument("--no-simplify", action="store_true", help="Skip topology simplification.")
    parser.add_argument("--only", choices=[s.label for s in SPECS], help="Process only this layer.")
    args = parser.parse_args()

    specs = [s for s in SPECS if not args.only or s.label == args.only]
    for spec in specs:
        print(f"\n== {spec.label.upper()} ==")
        zip_path = _download(spec.zip_name, force=args.force)
        _process(spec, zip_path, simplify=not args.no_simplify)


if __name__ == "__main__":
    main()
