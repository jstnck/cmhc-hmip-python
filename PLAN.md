# CMHC Data Portal — Project Plan

Companion to `RESEARCH.md`. Minimal shape. Add complexity only when a real problem demands it.

---

## The core model

Four things, in order:

1. **A catalogue** — Python dict / JSON mapping `(survey, series, dimension, breakdown)` → `TableId` plus the geographies and time grain it supports. This is the hard part; everything else is plumbing.
2. **A fetch function** — POST `ExportTable`, return a polars DataFrame.
3. **A loop** — walk catalogue × geography × time, write Parquet.
4. **DuckDB** — point at the Parquet directory, query.

That's the whole project.

---

## Stack

- **uv** for env / packaging
- **httpx** for HTTP
- **polars** for parsing (handles latin1, fast CSV)
- **DuckDB** for querying — reads Parquet directly, no warehouse layer needed

Nothing else until something breaks.

---

## Layout

```
cmhc_data_portal/
├── pyproject.toml
├── RESEARCH.md         # scouting notes on CMHC's data surfaces
├── PLAN.md             # this file
├── PROGRESS.md         # what's built; what's queued
├── DATA_DISCOVERY.md   # probe protocol + dated log of stale-catalogue finds
├── src/cmhc/
│   ├── catalogue.py    # (survey, series, ...) → TableId map
│   ├── hmip.py         # sync + async ExportTable client
│   ├── geographies.py  # Canada, provinces, CMAs, Ontario CSDs/CTs
│   ├── validity.py     # which (table, geo) combos are worth fetching
│   ├── tidy.py         # wide CSV → long polars DataFrame
│   ├── bulk.py         # catalogue × geographies pull orchestration
│   └── config.py       # shared paths and knobs
├── scripts/            # thin entrypoints: parse args, call into cmhc/
│   ├── pull_canada_and_provinces.py
│   ├── pull_cmas.py
│   ├── pull_csds.py
│   ├── pull_cts.py
│   ├── extract_geo_lookups.py
│   ├── build_boundaries.py
│   ├── build_parquet.py
│   ├── build_static_catalogue.py
│   ├── probe_table.py  # diagnostic prober for stale catalogue filters
│   └── example_queries.py
├── data/               # gitignored
│   ├── raw/            # CSVs as fetched (+ raw boundary zips)
│   └── clean/          # parquet (one file per table) + boundaries_*.geojson
└── notebooks/          # exploration, ad-hoc analysis
```

A small, neatly designed app. Real logic lives in `src/cmhc/`; scripts are thin entrypoints that wire argparse to library calls. No staging tier between raw and clean, no `fetch/parse/transform/warehouse` decomposition, no plugin or pipeline framework.

---

## On headless browsers

No. HMIP's `ExportTable` is a plain POST returning CSV. Open Gov and static publications are direct downloads. Add Playwright only if we hit a page that genuinely needs JS rendering.

---

## Data flow

```
HMIP ExportTable POST
   → data/raw/{survey}/{table_id}/{geo}_{time}.csv
   → parse to polars (latin1)
   → data/clean/{table_id}.parquet     (one parquet per logical table)

DuckDB:
   SELECT * FROM 'data/clean/*.parquet'
```

Raw is kept so we can re-parse without re-fetching. Clean is what gets queried.

---

## Sequencing

1. **Port the catalogue.** Translate mountainMath's `cmhc_tables.R` into `cmhc/catalogue.py`. ✅
2. **Write `hmip.fetch_table`.** ✅
3. **Pull RMS end-to-end** at Canada + provinces + Ontario CMAs into parquet. ✅
4. **Query it in a notebook.** Confirm it's useful before scaling. ✅
5. **Other surveys** (Scss, Srms, Seniors, Census, Core Housing Need) — done at Canada+province+Ontario CMA. CSD/CT pulls in progress; non-Ontario CMAs pending. Coverage tracked in PROGRESS.md.
6. **Discovery loop** — `scripts/probe_table.py` + `DATA_DISCOVERY.md` to find stale R-package catalogue entries. Already recovered 9 RMS dimensions (rent quartiles, rent ranges, year of construction, …) that the R port had silently broken via a wrong AppliedFilter. ✅ ongoing
7. **Static data tables** (`housing-data/data-tables/`). Most of this surface (mortgage delinquency, credit scores, core housing need by demographic cut, Indigenous housing, long-range household projections) is **not** served by HMIP. Sequence: build URL catalogue → pull xlsx files → per-table parsers.
8. **Open Gov sweep** when convenient — easy, gives national-level cross-checks.

Static publication PDFs (market reports): still deferred — mostly pre-formatted versions of data already in the structured surfaces.

---

## Things we are deliberately not doing

- No orchestrator (Dagster, Airflow, Prefect)
- No CLI framework (typer/click/fire) — stdlib `argparse` only, where useful
- No validation framework (pandera) — add per-script asserts if/when needed
- No staging tier between raw and clean
- No fetch manifest / audit log — the filesystem is the manifest
- No warehouse views layer — DuckDB queries Parquet directly
- No French endpoints, no live updates, no public API
- ~~No web UI~~ — superseded: building a Shiny for Python app for interactive Ontario choropleths. See `RESEARCH_MAPPING.md`. Scope stays local / single-user; no auth, no concurrent users, no deployment to a public host.
- No geography harmonization across census vintages — store the vintage on each row, document the limitation, leave reconciliation to the analyst

Revisit any of these when a concrete problem demands it. Not before.
