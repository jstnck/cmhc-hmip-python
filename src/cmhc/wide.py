"""Wide-matrix → long, the shape primitive shared by HMIP and static parsing.

Both CMHC's HMIP CSVs and its static spreadsheets are wide matrices: an index
column followed by value columns, each value column optionally trailed by a
reliability column. The two surfaces differ only in *how* a reliability column
is recognized (HMIP leaves its header blank; static names it 'Data quality')
and how a value cell is parsed — so those are injected, and the core
pair-walk + melt lives here once.

This is a pure shape transform: no period parsing, no geography logic, no
filtering of summary/divider rows. Callers layer those on top.
"""

from collections.abc import Callable

import polars as pl


def wide_to_long(
    df: pl.DataFrame,
    index_name: str,
    *,
    is_reliability: Callable[[str], bool],
    parse_value: Callable[[str], float | None],
) -> pl.DataFrame:
    """Melt a wide DataFrame into long `(index_name, category, value, reliability)`.

    The first column is the index (named `index_name`). Remaining columns are
    walked left to right: each non-reliability column is a value column, and a
    reliability column immediately following it carries that value's code.

        is_reliability  predicate on a column header — True for reliability columns
        parse_value     str → float | None for value cells (handles suppression)

    `value` is parsed via `parse_value`; `reliability` is whitespace-stripped and
    empty codes become null. No rows are dropped.
    """
    cols = df.columns[1:]
    pairs: list[tuple[str, str | None]] = []
    i = 0
    while i < len(cols):
        c = cols[i]
        if is_reliability(c):
            i += 1
            continue
        rel = cols[i + 1] if i + 1 < len(cols) and is_reliability(cols[i + 1]) else None
        pairs.append((c, rel))
        i += 1 if rel is None else 2

    parts: list[pl.DataFrame] = []
    for value_col, rel_col in pairs:
        parts.append(
            df.select(
                pl.col(index_name).str.strip_chars(),
                pl.lit(value_col.strip()).alias("category"),
                pl.col(value_col).map_elements(parse_value, return_dtype=pl.Float64).alias("value"),
                (pl.col(rel_col).str.strip_chars() if rel_col else pl.lit(None).cast(pl.Utf8)).alias("reliability"),
            )
        )

    out = pl.concat(parts)
    return out.with_columns(
        pl.when(pl.col("reliability") == "").then(None).otherwise(pl.col("reliability")).alias("reliability"),
    )
