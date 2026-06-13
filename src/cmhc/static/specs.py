"""The parse-recipe registry: static-table slug → MatrixSpec.

Declarative config kept in Python (typed, validated at import, can hold a
callable) rather than JSON — JSON is reserved for *discovered facts*
(static_catalogue.json). Each entry says how the engine should read one file.

Only tables the flat matrix engine parses *correctly* appear here. Two kinds are
deliberately absent:
  - housing-market-data: duplicates HMIP's Scss (starts / completions / absorption).
  - multi-dimensional tables (a leading Tenure / Age group / Quintile column on
    top of geography × period): the flat engine would silently collapse the extra
    dimension. These await a multi-dimension engine mode. Candidates are screened
    by a duplicate-(geography, period, category) key check before being added here.
"""

from cmhc.static.matrix import MatrixSpec, Sheets

_NOTES = frozenset({"Notes"})

# Single sheet, geographies × periods, one metric (category = catalogue title).
_SINGLE_PERIOD = [
    "mortgage-delinquency-rate-canada-provinces-cmas",
    "household-growth-summary-canada-provinces-territories-cmas",
    "number-households-canada-provinces-select-metropolitan-areas",
    "number-owner-households-owner-canada-provinces-cmas",
    "number-renter-households-renter-canada-provinces-cmas",
    "number-urban-households-core-housing-need",
    "ownership-rates-canada-provinces-territories-metropolitan-areas",
    "real-median-after-tax-household-income-1990-2011",
    "real-median-after-tax-household-income-owner-households-1990-2011",
    "real-median-after-tax-household-income-renter-households-1990-2011",
    "real-median-total-household-income-before-taxes-1990-2011",
    "real-median-total-household-income-before-taxes-owners-1990-2011",
]

SPECS: dict[str, MatrixSpec] = {
    # --- single sheet, geographies × periods ---
    **{slug: MatrixSpec(sheets=Sheets(mode="single"), axis="period") for slug in _SINGLE_PERIOD},

    # --- one sheet per census year; geographies × categories ---
    "number-households-core-housing-need": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="period", skip=_NOTES),
        axis="category", header_marker="Geography1",
    ),

    # --- one sheet per category (tenure / measure); geographies × years,
    #     with interleaved 'Data quality' reliability columns ---
    "real-average-total-household-income-before-taxes": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=_NOTES),
        axis="period", header_marker="Geography", reliability="Data quality",
    ),
    "real-average-household-income-after-taxes-tenure": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=_NOTES),
        axis="period", header_marker="Geography", reliability="Data quality",
    ),
    "incidence-urban-households-core-housing-need": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=_NOTES),
        axis="period", header_marker="Geography", reliability="Data quality",
    ),
    # These two carry no 'Geography' marker — header is found by the period heuristic.
    "real-median-household-income-after-tax-tenure": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=_NOTES),
        axis="period", reliability="Data quality",
    ),
    "real-median-total-household-income-before-taxes": MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=_NOTES),
        axis="period", reliability="Data quality",
    ),
}
