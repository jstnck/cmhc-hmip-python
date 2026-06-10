"""HMIP table catalogue.

Ported from mountainMath/cmhc R package (cmhc_tables.R). Each entry maps a
human-readable (Survey, Series, Dimension, Breakdown, GeoFilter) tuple to the
TableId that the HMIP ExportTable endpoint expects.

The R package is still maintained, but its vignettes only exercise a narrow
slice of the catalogue (Bedroom Type, Dwelling Type) — so stale filter sets
and dead table_ids in less-used dimensions can sit upstream for years. When
porting changes from upstream, verify each row with `scripts/probe_table.py`;
log finds in `docs/DATA_DISCOVERY.md`.
"""

from dataclasses import dataclass, field


# Geo type code lookups. Different surveys use different code spaces — these
# mirror cmhc_type_codes1/2/3/4 in the R package.
GEO_CODES_SCSS_SNAPSHOT = {
    "Provinces": 2,
    "Centres": 3,
    "Survey Zones": 8,
    "Census Subdivision": 9,
    "Neighbourhoods": 10,
    "Census Tracts": 11,
}
GEO_CODES_SCSS_PRICE = {
    "Survey Zones": 6,
    "Census Subdivision": 7,
    "Neighbourhoods": 8,
    "Census Tracts": 9,
}
GEO_CODES_RMS = {
    "Provinces": 1,
    "Centres": 2,
    "Survey Zones": 3,
    "Census Subdivision": 4,
    "Neighbourhoods": 5,
    "Census Tracts": 6,
}
GEO_CODES_RMS_QUARTILES = {
    "Survey Zones": 3,
    "Census Subdivision": 4,
}

DWELLING_TYPES = ["Single", "Semi-detached", "Row", "Apartment", "All"]
INTENDED_MARKETS = ["Homeowner", "Rental", "Condo", "Co-op", "All"]
BEDROOM_TYPES = ["Bachelor", "1 Bedroom", "2 Bedroom", "3 Bedroom +", "Total"]

SCSS_FILTERS = {
    "dimension-18": INTENDED_MARKETS,
    "dimension-1": DWELLING_TYPES,
}
TENURE_FILTER = {"Tenure": ["Total", "Renters", "Owners"]}


@dataclass(frozen=True)
class Table:
    survey: str
    series: str
    dimension: str | None
    breakdown: str
    table_id: str
    geo_filter: str = "Default"
    filters: dict = field(default_factory=dict)


# SCSS snapshot series. Each series has dwelling-type and (optionally) intended-
# market dimensions. `h_*` values are the DimensionCodes used to construct the
# time-series TableCode (which uses a different code space than snapshot
# breakdowns — see cmhc_tables.R for the dual-code mechanic).
# `geo_set` selects which geographic breakdowns are exposed:
#   "1" → all 6 (Provinces, Centres, Survey Zones, CSD, Neighbourhoods, CT)
#   "2" → only sub-CMA (Survey Zones, CSD, Neighbourhoods, CT)
_SCSS_SNAPSHOT_SPECS = [
    # series,                          s,    h_dwelling, d_intended, h_intended, geo_set
    ("Starts",                         "1",  "2",        "4",        "16",       "1"),
    ("Completions",                    "2",  "2",        "4",        "16",       "1"),
    ("Under Construction",             "3",  "2",        "4",        "9",        "1"),
    ("Length of Construction",         "7",  "2",        "4",        "16",       "2"),
    ("Absorbed Units",                 "5",  "2",        "4",        "16",       "2"),
    ("Share absorbed at completion",   "6",  "2",        None,       None,       "2"),
    ("Unabsorbed Inventory",           "4",  "2",        None,       None,       "2"),
]

# DimensionCode for Dwelling Type is always "1" in scss_snapshot1/2.
_DWELLING_DIM_CODE = "1"

