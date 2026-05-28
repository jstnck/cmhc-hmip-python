from collections import Counter

from cmhc.catalogue import CATALOGUE, find, surveys


def test_catalogue_not_empty():
    assert len(CATALOGUE) > 200


def test_expected_surveys_present():
    assert set(surveys()) == {"Census", "Core Housing Need", "Rms", "Scss", "Seniors", "Srms"}


def test_no_duplicate_lookup_keys():
    """Each (survey, series, dimension, breakdown, geo_filter) must map to one table_id."""
    keys = [(t.survey, t.series, t.dimension, t.breakdown, t.geo_filter) for t in CATALOGUE]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    assert dupes == [], f"Duplicate catalogue keys: {dupes}"


def test_table_ids_look_valid():
    """TableIds should be dot-separated numeric components."""
    for t in CATALOGUE:
        parts = t.table_id.split(".")
        assert len(parts) >= 2
        assert all(p.isdigit() for p in parts), f"Bad table_id: {t.table_id}"


def test_find_filters():
    rms_vacancy = find(survey="Rms", series="Vacancy Rate")
    assert len(rms_vacancy) > 0
    assert all(t.survey == "Rms" and t.series == "Vacancy Rate" for t in rms_vacancy)

    timeseries = find(survey="Rms", series="Vacancy Rate", breakdown="Historical Time Periods")
    assert len(timeseries) >= 1
    assert all(t.breakdown == "Historical Time Periods" for t in timeseries)


def test_canada_wide_starts_table_present():
    """The smoke-test path: Scss Starts time series with GeoFilter=All."""
    hits = find(
        survey="Scss", series="Starts", dimension="Dwelling Type",
        breakdown="Historical Time Periods", geo_filter="All",
    )
    assert len(hits) == 1
    assert hits[0].table_id == "5.7.2"


def test_new_scss_series_present():
    """Catalogue should include the SCSS series ported from cmhc_tables.R."""
    expected_series = {
        "Starts", "Completions", "Under Construction",
        "Length of Construction", "Absorbed Units",
        "Share absorbed at completion", "Unabsorbed Inventory",
    }
    scss_series = {t.series for t in find(survey="Scss")}
    missing = expected_series - scss_series
    assert not missing, f"Missing SCSS series: {missing}"


def test_length_of_construction_override():
    """R catalogue overrides this specific table_id: 1.2.8 not 1.16.7."""
    hits = find(
        survey="Scss", series="Length of Construction",
        dimension="Intended Market", breakdown="Historical Time Periods",
    )
    assert len(hits) == 1
    assert hits[0].table_id == "1.2.8"


def test_share_absorbed_only_has_dwelling_type():
    """In R, Share absorbed at completion only ships with Dwelling Type."""
    hits = find(survey="Scss", series="Share absorbed at completion")
    dimensions = {t.dimension for t in hits}
    assert dimensions == {"Dwelling Type"}
