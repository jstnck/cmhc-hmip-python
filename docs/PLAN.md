# CMHC Data Portal — Project Plan

Companion to [RESEARCH.md](RESEARCH.md). Minimal shape. Add complexity only when a real problem demands it.

---

## The core model

Five things, in order:

1. **A catalogue** — Python dict / JSON mapping `(survey, series, dimension, breakdown)` → `TableId` plus the geographies and time grain it supports. The hard part; everything else is plumbing.
2. **A fetch function** — POST `ExportTable`, return CSV bytes.
3. **A loop** — walk catalogue × geography × time, write CSV per (table, geo) and an empty-marker on confirmed-empty responses.
4. **A tidy layer** — wide CSV → long parquet (one parquet per `table_id`), uniform 8-column schema. DuckDB queries the directory directly.
5. **A data mart** — single-file DuckDB extract for sharing with analysts who don't need to know the catalogue. Currently: Ontario rental (Rms + Srms). See [DATAMART.md](DATAMART.md).

That's the whole project.

---

## Stack

- **uv** for env / packaging
- **httpx** for HTTP
- **polars** for parsing (handles latin1, fast CSV)
- **DuckDB** for querying — reads parquet directly, no warehouse layer needed
- **pyarrow** for the polars → DuckDB bridge at mart-build time
- **Shiny for Python** + **ipyleaflet** for the local Ontario choropleth app

Nothing else until something breaks.

---

## Layout

```
cmhc_data_portal/
├── pyproject.toml
├── README.md                       # project overview, mountainMath credit
├── docs/                           # all project documentation
│   ├── PLAN.md                     # this file
│   ├── PROGRESS.md                 # what's built; what's queued; coverage tally
│   ├── DATA_DISCOVERY.md           # probe protocol + dated log of finds
│   ├── DATAMART.md                 # rental mart schema + usage
│   ├── INSTRUCTIONS.md             # project values; missing-data protocol
│   └── RESEARCH.md                 # scouting notes on CMHC's data surfaces + the mapping-framework eval
├── src/cmhc/
│   ├── catalogue.py                # (survey, series, …) → TableId map
│   ├── hmip.py                     # sync + async ExportTable client
│   ├── geographies.py              # Canada, provinces, CMAs, Ontario CSDs/CTs;
│   │                               #   plus normalize_name() for cross-source joins
│   ├── validity.py                 # which (table, geo) combos are worth fetching
│   ├── tidy.py                     # wide CSV → long polars DataFrame
│   ├── bulk.py                     # catalogue × geographies pull orchestration
│   └── config.py                   # shared paths and knobs
├── scripts/                        # thin entrypoints over src/cmhc/
│   ├── pull_canada_and_provinces.py
│   ├── pull_cmas.py
│   ├── pull_csds.py
│   ├── pull_cts.py                 # not yet run; ~230k requests, overnight
│   ├── extract_geo_lookups.py      # StatCan / mountainMath geo lookups → CSVs
│   ├── build_boundaries.py         # StatCan cartographic files → Ontario GeoJSONs
│   ├── build_parquet.py            # raw CSV tree → tidy parquet
│   ├── build_dmt_rental.py         # parquet → single-file DuckDB mart
│   ├── build_static_catalogue.py   # discover non-HMIP xlsx data tables
│   ├── probe_table.py              # diagnostic prober for stale catalogue filters
│   └── example_queries.py
├── app/
│   ├── shiny/                      # Ontario choropleth + charts (Shiny for Python)
│   └── reflex/                     # parallel Reflex prototype
├── tests/                          # pytest; catalogue, geographies, tidy, validity
├── notebooks/                      # ad-hoc analysis
└── data/
    ├── raw/                        # CSVs as fetched (gitignored)
    ├── clean/                      # parquet + boundary GeoJSONs (gitignored)
    ├── logs/                       # per-attempt JSONL run logs (gitignored)
    └── marts/                      # single-file DuckDB extracts (tracked)
```

Real logic lives in `src/cmhc/`; scripts are thin entrypoints that wire argparse to library calls. No staging tier between raw and clean, no `fetch/parse/transform/warehouse` decomposition, no plugin or pipeline framework.

---

## On headless browsers

HMIP stays browser-free — `ExportTable` is a plain POST returning CSV; Open Gov and static publications are direct downloads.