# Time-series TableCode overrides — quirks in HMIP's table numbering captured
# via case_when in cmhc_tables.R.
_SCSS_TS_OVERRIDES = {
    ("Length of Construction", "Intended Market"): "1.2.8",
}


def _expand_scss_snapshot() -> list[Table]:
    """SCSS snapshot tables: series × dimension × geographic breakdown."""
    out: list[Table] = []
    for series, s_code, _h_dw, d_intended_code, _h_int, geo_set in _SCSS_SNAPSHOT_SPECS:
        geos = GEO_CODES_SCSS_SNAPSHOT if geo_set == "1" else GEO_CODES_SCSS_PRICE
        # Dwelling Type dimension — every series has it
        for geo, g_code in geos.items():
            table_id = f"1.{_DWELLING_DIM_CODE}.{s_code}.{g_code}"
            out.append(Table("Scss", series, "Dwelling Type", geo, table_id, filters=SCSS_FILTERS))
        # Intended Market dimension — only if the series defines it
        if d_intended_code is not None:
            for geo, g_code in geos.items():
                table_id = f"1.{d_intended_code}.{s_code}.{g_code}"
                out.append(Table("Scss", series, "Intended Market", geo, table_id, filters=SCSS_FILTERS))
    return out


def _expand_scss_timeseries() -> list[Table]:
    """SCSS historical time series — one per (series, dimension)."""
    out: list[Table] = []
    for series, s_code, h_dw, d_intended_code, h_int, _geo_set in _SCSS_SNAPSHOT_SPECS:
        # Dwelling Type time series
        table_id = _SCSS_TS_OVERRIDES.get((series, "Dwelling Type"), f"1.{h_dw}.{s_code}")
        out.append(Table("Scss", series, "Dwelling Type", "Historical Time Periods", table_id, filters=SCSS_FILTERS))
        # Intended Market time series
        if d_intended_code is not None:
            table_id = _SCSS_TS_OVERRIDES.get((series, "Intended Market"), f"1.{h_int}.{s_code}")
            out.append(Table("Scss", series, "Intended Market", "Historical Time Periods", table_id, filters=SCSS_FILTERS))
    return out


# RMS series: (series, series_code, dimension, dimension_code, geo_set)
# geo_set is "rms" (6 geos) or "quartiles" (2 geos). Mirrors rms_snapshot in R.
#
# NOTE: The R package's cmhc_tables.R applies a `bedroom_count_type_desc_en`
# AppliedFilter to most of these rows. HMIP rejects that filter for tables where
# bedroom isn't a dimension — the cross-product narrows to zero rows and HMIP
# returns "No data available", indistinguishable from a real empty result.
# We omit the bedroom filter entirely. See docs/DATA_DISCOVERY.md 2026-05-23 entry.
_RMS_SERIES = [
    ("Vacancy Rate",      "1", "Bedroom Type",         "1",  "rms"),
    ("Vacancy Rate",      "1", "Year of Construction", "2",  "rms"),
    ("Vacancy Rate",      "1", "Structure Size",       "3",  "rms"),
    ("Vacancy Rate",      "1", "Rent Ranges",          "4",  "rms"),
    ("Vacancy Rate",      "1", "Rent Quartiles",       "33", "quartiles"),
    ("Availability Rate", "1", "Bedroom Type",         "6",  "rms"),
    ("Availability Rate", "1", "Year of Construction", "7",  "rms"),
    ("Availability Rate", "1", "Structure Size",       "8",  "rms"),
    ("Average Rent",      "1", "Bedroom Type",         "11", "rms"),
    ("Average Rent",      "1", "Year of Construction", "13", "rms"),
    ("Average Rent",      "1", "Structure Size",       "15", "rms"),
    ("Average Rent Change", "1", "Bedroom Type",       "12", "rms"),
    ("Median Rent",       "1", "Bedroom Type",         "21", "rms"),
    ("Median Rent",       "1", "Year of Construction", "22", "rms"),
    ("Median Rent",       "1", "Structure Size",       "23", "rms"),
    ("Rental Universe",   "1", "Bedroom Type",         "26", "rms"),
    ("Rental Universe",   "1", "Year of Construction", "27", "rms"),
    ("Rental Universe",   "1", "Structure Size",       "28", "rms"),
    ("Summary Statistics","1", None,                   "31", "quartiles"),
]


