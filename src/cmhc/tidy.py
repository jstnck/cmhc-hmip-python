"""Convert HMIP wide CSVs into tidy long-format DataFrames.

HMIP returns wide CSVs in three shapes:

    Time-series simple:      Time-series reliability:     Snapshot (no index col):
    ,A,B,C,                  ,A,,B,,C,,                   Title
    row1,1,2,3,              row1,1,a,2,b,3,c,            2021
    row2,4,5,6,              row2,4,a,5,b,6,c,            A,B,C,
                                                          1,2,3,

The empty-named columns in the reliability form carry CMHC's reliability
codes (a/b/c/d, or ** = suppressed). The snapshot form has no leading-comma
header (because there's no index column — period is implied by a line above
the header instead).

The first column's meaning depends on the table's breakdown:
    - Historical Time Periods → a time period (period in body)
    - Snapshot → period above the header (no index column)
    - Provinces / Centres / CSD / Neighbourhoods / CT / Survey Zones → a
      sub-geography (e.g. Quebec inside a Canada-level query). The period
      lives in the subtitle line ('October 2025 Row / Apartment …') above
      the header and is extracted via `_extract_subtitle_period`.

`tidy()` accepts the breakdown and populates either `period` or
`sub_geography` accordingly. The other column is always present but null,
so every tidy DataFrame has the same schema. Snapshot tables queried at the
geo itself (no sub-geos to list) come back as a single empty-index row that's
preserved with `sub_geography = null`. `period` is parsed to a real `date`
(start-of-period convention) via `_parse_period`.
"""

import csv
import io
from datetime import date

import polars as pl

from cmhc.wide import wide_to_long


_TIME_BREAKDOWNS = {"Historical Time Periods", "Snapshot"}
_GEO_BREAKDOWNS = {
    "Provinces", "Centres", "Census Subdivision",
    "Neighbourhoods", "Census Tracts", "Survey Zones",
}


# CMHC suppression / not-available sentinels in value cells.
NULL_TOKENS = {"", "**", "n/a", "N/A", "..", "-"}


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Quarter → start month (start-of-period convention)
_QUARTER_MONTH = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}


def _parse_period(s: str | None) -> date | None:
    """Parse HMIP period strings to start-of-period dates.

    Handles:
        '2021'        → 2021-01-01  (bare year — annual surveys: Census, Srms, Seniors, Core Housing)
        'Feb 1990'    → 1990-02-01
        '1990 March'  → 1990-03-01
        '1991/Q1'     → 1991-01-01 (Q2 → Apr 1, Q3 → Jul 1, Q4 → Oct 1)

    Returns None for null/empty input or unrecognized formats.
    """
    if not s:
        return None
    s = s.strip()
    if s.isdigit() and 1900 <= int(s) <= 2100:
        return date(int(s), 1, 1)
    if "/" in s:
        year_part, q_part = s.split("/", 1)
        if year_part.isdigit() and q_part in _QUARTER_MONTH:
            return date(int(year_part), _QUARTER_MONTH[q_part], 1)
        return None
    parts = s.split()
    if len(parts) != 2:
        return None
    a, b = parts
    if a.isdigit() and b.lower() in _MONTHS:
        return date(int(a), _MONTHS[b.lower()], 1)
    if b.isdigit() and a.lower() in _MONTHS:
        return date(int(b), _MONTHS[a.lower()], 1)
    return None


def _slice_data_block(raw: bytes) -> tuple[str, list[str], int]:
    """Strip HMIP metadata header and footer.

    Returns (csv_body, all_lines, header_idx) so callers can also reach back
    above the header (e.g. to read the snapshot subtitle that carries the
    period for geo-breakdown tables).
    """
    text = raw.decode("latin1")
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(","))
    except StopIteration:
        raise ValueError("No data table found in HMIP response")
    end = next((i for i, line in enumerate(lines[start:], start) if line.strip() == ""), len(lines))
    return "\n".join(lines[start:end]), lines, start


def _extract_subtitle_period(lines: list[str], header_idx: int) -> date | None:
    """Pull the snapshot date out of the line above the CSV header.

    HMIP snapshot CSVs put the reference period at the start of the line just
    above the comma-prefixed header, followed by the applied filters, e.g.
        'October 2025 Row / Apartment'
        'October 2025 Row / Apartment Bedroom Type - Total'
        '1990 to 2025 Row / Apartment October'  (time-series — period in data)

    We try `_parse_period` on the whole line, then on progressively shorter
    leading-token prefixes (3, 2, 1 tokens). Stop at the first non-empty line
    above the header so we don't accidentally match something deep in the file.
    """
    for j in range(header_idx - 1, -1, -1):
        line = lines[j].strip()
        if not line:
            continue
        parsed = _parse_period(line)
        if parsed:
            return parsed
        parts = line.split()
        for n in range(min(3, len(parts)), 0, -1):
            parsed = _parse_period(" ".join(parts[:n]))
            if parsed:
                return parsed
        return None
    return None


