"""Demonstrate querying the cleaned parquet data with DuckDB.

Run from project root:
    uv run python scripts/example_queries.py

Schema (every parquet has the same shape):
    period          date | null — start-of-period date. Populated for time-series
                                 (from the data column) AND for snapshots (from
                                 the CSV subtitle, e.g. 'October 2025 …'). Null
                                 only if HMIP's response had no parseable period.
    sub_geography   str  | null — sub-geography for Provinces/Centres/CSD/etc.
                                 breakdowns; null when the table is queried at
                                 the geo itself (no sub-geos to list).
    category        str
    value           f64  | null
    reliability     str  | null
    survey, table_id, geography  — metadata; geography = the geo we queried
"""

import duckdb


def main() -> None:
    con = duckdb.connect()
    con.execute("CREATE VIEW scss AS SELECT * FROM 'data/clean/Scss/*.parquet'")
    con.execute("CREATE VIEW rms AS SELECT * FROM 'data/clean/Rms/*.parquet'")

    print("=" * 60)
    print("Rows per survey:")
    print(con.execute("""
        SELECT 'scss' AS survey, COUNT(*) AS rows FROM scss
        UNION ALL
        SELECT 'rms', COUNT(*) FROM rms
    """).fetchdf())

    # --- Time series example -------------------------------------------------
    print("=" * 60)
    print("\nCanada apartment starts — annual totals from quarterly data:")
    # 5.7.2 = Scss Starts × Dwelling Type, "All areas" historical time series.
    # period is a Date (start-of-quarter, e.g. 1990/Q1 → 1990-01-01).
    print(con.execute("""
        SELECT extract(year FROM period) AS year, SUM(value)::INT AS apartment_starts
        FROM scss
        WHERE table_id = '5.7.2' AND category = 'Apartment'
        GROUP BY year
        ORDER BY year DESC
        LIMIT 8
    """).fetchdf())

    # --- Snapshot example: Provinces breakdown --------------------------------
    print("=" * 60)
    print("\nMost recent monthly starts by province (all dwelling types):")
    # 1.1.1.2 = Scss Starts Dwelling Type Provinces breakdown, queried @ Canada.
    # Each row is a province → sub_geography holds the province name.
    print(con.execute("""
        SELECT sub_geography AS province, category, value::INT AS starts
        FROM scss
        WHERE table_id = '1.1.1.2' AND category = 'All'
        ORDER BY starts DESC
    """).fetchdf())

    # --- RMS Centres breakdown @ a specific province --------------------------
    print("=" * 60)
    print("\nBC CMA vacancy rates — most recent reading:")
    # 2.1.1.2 = Rms Vacancy Rate Bedroom Type Centres breakdown.
    # Queried at province=BC, each row's sub_geography is a CMA within BC.
    print(con.execute("""
        SELECT sub_geography AS centre, value AS vacancy_pct, reliability
        FROM rms
        WHERE table_id = '2.1.1.2'
          AND geography = 'British Columbia'
          AND category = 'Total'
          AND value IS NOT NULL
        ORDER BY value DESC
        LIMIT 10
    """).fetchdf())

    # --- RMS Provinces breakdown @ Canada -------------------------------------
    print("=" * 60)
    print("\nVacancy rate by province (most recent reading, Total bedroom):")
    print(con.execute("""
        SELECT sub_geography AS province, value AS vacancy_pct, reliability
        FROM rms
        WHERE table_id = '2.1.1.1' AND category = 'Total'
        ORDER BY value DESC NULLS LAST
    """).fetchdf())

    # --- Reliability filter now works (whitespace bug fixed) -----------------
    print("=" * 60)
    print("\nCount of vacancy rate readings by reliability code:")
    print(con.execute("""
        SELECT reliability, COUNT(*) AS n
        FROM rms
        WHERE table_id = '2.1.1.2' AND value IS NOT NULL
        GROUP BY reliability
        ORDER BY n DESC
    """).fetchdf())


if __name__ == "__main__":
    main()