def _rms_filters() -> dict:
    return {"dwelling_type_desc_en": ["Row / Apartment", "Row", "Apartment"]}


def _expand_rms_snapshot() -> list[Table]:
    out: list[Table] = []
    for series, s_code, dim, d_code, geo_set in _RMS_SERIES:
        geos = GEO_CODES_RMS if geo_set == "rms" else GEO_CODES_RMS_QUARTILES
        for geo, g_code in geos.items():
            # TableCode = SurveyCode.SeriesCode.DimensionCode.BreakdownCode
            table_id = f"2.{s_code}.{d_code}.{g_code}"
            out.append(Table("Rms", series, dim, geo, table_id, filters=_rms_filters()))
    return out


def _expand_rms_timeseries() -> list[Table]:
    """RMS time series: one row per (series, dimension), no geo dimension."""
    out: list[Table] = []
    seen: set[tuple[str, str | None]] = set()
    for series, _s_code, dim, d_code, _geo_set in _RMS_SERIES:
        key = (series, dim)
        if key in seen:
            continue
        seen.add(key)
        # R sets SeriesCode="2" for time series and drops breakdown code
        table_id = f"2.2.{d_code}"
        filters = _rms_filters() | {"season": ["October", "April"]}
        out.append(Table("Rms", series, dim, "Historical Time Periods", table_id, filters=filters))
    return out


def _srms() -> list[Table]:
    return [
        Table("Srms", "Condo Vacancy Rate",                  "Structure Size", "Historical Time Periods", "4.2.1"),
        Table("Srms", "Condo Average Rent",                  "Bedroom Type",   "Historical Time Periods", "4.4.2"),
        Table("Srms", "Condo Universe",                      "Structure Size", "Historical Time Periods", "4.2.3"),
        Table("Srms", "Rental Condo Universe",               "Structure Size", "Historical Time Periods", "4.2.4"),
        Table("Srms", "Percentage Condo used as Rental",     "Structure Size", "Historical Time Periods", "4.2.5"),
        Table("Srms", "Other Secondary Rental Universe",     "Dwelling Type",  "Historical Time Periods", "4.6.1"),
        Table("Srms", "Other Secondary Rental Average Rent", "Dwelling Type",  "Historical Time Periods", "4.6.2"),
    ]


def _seniors() -> list[Table]:
    rows = [
        ("Rental Housing Vacancy Rates", "Unit Type", "Snapshot", "1"),
        ("Rental Housing Vacancy Rates", "Unit Type", "Historical Time Periods", "1"),
        ("Spaces", "Unit Type", "Snapshot", "1"),
        ("Spaces", "Unit Type", "Historical Time Periods", "2"),
        ("Universe and Number of Residents", "Spaces and Residents", "Snapshot", "2"),
        ("Universe and Number of Residents", "Spaces and Residents", "Historical Time Periods", "6"),
        ("Heavy Care Spaces", "Vacancy Rate and Average Rent", "Snapshot", "1"),
        ("Heavy Care Spaces", "Vacancy Rate and Average Rent", "Historical Time Periods", "3"),
        ("Proportion of Standard Spaces", "Rent Range", "Snapshot", "1"),
        ("Proportion of Standard Spaces", "Rent Range", "Historical Time Periods", "2"),
    ]
    # SeriesCode varies per series in the R source; for snapshot/timeseries the
    # TableCode is built as SurveyCode.SeriesCode.BreakdownCode. We bake the
    # SeriesCode into the lookup below to keep the dataclass simple.
    series_codes = {
        ("Rental Housing Vacancy Rates", "Snapshot"): "2",
        ("Rental Housing Vacancy Rates", "Historical Time Periods"): "8",
        ("Spaces", "Snapshot"): "3",
        ("Spaces", "Historical Time Periods"): "3",
        ("Universe and Number of Residents", "Snapshot"): "1",
        ("Universe and Number of Residents", "Historical Time Periods"): "8",
        ("Heavy Care Spaces", "Snapshot"): "4",
        ("Heavy Care Spaces", "Historical Time Periods"): "8",
        ("Proportion of Standard Spaces", "Snapshot"): "6",
        ("Proportion of Standard Spaces", "Historical Time Periods"): "6",
    }
    out = []
    for series, dim, breakdown, breakdown_code in rows:
        s_code = series_codes[(series, breakdown)]
        out.append(Table("Seniors", series, dim, breakdown, f"3.{s_code}.{breakdown_code}"))
    return out


