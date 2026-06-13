"""Configurable engine for static spreadsheets laid out as wide matrices.

Most static tables — and nearly all the high-value ones (census household /
income / core-need, mortgage delinquency) — are the same shape under a thin
disguise: a metadata header block, then a `Geography` row, then geographies
down the rows and either periods or categories across the columns. Multi-sheet
files carry one extra dimension in the *sheet name* (census year, tenure).

Rather than a parser per file, one engine reads a `MatrixSpec` — a small typed
declaration of where the header is, what the columns are, and what the sheet
name means. The spec stays declarative but, being Python, can hold a callable
when a file needs one. Specs live in `cmhc.static.specs`.

The engine reuses `cmhc.wide.wide_to_long` for the melt; only the static-specific
parts (sheet handling, metadata-header detection, period parsing, sentinels)
live here. It never touches the HMIP path.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from cmhc.static.catalogue import StaticTable
from cmhc.static.schema import COLUMNS, SOURCE
from cmhc.wide import wide_to_long

# Suppression / not-available sentinels seen across the static surface.
DEFAULT_SENTINELS = frozenset({"", "**", "n/a", "N/A", "..", "...", "-", "x", "[x]"})

_QUARTER_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}
_SHEETN = re.compile(r"(?i)^sheet\d*$")


@dataclass(frozen=True)
class Sheets:
    """Which sheets to read and what a sheet name contributes.

    mode="single": one sheet (by `name`, or the first sheet if None); the sheet
    name contributes nothing.
    mode="per_sheet": every data sheet; `dimension` says what the sheet name is
    ("period" or "category"). Notes/Sheet1-style sheets are skipped, as are
    period-dimension sheets whose name doesn't parse as a period.
    """

    mode: str
    name: str | None = None
    dimension: str | None = None
    skip: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MatrixSpec:
    """How to parse one static table into the shared long schema."""

    sheets: Sheets
    axis: str                            # what column headers are: "period" | "category"
    header_marker: str | None = None     # col-0 keyword marking the header row; else heuristic
    reliability: str | None = None       # substring identifying reliability columns (e.g. "Data quality")
    metric: str | None = None            # fixed category when axis="period" w/o a sheet category; None → file title
    sentinels: frozenset[str] = field(default=DEFAULT_SENTINELS)


def parse_period(s: str | None) -> date | None:
    """Static period strings → start-of-period date. None on anything else.

    Handles '2012Q3', '2012/Q3', bare '2012'. Start-of-period convention.
    """
    if not s:
        return None
    s = s.strip()
    if s.isdigit() and 1900 <= int(s) <= 2100:
        return date(int(s), 1, 1)
    m = re.fullmatch(r"(\d{4})\s*/?\s*Q([1-4])", s)
    if m:
        return date(int(m.group(1)), _QUARTER_MONTH[int(m.group(2))], 1)
    return None


def parse_value(s: str | None, sentinels: frozenset[str]) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s in sentinels:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read(path: Path, sheet: str | int) -> pl.DataFrame:
    return pl.read_excel(path, sheet_name=sheet if isinstance(sheet, str) else None,
                         sheet_id=None if isinstance(sheet, str) else sheet,
                         has_header=False, read_options={"dtypes": "string"})


def _iter_sheets(path: Path, sheets: Sheets) -> Iterator[tuple[str | None, pl.DataFrame]]:
    if sheets.mode == "single":
        yield None, _read(path, sheets.name if sheets.name else 1)
        return
    for name in fastexcel.read_excel(path).sheet_names:
        if name in sheets.skip or _SHEETN.match(name):
            continue
        if sheets.dimension == "period" and parse_period(name) is None:
            continue
        yield name, _read(path, name)


def _find_header(grid: pl.DataFrame, spec: MatrixSpec) -> int:
    col0 = grid[grid.columns[0]].to_list()
    if spec.header_marker:
        for i, v in enumerate(col0):
            if v and v.strip() == spec.header_marker:
                return i
        raise ValueError(f"header marker {spec.header_marker!r} not found")
    if spec.axis == "period":
        for i in range(grid.height):
            if sum(1 for c in grid.row(i)[1:] if parse_period(c) is not None) >= 2:
                return i
        raise ValueError("no period header row found")
    raise ValueError("axis='category' requires a header_marker")


def _keep_column(header: str | None, spec: MatrixSpec) -> bool:
    if header is None:
        return False
    if spec.reliability and spec.reliability.lower() in header.lower():
        return True
    return parse_period(header) is not None if spec.axis == "period" else bool(header.strip())


def _parse_sheet(grid: pl.DataFrame, sheet_name: str | None, spec: MatrixSpec,
                 table: StaticTable, metric: str | None) -> pl.DataFrame:
    header_idx = _find_header(grid, spec)
    header = grid.row(header_idx)
    first_col = grid.columns[0]

    # Map kept source columns → their header. Reliability columns get a unique
    # name (CMHC repeats 'Data quality [Note2]' verbatim across columns, which
    # would collide on rename) while staying matchable by the predicate below.
    keep: dict[str, str] = {}
    for i in range(1, len(header)):
        h = header[i]
        if not _keep_column(h, spec):
            continue
        if spec.reliability and spec.reliability.lower() in h.lower():
            keep[grid.columns[i]] = f"{spec.reliability} [col{i}]"
        else:
            keep[grid.columns[i]] = h
    if not keep:
        raise ValueError(f"no value columns found (axis={spec.axis})")
    body = grid.slice(header_idx + 1).select(first_col, *keep).rename({first_col: "geography"} | keep)

    long = wide_to_long(
        body, "geography",
        is_reliability=lambda c: bool(spec.reliability) and spec.reliability.lower() in c.lower(),
        parse_value=lambda s: parse_value(s, spec.sentinels),
    ).rename({"category": "key"})

    # Drop divider/footnote rows: a geography with no value anywhere in the sheet
    # (a suppressed-but-present geography keeps its null cells).
    long = long.filter(pl.col("value").is_not_null().any().over("geography"))

    sheet_period = parse_period(sheet_name) if spec.sheets.dimension == "period" else None
    sheet_category = sheet_name if spec.sheets.dimension == "category" else None
    if spec.axis == "period":
        long = long.with_columns(
            pl.col("key").map_elements(parse_period, return_dtype=pl.Date).alias("period"),
            pl.lit(sheet_category if sheet_category is not None else metric).alias("category"),
        )
    else:  # axis == "category"
        long = long.with_columns(
            pl.lit(sheet_period).cast(pl.Date).alias("period"),
            pl.col("key").alias("category"),
        )

    return long.with_columns(
        pl.col("geography").str.strip_chars(),
        pl.lit(None, dtype=pl.Utf8).alias("sub_geography"),
        pl.lit(table.survey).alias("survey"),
        pl.lit(table.table_id).alias("table_id"),
        pl.lit(SOURCE).alias("source"),
    ).select(COLUMNS)


def run(spec: MatrixSpec, table: StaticTable, path: Path) -> pl.DataFrame:
    """Parse the file at `path` per `spec`, stamping provenance from `table`."""
    # Category for a single-metric table (axis="period" with no sheet category):
    # the catalogue's page title is the reliable label — the sheet's first cell
    # is often a branding banner ('CANADIAN HOUSING OBSERVER'), not the metric.
    metric = spec.metric or table.title or ""
    frames: list[pl.DataFrame] = []
    for sheet_name, grid in _iter_sheets(path, spec.sheets):
        frames.append(_parse_sheet(grid, sheet_name, spec, table, metric))
    if not frames:
        raise ValueError(f"no data sheets parsed for {table.table_id}")
    return pl.concat(frames).select(COLUMNS)
