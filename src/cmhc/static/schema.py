"""The shared long-format contract for static-table parsers.

Static parsers are separate from the HMIP library at acquisition — these are
bespoke spreadsheet files, not a parameterized API — but they converge on the
*same long-format schema* HMIP's `tidy()` produces, so both feed one
source-agnostic data mart.

`COLUMNS` is that contract: the HMIP long schema plus a `source` column. Every
static parser returns a DataFrame with exactly these columns, in this order.

    period         date | null  start-of-period date
    sub_geography  str  | null  sub-breakdown within `geography` (usually null here)
    category       str          the measured quantity (e.g. metric name)
    value          f64  | null  suppressed / blank → null
    reliability    str  | null  static files rarely carry reliability codes → null
    survey         str          data family (the static section, e.g. 'Mortgage and Debt')
    table_id       str          stable page slug (joins to static_catalogue.json)
    geography      str          geography name as it appears in the source file
    source         str          always 'static'

`geography` is stored verbatim from the file. Reconciling those names to the
HMIP geography set (em-dash spacing, 'Newfoundland' → 'Newfoundland and
Labrador', etc.) is a mart-build concern via `cmhc.geographies.normalize_name`,
not the parser's job — the parser stays a pure spreadsheet → long transform.
"""

SOURCE = "static"

COLUMNS = [
    "period",
    "sub_geography",
    "category",
    "value",
    "reliability",
    "survey",
    "table_id",
    "geography",
    "source",
]