The one exception (added 2026-06-11): the **static-data-tables** surface. 74 of 136 leaf pages inject their xlsx download link via client-side JS — the asset URL is absent from the server HTML, so httpx can't see it (see DATA_DISCOVERY.md). `scripts/build_static_catalogue.py` now has an opt-in `--render` fallback (Playwright, isolated in the `scrape` dep group) that renders only those 0-asset pages. This is deliberately quarantined to the static-tables scraper — it does not touch the HMIP library (`src/cmhc/`) or the default install. Playwright is the floor; we do not add it anywhere else.

---

## Data flow

```
HMIP ExportTable POST
   → data/raw/{survey}/{table_id}/{geo}.csv   (or empty marker)
   → tidy() to polars (latin1)
   → data/clean/{survey}/{table_id}.parquet   (one parquet per logical table)

DuckDB ad-hoc:
   SELECT * FROM 'data/clean/Rms/*.parquet'

Sharable mart (analyst-facing):
   → data/marts/cmhc_rental.duckdb            (star + materialized metric tables)
```

Raw is kept so we can re-parse without re-fetching. Clean is what gets queried. The mart is a snapshot extract for handoff — every rebuild overwrites it.

---

## Sequencing

1. **Port the catalogue.** Translate mountainMath's `cmhc_tables.R` into `cmhc/catalogue.py`. ✅
2. **Write `hmip.fetch_table`.** ✅
3. **Pull RMS end-to-end** at Canada + provinces + Ontario CMAs + Ontario CSDs (CMA-member subset). ✅
4. **Discovery loop** — `probe_table.py` + `DATA_DISCOVERY.md` to find stale R-package entries. Recovered 9 RMS dimensions via bedroom-filter fix; recovered 5 CSDs via slash/hyphen name-format fix; ongoing. ✅ ongoing
5. **Tidy parquet archive** at the (period, geography, category) grain. ✅
6. **Other surveys** (Scss, Srms, Seniors, Census, Core Housing Need) at Canada + Ontario CMA scope. Srms expanded to 8 publishing Ontario CMAs (was 4) via stale-marker refresh. ✅
7. **Sharable data mart** — single-file DuckDB extract of Ontario rental, star schema + materialized metric tables. ✅ Coverage tracked in `_meta`; rebuild via `scripts/build_dmt_rental.py`.
8. **Ontario CT pull** — `scripts/pull_cts.py --surveys Rms,Srms`. ~230k requests, overnight at the safer concurrency. Unlocks neighbourhood-level rental. Queued.
9. **Static data tables** (`housing-data/data-tables/`). Most of this surface (mortgage delinquency, credit scores, demographic cuts of core housing need, Indigenous housing, long-range household projections) is not served by HMIP. Sequence: URL catalogue → xlsx download → per-table parsers. ✅ catalogue scraper built (`build_static_catalogue.py`, 136 leaf pages). ⏳ Render fallback added + validated on the delinquency page (2026-06-11); the 74-page `--render` pass to capture the JS-injected downloads is queued. Then xlsx parsers (mortgage delinquency first — confirmed, small). Needs `fastexcel` for polars xlsx reading (not yet a dep).
10. **Open Gov sweep** when convenient — easy, gives national-level cross-checks.
11. **Pair-level denylist** (`(table_id, geo)`) if the per-CMA HMIP 500 noise becomes painful as we expand provincial coverage.

Static publication PDFs (market reports): deferred — pre-formatted versions of data already in the structured surfaces.

---

## Things we are deliberately not doing

- No orchestrator (Dagster, Airflow, Prefect).
- No CLI framework (typer/click/fire) — stdlib `argparse` only, where useful.
- No validation framework (pandera) — per-script asserts if/when needed.
- No staging tier between raw and clean.
- No fetch manifest / audit log — the filesystem is the manifest (run logs in `data/logs/` are for analytics, not orchestration).
- No warehouse / views layer over the parquet — DuckDB queries it directly.
- No semantic layer or `metrics.yml` over the broader archive — the data mart serves that role for Ontario rental; other domains would warrant their own mart.
- No French endpoints, no live updates, no public API.
- No geography harmonization across census vintages — store the vintage on each row, document the limitation, leave reconciliation to the analyst. Concordance catalogue is a deliberate long-term track, not in the current scope.
- No public hosting of the Shiny app — local / single-user only, no auth, no concurrent users.

Revisit any of these when a concrete problem demands it. Not before.