def _canada_tables() -> list[Table]:
    """Canada/provincial tables with explicit GeoFilter (All / 10k / 50k / Metro)."""
    rows = [
        # (series, dim, breakdown, geo_filter, table_id)
        ("Starts",      "Dwelling Type",  "Historical Time Periods", "All",   "5.7.2"),
        ("Starts",      "Dwelling Type",  "Historical Time Periods", "10k",   "5.6.2"),
        ("Starts",      "Intended Market","Historical Time Periods", "10k",   "1.16.1.6"),
        ("Starts",      "Dwelling Type",  "Historical Time Periods", "50k",   "1.2.1.5"),
        ("Starts",      "Intended Market","Historical Time Periods", "50k",   "1.16.1.5"),
        ("Starts",      "Dwelling Type",  "Historical Time Periods", "Metro", "1.2.1.4"),
        ("Starts",      "Intended Market","Historical Time Periods", "Metro", "1.16.1.4"),
        ("Completions", "Dwelling Type",  "Historical Time Periods", "All",   "5.11.2"),
        ("Completions", "Dwelling Type",  "Historical Time Periods", "10k",   "5.10.2"),
        ("Completions", "Intended Market","Historical Time Periods", "10k",   "1.16.2.6"),
        ("Completions", "Dwelling Type",  "Historical Time Periods", "50k",   "1.2.2.5"),
        ("Completions", "Intended Market","Historical Time Periods", "50k",   "1.16.2.5"),
        ("Completions", "Dwelling Type",  "Historical Time Periods", "Metro", "1.2.2.4"),
        ("Completions", "Intended Market","Historical Time Periods", "Metro", "1.16.2.4"),
        ("Under Construction", "Dwelling Type",   "Historical Time Periods", "10k",   "1.2.3.6"),
        ("Under Construction", "Intended Market", "Historical Time Periods", "10k",   "1.9.3.6"),
        ("Under Construction", "Dwelling Type",   "Historical Time Periods", "50k",   "1.2.3.5"),
        ("Under Construction", "Intended Market", "Historical Time Periods", "50k",   "1.16.3.5"),
        ("Under Construction", "Dwelling Type",   "Historical Time Periods", "Metro", "1.2.3.4"),
        ("Under Construction", "Intended Market", "Historical Time Periods", "Metro", "1.16.3.4"),
    ]
    return [
        Table("Scss", s, d, b, tid, geo_filter=gf, filters=SCSS_FILTERS)
        for s, d, b, gf, tid in rows
    ]


