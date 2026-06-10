import pytest

from cmhc.geographies import CANADA, CMAS, PROVINCES, get, normalize_name


def test_canada():
    assert CANADA.geography_id == "1"
    assert CANADA.geography_type_id == 1


def test_all_provinces_and_territories():
    assert len(PROVINCES) == 13
    bc = get("British Columbia")
    assert bc.geography_id == "59"
    assert bc.geography_type_id == 2


def test_cma_lookup_loaded():
    # The R package ships ~150 CMAs/centres
    assert len(CMAS) >= 150


def test_vancouver_cma():
    van = get("Vancouver")
    # METCODE comes through as a string, preserving any leading zeros.
    assert van.geography_id == "2410"
    assert van.geography_type_id == 3
    assert van.cma_uid == "59933"


def test_ontario_csd_lookup_loaded():
    from cmhc.geographies import CSDS_ONTARIO

    assert len(CSDS_ONTARIO) > 400  # ~574 expected
    # Toronto City — CSDUID 3520005
    tor = CSDS_ONTARIO["3520005"]
    assert tor.geography_id == "3520005"
    assert tor.geography_type_id == 4
    assert tor.province_code == "35"
    assert "Toronto" in tor.name


def test_ontario_ct_lookup_loaded():
    from cmhc.geographies import CTS_ONTARIO

    assert len(CTS_ONTARIO) > 2000  # ~2,376 unique CTUIDs (after dropping 15 source nulls)
    sample = next(iter(CTS_ONTARIO.values()))
    # GeographyId for a CT is METCODE+NBHDCODE+CMHC_CT — contains a '.'
    assert "." in sample.geography_id
    assert sample.geography_type_id == 7
    assert sample.province_code == "35"


def test_unknown_geo_raises():
    with pytest.raises(KeyError):
        get("Atlantis")


def test_normalize_name_slash_to_hyphen():
    # Regression for 2026-06-10 finding: StatCan reference data uses '/' for
    # compound CSD/CMA names but CMHC's HMIP renders them with ' - '. The mart
    # filter previously rejected the StatCan form against HMIP-sourced parquet
    # geography names, silently dropping 5 Ontario CSDs (Guelph-Eramosa, Greater
    # Sudbury, McNab-Braeside, The Nation, West Nipissing). See
    # docs/DATA_DISCOVERY.md 2026-06-10 entry.
    assert normalize_name("Guelph/Eramosa (TP)") == "Guelph-Eramosa (TP)"
    assert normalize_name("Greater Sudbury / Grand Sudbury (CV)") == (
        "Greater Sudbury - Grand Sudbury (CV)"
    )
    assert normalize_name("McNab/Braeside (TP)") == "McNab-Braeside (TP)"
    # Idempotent: already-normalized names are unchanged
    assert normalize_name("Toronto") == "Toronto"
    assert normalize_name("Guelph-Eramosa (TP)") == "Guelph-Eramosa (TP)"
    # None propagates
    assert normalize_name(None) is None
