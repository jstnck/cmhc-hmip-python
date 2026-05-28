"""Tests for is_valid_for_geo, especially the CMA-scoped breakdown filter
for non-CMA-member CSDs (a removable optimization documented in
validity.py's module docstring)."""

from cmhc.catalogue import Table
from cmhc.geographies import CSDS_ONTARIO, CSDS_ONTARIO_CMA
from cmhc.validity import is_valid_for_geo


def _csd(uid: str):
    return CSDS_ONTARIO[uid]


def _table(breakdown: str, table_id: str = "2.1.99.99"):
    return Table(
        survey="Rms", series="Vacancy Rate", dimension="Bedroom Type",
        breakdown=breakdown, table_id=table_id,
    )


def test_cma_member_csd_passes_all_breakdowns():
    """A CSD inside a CMA should accept Survey Zones / Neighbourhoods / CTs
    breakdowns (the optimization doesn't apply)."""
    toronto_uid = next(iter(CSDS_ONTARIO_CMA))  # any CMA-member CSD
    geo = CSDS_ONTARIO_CMA[toronto_uid]
    for breakdown in ("Survey Zones", "Neighbourhoods", "Census Tracts", "Census Subdivision",
                      "Historical Time Periods"):
        assert is_valid_for_geo(_table(breakdown), geo), \
            f"CMA-member CSD should accept {breakdown!r}"


def test_non_cma_csd_skips_cma_scoped_breakdowns():
    """A CSD outside any CMA should skip Survey Zones / Neighbourhoods / CTs
    because those geographies don't exist below the CMA level."""
    cma_uids = set(CSDS_ONTARIO_CMA.keys())
    non_cma_csd = next(g for uid, g in CSDS_ONTARIO.items() if uid not in cma_uids)
    for breakdown in ("Survey Zones", "Neighbourhoods", "Census Tracts"):
        assert not is_valid_for_geo(_table(breakdown), non_cma_csd), \
            f"non-CMA CSD should reject {breakdown!r}"


def test_non_cma_csd_still_accepts_csd_and_timeseries_breakdowns():
    """The optimization shouldn't block Census Subdivision (returns the queried
    CSD itself) or Historical Time Periods (per-CSD time series). Those have
    not been disproven to publish at non-CMA CSDs."""
    cma_uids = set(CSDS_ONTARIO_CMA.keys())
    non_cma_csd = next(g for uid, g in CSDS_ONTARIO.items() if uid not in cma_uids)
    assert is_valid_for_geo(_table("Census Subdivision"), non_cma_csd)
    assert is_valid_for_geo(_table("Historical Time Periods"), non_cma_csd)


def test_parent_breakdowns_always_rejected_for_csd():
    """Provinces / Centres breakdowns describe siblings, not contents — never
    valid at a CSD, CMA-member or not."""
    cma_uid = next(iter(CSDS_ONTARIO_CMA))
    for geo in (CSDS_ONTARIO_CMA[cma_uid], next(iter(CSDS_ONTARIO.values()))):
        for breakdown in ("Provinces", "Centres"):
            assert not is_valid_for_geo(_table(breakdown), geo)
