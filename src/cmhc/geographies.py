"""Geography lookup: Stats Canada UID → CMHC (geography_id, geography_type_id).

Levels supported:
    - Canada           (geography_type_id=1, geography_id='1')
    - Provinces        (geography_type_id=2, geography_id=<2-digit PR code>)
    - Census Metropolitan Areas (geography_type_id=3, geography_id=METCODE)
    - Census Subdivisions       (geography_type_id=4, geography_id=CSDUID) — Ontario only
    - Census Tracts             (geography_type_id=7, geography_id=METCODE+NBHDCODE+CMHC_CT)
                                 — Ontario only

CMA/CSD/CT lookups are loaded from CSVs in src/cmhc/data/, extracted from
mountainMath's cmhc R package via scripts/extract_geo_lookups.py.

`geography_id` is always a string. It's used as an HTTP form value; preserving
leading zeros (e.g. CMA METCODE='0110') and supporting CT IDs with decimal
points (e.g. '01206500001.01') both require string typing.

Neighbourhoods and Survey Zones are derivable from the CT crosswalk but not
yet exposed as separate Geography sets.
"""

import csv
from dataclasses import dataclass
from importlib.resources import files


# CMHC's internal geography_type_id values, from cmhc_geography.R
TYPE_CANADA = 1
TYPE_PROVINCE = 2
TYPE_CMA = 3
TYPE_CSD = 4
TYPE_ZONE = 5
TYPE_NEIGHBOURHOOD = 6
TYPE_CT = 7


@dataclass(frozen=True)
class Geography:
    name: str
    geography_id: str
    geography_type_id: int
    cma_uid: str | None = None   # Stats Canada CMA UID (CMA-level only)
    province_code: str | None = None  # Stats Canada PR code (sub-CMA levels: for filtering)


CANADA = Geography("Canada", geography_id="1", geography_type_id=TYPE_CANADA)

# Stats Canada province / territory codes
PROVINCES: dict[str, Geography] = {
    name: Geography(name, geography_id=str(code), geography_type_id=TYPE_PROVINCE)
    for name, code in [
        ("Newfoundland and Labrador", 10),
        ("Prince Edward Island", 11),
        ("Nova Scotia", 12),
        ("New Brunswick", 13),
        ("Quebec", 24),
        ("Ontario", 35),
        ("Manitoba", 46),
        ("Saskatchewan", 47),
        ("Alberta", 48),
        ("British Columbia", 59),
        ("Yukon", 60),
        ("Northwest Territories", 61),
        ("Nunavut", 62),
    ]
}


def _load_cmas() -> dict[str, Geography]:
    csv_text = files("cmhc.data").joinpath("cmas.csv").read_text()
    reader = csv.DictReader(csv_text.splitlines())
    out: dict[str, Geography] = {}
    for row in reader:
        name = row["NAME_EN"]
        out[name] = Geography(
            name=name,
            geography_id=row["METCODE"],
            geography_type_id=TYPE_CMA,
            cma_uid=row["CMA_UID"],
        )
    return out


def _load_csds_ontario() -> dict[str, Geography]:
    """All Ontario CSDs (~574), keyed by CSDUID. GeographyId = CSDUID."""
    return _load_csd_csv("csds_ontario.csv")


def _load_csds_ontario_cma() -> dict[str, Geography]:
    """Ontario CSDs that sit inside a CMA (~168), per the 2021 census.

    Strictly a subset of CSDS_ONTARIO — use this for Rms / Srms pulls where
    CMHC only publishes data inside CMA boundaries. Use the full list for
    Census / Core Housing Need which publish at every CSD.
    """
    return _load_csd_csv("csds_ontario_cma_members.csv")


def _load_csd_csv(filename: str) -> dict[str, Geography]:
    csv_text = files("cmhc.data").joinpath(filename).read_text()
    reader = csv.DictReader(csv_text.splitlines())
    out: dict[str, Geography] = {}
    for row in reader:
        uid = row["CSDUID"]
        out[uid] = Geography(
            name=f"{row['CSDNAME']} ({row['CSDTYPE']})",
            geography_id=uid,
            geography_type_id=TYPE_CSD,
            province_code=uid[:2],
        )
    return out


def _load_cts_ontario() -> dict[str, Geography]:
    """Ontario CTs, keyed by GeographyId (METCODE+NBHDCODE+CMHC_CT).

    CTUID is NOT unique: 6 Ontario CTUIDs span two CMHC CSDs and get distinct
    CMHC_CT codes (and therefore distinct GeographyIds). Keying by GeographyId
    keeps all 2,382 rows; keying by CTUID would silently drop 6.
    """
    csv_text = files("cmhc.data").joinpath("cts_ontario.csv").read_text()
    reader = csv.DictReader(csv_text.splitlines())
    out: dict[str, Geography] = {}
    for row in reader:
        ctuid = row["CTUID"]
        geography_id = row["GeographyId"]
        # CSDNAME disambiguates the 6 CTUID splits; it's also more informative
        # than METNAME_EN for the on-disk filename.
        out[geography_id] = Geography(
            name=f"CT {ctuid} ({row['CSDNAME']})",
            geography_id=geography_id,
            geography_type_id=TYPE_CT,
            province_code=row["CSDUID"][:2],
        )
    return out


CMAS: dict[str, Geography] = _load_cmas()
CSDS_ONTARIO: dict[str, Geography] = _load_csds_ontario()
CSDS_ONTARIO_CMA: dict[str, Geography] = _load_csds_ontario_cma()
CTS_ONTARIO: dict[str, Geography] = _load_cts_ontario()


def get(name: str) -> Geography:
    """Look up a geography by name. Tries Canada, then provinces, then CMAs."""
    if name == "Canada":
        return CANADA
    if name in PROVINCES:
        return PROVINCES[name]
    if name in CMAS:
        return CMAS[name]
    raise KeyError(f"Unknown geography: {name!r}")


def all_geographies() -> list[Geography]:
    return [CANADA, *PROVINCES.values(), *CMAS.values()]