def _census_tables() -> list[Table]:
    """Census-derived tables surfaced through HMIP (income, dwelling value, age, etc.)."""
    rows: list[tuple[str, str, str, str, dict]] = [
        # Income
        ("Income", "Average and Median", "Historical Time Periods", "7.63",   TENURE_FILTER),
        ("Income", "Average and Median", "Survey Zones",            "7.62.3", TENURE_FILTER),
        ("Income", "Average and Median", "Neighbourhoods",          "7.62.4", TENURE_FILTER),
        ("Income", "Average and Median", "Census Tracts",           "7.62.5", TENURE_FILTER),
        ("Income", "Ranges",             "Historical Time Periods", "6.60",   TENURE_FILTER),
        ("Income", "Ranges",             "Survey Zones",            "6.59.3", TENURE_FILTER),
        ("Income", "Ranges",             "Neighbourhoods",          "6.59.4", TENURE_FILTER),
        ("Income", "Ranges",             "Census Tracts",           "6.59.5", TENURE_FILTER),
        # Dwelling value
        ("Dwelling value", "Average", "Historical Time Periods", "6.4",   {}),
        ("Dwelling value", "Average", "Survey Zones",            "6.3.3", {}),
        ("Dwelling value", "Average", "Neighbourhoods",          "6.3.4", {}),
        ("Dwelling value", "Average", "Census Tracts",           "6.3.5", {}),
        ("Dwelling value", "Median",  "Historical Time Periods", "6.6",   {}),
        ("Dwelling value", "Median",  "Survey Zones",            "6.5.3", {}),
        ("Dwelling value", "Median",  "Neighbourhoods",          "6.5.4", {}),
        ("Dwelling value", "Median",  "Census Tracts",           "6.5.5", {}),
        # Age of population
        ("All Households", "Age of Population", "Historical Time Periods", "6.8",   {}),
        ("All Households", "Age of Population", "Survey Zones",            "6.7.3", {}),
        ("All Households", "Age of Population", "Neighbourhoods",          "6.7.4", {}),
        ("All Households", "Age of Population", "Census Tracts",           "6.7.5", {}),
        ("65 and over",    "Age of Population", "Historical Time Periods", "6.98",  {}),
        ("65 and over",    "Age of Population", "Survey Zones",            "6.97.3", {}),
        ("65 and over",    "Age of Population", "Neighbourhoods",          "6.97.4", {}),
        ("65 and over",    "Age of Population", "Census Tracts",           "6.97.5", {}),
        # Household maintainer age
        ("All Households", "Age of Primary Household Maintainer", "Historical Time Periods", "6.12",   TENURE_FILTER),
        ("All Households", "Age of Primary Household Maintainer", "Survey Zones",            "6.11.3", TENURE_FILTER),
        ("All Households", "Age of Primary Household Maintainer", "Neighbourhoods",          "6.11.4", TENURE_FILTER),
        ("All Households", "Age of Primary Household Maintainer", "Census Tracts",           "6.11.5", TENURE_FILTER),
        ("65 and over",    "Age of Primary Household Maintainer", "Historical Time Periods", "7.3",    TENURE_FILTER),
        ("65 and over",    "Age of Primary Household Maintainer", "Survey Zones",            "7.2.3",  TENURE_FILTER),
        ("65 and over",    "Age of Primary Household Maintainer", "Neighbourhoods",          "7.2.4",  TENURE_FILTER),
        ("65 and over",    "Age of Primary Household Maintainer", "Census Tracts",           "7.2.5",  TENURE_FILTER),
        # Core housing need
        ("Housing Standards", "% of Households in Core Housing Need", "Historical Time Periods", "7.17", TENURE_FILTER),
        ("Housing Standards", "Households in Core Housing Need",      "Historical Time Periods", "7.15", TENURE_FILTER),
        ("Housing Standards", "Households Tested For Core Housing Need", "Historical Time Periods", "6.96", TENURE_FILTER),
    ]
    # Mobility tables — same series naming as in R
    for series in ("Mobility 5 of All Households", "Mobility 5 of 65 and over",
                   "Mobility 1 of All Households", "Mobility 1 of 65 and over"):
        # Codes hardcoded per R; only including the ones explicitly in the R source
        pass  # handled below for clarity

    mobility_rows = [
        ("Mobility 5 of All Households", "Age of Primary Household Maintainer", "Historical Time Periods", "6.16",   TENURE_FILTER),
        ("Mobility 5 of All Households", "Age of Primary Household Maintainer", "Survey Zones",            "6.15.3", TENURE_FILTER),
        ("Mobility 5 of All Households", "Age of Primary Household Maintainer", "Neighbourhoods",          "6.15.4", TENURE_FILTER),
        ("Mobility 5 of All Households", "Age of Primary Household Maintainer", "Census Tracts",           "6.15.5", TENURE_FILTER),
        ("Mobility 5 of 65 and over",    "Age of Primary Household Maintainer", "Historical Time Periods", "7.7",    TENURE_FILTER),
        ("Mobility 5 of 65 and over",    "Age of Primary Household Maintainer", "Survey Zones",            "7.6.3",  TENURE_FILTER),
        ("Mobility 5 of 65 and over",    "Age of Primary Household Maintainer", "Neighbourhoods",          "7.6.4",  TENURE_FILTER),
        ("Mobility 5 of 65 and over",    "Age of Primary Household Maintainer", "Census Tracts",           "7.6.5",  TENURE_FILTER),
        ("Mobility 1 of All Households", "Age of Primary Household Maintainer", "Historical Time Periods", "6.20",   TENURE_FILTER),
        ("Mobility 1 of All Households", "Age of Primary Household Maintainer", "Survey Zones",            "6.19.3", TENURE_FILTER),
        ("Mobility 1 of All Households", "Age of Primary Household Maintainer", "Neighbourhoods",          "6.19.4", TENURE_FILTER),
        ("Mobility 1 of All Households", "Age of Primary Household Maintainer", "Census Tracts",           "6.19.5", TENURE_FILTER),
        ("Mobility 1 of 65 and over",    "Age of Primary Household Maintainer", "Historical Time Periods", "7.11",   TENURE_FILTER),
        ("Mobility 1 of 65 and over",    "Age of Primary Household Maintainer", "Survey Zones",            "7.10.3", TENURE_FILTER),
        ("Mobility 1 of 65 and over",    "Age of Primary Household Maintainer", "Neighbourhoods",          "7.10.4", TENURE_FILTER),
        ("Mobility 1 of 65 and over",    "Age of Primary Household Maintainer", "Census Tracts",           "7.10.5", TENURE_FILTER),
    ]

    out: list[Table] = []
    survey = "Census"
    for series, dim, breakdown, tid, flt in rows + mobility_rows:
        # Use the Core Housing Need survey label for those three rows
        sv = "Core Housing Need" if series == "Housing Standards" else survey
        out.append(Table(sv, series, dim, breakdown, tid, filters=flt))
    return out


def build_catalogue() -> list[Table]:
    """Return the full catalogue as a flat list of Table entries."""
    return (
        _expand_scss_snapshot()
        + _expand_scss_timeseries()
        + _expand_rms_snapshot()
        + _expand_rms_timeseries()
        + _srms()
        + _seniors()
        + _canada_tables()
        + _census_tables()
    )


CATALOGUE: list[Table] = build_catalogue()


def surveys() -> list[str]:
    return sorted({t.survey for t in CATALOGUE})


def find(survey: str | None = None, series: str | None = None,
         dimension: str | None = None, breakdown: str | None = None,
         geo_filter: str | None = None) -> list[Table]:
    """Filter the catalogue by any combination of fields."""
    out = CATALOGUE
    if survey is not None:
        out = [t for t in out if t.survey == survey]
    if series is not None:
        out = [t for t in out if t.series == series]
    if dimension is not None:
        out = [t for t in out if t.dimension == dimension]
    if breakdown is not None:
        out = [t for t in out if t.breakdown == breakdown]
    if geo_filter is not None:
        out = [t for t in out if t.geo_filter == geo_filter]
    return out