def _parse_number(s: str) -> float | None:
    s = s.strip()
    if s in NULL_TOKENS:
        return None
    # CMHC formats numbers with embedded commas: "1,234.5"
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def tidy(raw: bytes, breakdown: str | None = None) -> pl.DataFrame:
    """Parse an HMIP CSV and return a long-format DataFrame.

    Columns (always present, in this order):
        period          date | null — populated for Historical Time Periods / Snapshot
        sub_geography   str  | null — populated for Provinces / Centres / CSD / etc.
        category        str         — the dimension value (e.g. 'Apartment', 'Studio')
        value           f64  | null
        reliability     str  | null

    `breakdown` should be the catalogue's breakdown for this table. If omitted,
    the first column is treated as `period` (preserves the original behavior).
    """
    try:
        body, all_lines, header_idx = _slice_data_block(raw)
    except ValueError:
        # No leading-comma header line — fall back to the snapshot shape.
        return _tidy_snapshot(raw)

    df = pl.read_csv(
        io.BytesIO(body.encode("utf-8")),
        infer_schema_length=0,
        truncate_ragged_lines=True,
        has_header=True,
    )

    if df.width < 2:
        raise ValueError(f"Expected at least 2 columns, got {df.width}")

    # Decide which named column the first column maps to.
    if breakdown in _GEO_BREAKDOWNS:
        index_role = "sub_geography"
    else:
        index_role = "period"  # default for time-like or unknown
    df = df.rename({df.columns[0]: index_role})

    # Snapshot tables (geo breakdown) carry their reference period in the
    # subtitle line, not in the data. Pull it out before building the long
    # frame; for time-series the period comes from the data column and this
    # stays None.
    subtitle_period: date | None = None
    if index_role == "sub_geography":
        subtitle_period = _extract_subtitle_period(all_lines, header_idx)

    # Melt to long via the shared shape primitive. HMIP marks reliability
    # columns by leaving the header blank (polars renames those to ''/_duplicated_).
    out = wide_to_long(df, index_role, is_reliability=_is_unnamed, parse_value=_parse_number)

    # Drop empty-index rows ONLY if non-empty rows also exist (those are
    # summary/total rows duplicating data we already have). When EVERY row's
    # index is empty, this is a single-geo query — e.g. a CSD-level table
    # queried at the CSD itself returns one row with no sub-geo column —
    # and dropping it would lose the data entirely. Preserve it as null.
    nonempty = out.filter(pl.col(index_role).is_not_null() & (pl.col(index_role) != ""))
    if nonempty.height > 0:
        out = nonempty
    else:
        out = out.with_columns(pl.lit(None).cast(pl.Utf8).alias(index_role))

    if index_role == "period":
        # Time-series shape: period is in the data column; parse it. Null
        # input → null, so this is safe even on rows where parsing fails.
        out = out.with_columns(
            pl.col("period").map_elements(_parse_period, return_dtype=pl.Date).alias("period"),
            pl.lit(None).cast(pl.Utf8).alias("sub_geography"),
        )
    else:
        # Snapshot shape: period comes from the subtitle and applies to
        # every row uniformly.
        out = out.with_columns(pl.lit(subtitle_period).cast(pl.Date).alias("period"))

    # Stable column order.
    return out.select("period", "sub_geography", "category", "value", "reliability")


def _is_unnamed(col: str) -> bool:
    """Polars renames duplicate/blank headers to '' or '_duplicated_N'."""
    return col == "" or col.startswith("_duplicated_")


def _split_csv_line(line: str) -> list[str]:
    """Split a single CSV line respecting quotes. Trailing empty token dropped."""
    cells = next(csv.reader([line]))
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _tidy_snapshot(raw: bytes) -> pl.DataFrame:
    """Parse the snapshot CSV shape: no leading-comma header, single data row.

    HMIP snapshot CSVs look like:
        Title line
        2021                         # period — bare year, 'Mon YYYY', etc.
        col1,col2,col3,              # header — no leading comma
        v1,v2,v3,                    # single data row
        <blank>
        Source / Notes

    Detection: find two consecutive non-blank lines with matching token counts
    where the second line's tokens are mostly numeric. Period is the most
    recent earlier line that `_parse_period` accepts.
    """
    text = raw.decode("latin1")
    lines = text.splitlines()

    header_idx = None
    for i in range(len(lines) - 1):
        h, d = lines[i].strip(), lines[i + 1].strip()
        if not h or not d or h.startswith(",") or d.startswith(","):
            continue
        h_cells = _split_csv_line(h)
        d_cells = _split_csv_line(d)
        if len(h_cells) < 2 or len(h_cells) != len(d_cells):
            continue
        # Data row: every cell must be either a parseable number or a known
        # null sentinel (suppressed, dash, etc.). This rejects notes lines
        # (e.g. "Source,CMHC ...") while accepting all-suppressed rows.
        if not all(_parse_number(c) is not None or c.strip() in NULL_TOKENS for c in d_cells):
            continue
        header_idx = i
        break

    if header_idx is None:
        raise ValueError("No data table found in HMIP response")

    header_cells = _split_csv_line(lines[header_idx])
    data_cells = _split_csv_line(lines[header_idx + 1])

    # Period: scan backward from the header for a parseable date string.
    period: date | None = None
    for j in range(header_idx - 1, -1, -1):
        parsed = _parse_period(lines[j].strip())
        if parsed is not None:
            period = parsed
            break

    return pl.DataFrame(
        {
            "period": [period] * len(header_cells),
            "sub_geography": [None] * len(header_cells),
            "category": [c.strip() for c in header_cells],
            "value": [_parse_number(c) for c in data_cells],
            "reliability": [None] * len(header_cells),
        },
        schema={
            "period": pl.Date,
            "sub_geography": pl.Utf8,
            "category": pl.Utf8,
            "value": pl.Float64,
            "reliability": pl.Utf8,
        },
    )
