"""One-shot: download mountainMath/cmhc R-package geo lookup tables and write
Ontario-filtered CSVs into src/cmhc/data/.

Inputs (downloaded from GitHub):
    cmhc_csd_translation_data.rda          (5,161 Canada-wide CSDs)
    cmhc_csd_translation_data_2023.rda     (918 Canada-wide CMA-member CSDs, 2021 census)
    cmhc_ct_translation_data.rda           (5,934 Canada-wide CTs, with NBHD/ZONE/CSD/CMA crosswalk)

Outputs (Ontario only, province code 35):
    src/cmhc/data/csds_ontario.csv             — all 574 Ontario CSDs
    src/cmhc/data/csds_ontario_cma_members.csv — ~150 Ontario CSDs that sit inside a CMA
                                                  (the only ones where Rms/Srms publish data)
    src/cmhc/data/cts_ontario.csv              — ~2,382 Ontario CTs with full crosswalk

Wire format (per mountainMath/cmhc R/cmhc_geography.R):
    CSD GeographyId = CSDUID directly
    CT  GeographyId = METCODE + NBHDCODE + CMHC_CT  (concatenated string)

Re-run when mountainMath updates their .rda files:
    uv run --with pyreadr python scripts/extract_geo_lookups.py
"""

import tempfile
from pathlib import Path
from urllib.request import urlretrieve

import pyreadr


ONTARIO_PROVINCE_CODE = "35"
REPO_BASE = "https://github.com/mountainMath/cmhc/raw/master/data"
OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "cmhc" / "data"


def _download(name: str, dest: Path) -> None:
    url = f"{REPO_BASE}/{name}"
    print(f"  fetching {url}")
    urlretrieve(url, dest)


def _read_rda_single(path: Path):
    """Read an .rda file that contains exactly one data frame, return it."""
    result = pyreadr.read_r(str(path))
    if len(result) != 1:
        raise ValueError(f"{path.name}: expected one R object, got {list(result)}")
    return next(iter(result.values()))


def extract_csds(tmp: Path) -> None:
    # All Ontario CSDs (~574)
    src = tmp / "cmhc_csd_translation_data.rda"
    _download("cmhc_csd_translation_data.rda", src)
    df = _read_rda_single(src)
    df["CSDUID"] = df["CSDUID"].astype(str)
    on = df[df["CSDUID"].str.startswith(ONTARIO_PROVINCE_CODE)].copy()
    on = on[["CSDUID", "CSDNAME", "CSDTYPE", "CMHC_CSDUID"]]
    out = OUT_DIR / "csds_ontario.csv"
    on.to_csv(out, index=False)
    print(f"  wrote {len(on)} Ontario CSDs → {out.relative_to(OUT_DIR.parents[2])}")

    # Ontario CSDs that are CMA members (post-2021 census). Inner-join the
    # 2023 file's GeoUID list against the full CSD table to enrich with name.
    src2 = tmp / "cmhc_csd_translation_data_2023.rda"
    _download("cmhc_csd_translation_data_2023.rda", src2)
    members = _read_rda_single(src2)
    members["GeoUID"] = members["GeoUID"].astype(str)
    on_members = members[members["GeoUID"].str.startswith(ONTARIO_PROVINCE_CODE)].copy()
    on_members = on_members.merge(on, left_on="GeoUID", right_on="CSDUID", how="left")
    on_members = on_members[["CSDUID", "CSDNAME", "CSDTYPE", "CMHC_CSDUID", "METCODE"]]
    out2 = OUT_DIR / "csds_ontario_cma_members.csv"
    on_members.to_csv(out2, index=False)
    print(f"  wrote {len(on_members)} CMA-member Ontario CSDs → {out2.relative_to(OUT_DIR.parents[2])}")


def extract_cts(tmp: Path) -> None:
    src = tmp / "cmhc_ct_translation_data.rda"
    _download("cmhc_ct_translation_data.rda", src)
    df = _read_rda_single(src)
    on = df[df["CSDUID"].astype(str).str.startswith(ONTARIO_PROVINCE_CODE)].copy()

    # The source encodes missing CTUIDs as the literal string ' ' (single space).
    # Strip them out — they're not real CTs (~15 such rows in Ontario).
    # Note: pyreadr reads CTUID as already-formatted strings ('5680001.01'), but
    # downstream polars readers must pass schema_overrides={"CTUID": pl.Utf8} or
    # dtype inference will collapse '5680001.10' → 5680001.1 (Float64).
    on["CTUID"] = on["CTUID"].astype(str).str.strip()
    bad = on["CTUID"].eq("").sum()
    if bad:
        print(f"  dropping {bad} rows with empty CTUID")
        on = on[on["CTUID"].ne("")].copy()

    # Precompute the GeographyId per R/cmhc_geography.R line 65:
    #   id = paste0(METCODE, NBHDCODE, CMHC_CT)
    on["GeographyId"] = (
        on["METCODE"].astype(str) + on["NBHDCODE"].astype(str) + on["CMHC_CT"].astype(str)
    )
    keep = ["CTUID", "CSDUID", "CSDNAME", "METCODE", "METNAME_EN",
            "NBHDCODE", "NBHDNAME_EN", "CMHC_CT", "GeographyId"]
    on = on[keep]
    out = OUT_DIR / "cts_ontario.csv"
    on.to_csv(out, index=False)
    print(f"  wrote {len(on)} Ontario CTs → {out.relative_to(OUT_DIR.parents[2])}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        extract_csds(tmp)
        extract_cts(tmp)


if __name__ == "__main__":
    main()
