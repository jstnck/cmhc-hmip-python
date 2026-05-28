"""Which (table, geography) combinations actually have data on HMIP.

HMIP doesn't enforce this — invalid combos silently return zeros or garbage.
So we filter client-side based on what each table is structured for.

REMOVABLE OPTIMIZATION: non-CMA-member CSDs skip CMA-scoped breakdowns
(Survey Zones / Neighbourhoods / Census Tracts) since those geographies are
defined inside CMAs. Empirically backed by 881/881 empty results in the
2026-05-23 `pull_csds.py --all` sample (logged in DATA_DISCOVERY.md), but
*not yet conclusive across every RMS table_id*. If we ever want to re-verify
or CMHC starts publishing at the non-CMA level, remove the `CMA_SCOPED_*`
guard in `is_valid_for_geo`. Currently scoped to Ontario only — extends
automatically once other-province CMA-member lists exist.
"""

from cmhc.catalogue import Table
from cmhc.geographies import (
    CANADA,
    CSDS_ONTARIO_CMA,
    TYPE_CMA,
    TYPE_CSD,
    TYPE_CT,
    TYPE_PROVINCE,
    Geography,
)


# Breakdowns that only make sense at CMA level or below.
SUB_CMA_BREAKDOWNS = {"Survey Zones", "Census Subdivision", "Neighbourhoods", "Census Tracts"}

# Breakdowns that only make sense INSIDE a CMA — these are statistical
# geographies (zones, neighbourhoods, CTs) defined by CMHC/StatCan as
# subdivisions of CMAs. Querying them at a non-CMA-member CSD returns empty.
CMA_SCOPED_BREAKDOWNS = {"Survey Zones", "Neighbourhoods", "Census Tracts"}

# CSDUIDs that sit inside a CMA. Currently Ontario only; the membership check
# silently falls through for other provinces (treated as unknown → permissive).
_CMA_MEMBER_CSD_UIDS = frozenset(CSDS_ONTARIO_CMA.keys())

# geo_filter values used in the catalogue for explicit Canada-aggregate tables.
# "Default" means the table is designed for CMA-level queries.
CANADA_AGG_FILTERS = {"All", "10k", "50k", "Metro"}

# Table IDs HMIP returns persistent 500s for at every geography. Stale R
# catalogue entries the HMIP backend no longer serves. Skip rather than retry.
#
# NOTE: a much larger set of Scss 1.x tables 500s for *specific* (table, CMA)
# pairs but works fine for most other CMAs — those are deliberately NOT
# denylisted here, since blocking them would lose the ~35 valid CMA datasets
# per table. See "Known issues" in PROGRESS.md.
BROKEN_TABLE_IDS = {"1.16.3.4", "1.16.3.5"}


def _is_known_non_cma_csd(geo: Geography) -> bool:
    """True if we know this CSD is *not* a CMA member. False if it is a member
    OR if we don't have a CMA-member list for its province yet (permissive)."""
    if geo.province_code == "35":  # Ontario — we have the lookup
        return geo.geography_id not in _CMA_MEMBER_CSD_UIDS
    return False  # unknown province → don't restrict


def is_valid_for_geo(table: Table, geo: Geography) -> bool:
    """Return True if (table, geo) is a query worth making."""
    if table.table_id in BROKEN_TABLE_IDS:
        return False

    if geo is CANADA:
        # Canada-level: snapshot listings (Provinces/Centres breakdown) OR
        # explicit Canada-aggregate tables (geo_filter in CANADA_AGG_FILTERS).
        if table.breakdown in SUB_CMA_BREAKDOWNS:
            return False
        if table.breakdown in ("Provinces", "Centres"):
            return True
        return table.geo_filter in CANADA_AGG_FILTERS

    if geo.geography_type_id == TYPE_PROVINCE:
        # Only "Centres" breakdown reliably works at province level — it lists
        # the CMAs within the province.
        if table.breakdown in SUB_CMA_BREAKDOWNS:
            return False
        return table.breakdown == "Centres" and table.geo_filter == "Default"

    if geo.geography_type_id == TYPE_CMA:
        # CMA queries can't ask for parent-geography breakdowns (Provinces /
        # Centres) — those describe siblings of the CMA, not its contents.
        # Sub-CMA breakdowns also don't apply here (those are for sub-CMA geos).
        if table.breakdown in ("Provinces", "Centres", *SUB_CMA_BREAKDOWNS):
            return False
        return table.geo_filter == "Default"

    if geo.geography_type_id in (TYPE_CSD, TYPE_CT):
        # Sub-CMA queries: accept any Default table whose breakdown isn't
        # explicitly a parent-geography breakdown. Generally permissive — an
        # invalid combo just becomes an empty marker, not lost data.
        if table.breakdown in ("Provinces", "Centres"):
            return False
        # Optimization (removable — see module docstring): non-CMA-member CSDs
        # have no Survey Zones / Neighbourhoods / CTs inside them, so skip
        # those breakdowns. Cuts ~21k wasted requests off a full Ontario CSD
        # sweep. CTs always live inside CMAs so the guard doesn't trip for them.
        if (
            geo.geography_type_id == TYPE_CSD
            and table.breakdown in CMA_SCOPED_BREAKDOWNS
            and _is_known_non_cma_csd(geo)
        ):
            return False
        return table.geo_filter == "Default"

    return False
