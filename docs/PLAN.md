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
│   ├── tidy.py                     # HMIP wide CSV → long polars DataFrame
│   ├── wide.py                     # pure wide-matrix → long primitive (shared by tidy + static)
│   ├── bulk.py                     # catalogue × geographies pull orchestration
│   ├── config.py                   # shared paths and knobs
│   └── static/                     # non-HMIP static-table harvest
│       ├── schema.py               # the shared long-format contract (COLUMNS, SOURCE)
│       ├── catalogue.py            # accessor over static_catalogue.json (provenance)
│       ├── matrix.py               # configurable engine: MatrixSpec + run() (uses wide.py)
│       ├── specs.py                # typed recipe registry: slug → MatrixSpec
│       └── runner.py               # parse(table_id, path): spec + provenance → long
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
│   ├── download_static.py          # fetch catalogued static assets → data/raw/static/
│   ├── build_static_parquet.py     # spec'd static files → data/clean/static/ parquet
│   ├── probe_table.py              # diagnostic prober for stale catalogue filters
│   └── example_queries.py
├── app/
│   ├── shiny/                      # Ontario choropleth + charts (Shiny for Python)
│   └── reflex/                     # parallel Reflex prototype
├── tests/                          # pytest; catalogue, geographies, tidy, validity, static matrix engine
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

## Static data tables: separate harvest, shared schema

The static-data-tables surface (`housing-data/data-tables/`) is a second data source, kept structurally separate from HMIP but converging at the schema layer. The governing principle:

**Separate at acquisition, converge at the mart.** HMIP is a *parameterized API client* — catalogue × geography → one uniform CSV shape → one `tidy()`. The static tables are a *file harvester* over heterogeneous bespoke xlsx/xls, where each table family needs its own hand-written parser. These two acquisition paths share almost nothing and must not share code: no xlsx logic leaks into `tidy.py`, no Playwright touches `src/cmhc/`'s default path. The static side lives in its own namespace (`src/cmhc/static/`).

Where they *do* meet is the **output contract**, not the pipeline. The contract lives in `cmhc.static.schema.COLUMNS` — HMIP's long schema plus a `source` column:

```
period, sub_geography, category, value, reliability, survey, table_id, geography, source
```

Every static table resolves to exactly these columns. With the contract fixed up front, the mart builder becomes source-agnostic: it unions HMIP parquet and static parquet and joins geography the same way for both.

**One engine, not N parsers.** Most static tables are the same wide matrix under a thin disguise: a metadata header block, a `Geography` row, geographies down the rows, and periods *or* categories across the columns — with multi-sheet files carrying one extra dimension in the *sheet name* (census year, tenure). So instead of a parser per file, one engine (`cmhc.static.matrix`) reads a typed `MatrixSpec` per table. The spec declares only what varies: sheet handling (single / per-sheet + what the sheet name means), the column axis (`period` | `category`), the header marker, the reliability-column marker, and the sentinel set. The engine reuses `cmhc.wide.wide_to_long` (the same primitive `tidy()` uses) for the melt; only static-specific concerns (sheet iteration, metadata-header detection, period parsing, sentinels) live in `matrix`.

Design consequences:

- **The cost is the layout tail, not scraping.** The clean 2-D tables fall straight into the engine. The work is the heterogeneous tail — and a chunk of it is genuinely **multi-dimensional** (a leading Tenure / Age group / Quintile column on top of geography × period). The flat engine cannot represent that extra dimension and would silently mangle it (see the correctness gate below). Those await a multi-dimension engine mode; until then they stay out of `specs.py`.
- **Recipes in Python, facts in JSON.** `specs.py` is a typed registry (validated at import, can hold a callable); `static_catalogue.json` stays pure scraped data. Two different "mappings": provenance (JSON) vs parse-recipe (code).
- **Trust a spec only after a correctness gate.** "Parses without error" badly overcounts — a flat parse of a multi-dimensional table raises nothing but is wrong. Before a table earns a spec, screen the output for (a) any all-null category (a text dimension column got melted) and (b) duplicate `(geography, period, category)` keys (a leading dimension column got dropped, collapsing rows). Only tables that pass go in `specs.py`.
- **Provenance comes from the catalogue, never hardcoded.** `cmhc.static.catalogue` is the single source of truth: page slug → `table_id`, section → `survey` label, plus asset URL / size / last-modified and the page **title** (used as the metric/`category` for single-metric tables — the sheet's first cell is often a branding banner, not the metric).
- **Geography join is the merge surface.** Parsers store geography names verbatim; reconciliation to the HMIP geography set is a mart-build concern via `normalize_name()`, extended for static-specific drift (em-dash spacing, `Newfoundland` → `Newfoundland and Labrador`). Static files aren't even internally consistent on names, so this is real work — but smaller than HMIP's (mostly Canada/Province/CMA, no CSD/CT).
- **One merged mart, not two reconciled later.** Every spec'd table is born mart-compatible; we never build a separate static mart and reconcile divergent schemas after the fact.
- **`fastexcel`** provides polars xlsx/xls reading (calamine backend; note the one legacy SpreadsheetML-2003 XML file it can't open). `xlsxwriter` (dev only) builds test fixtures. Neither affects the HMIP path.

---

## Data flow

```
HMIP ExportTable POST
   → data/raw/{survey}/{table_id}/{geo}.csv   (or empty marker)
   → tidy() to polars (latin1)
   → data/clean/{survey}/{table_id}.parquet   (one parquet per logical table)

DuckDB ad-hoc:
   SELECT * FROM 'data/clean/Rms/*.parquet'

Static data tables (parallel source):
   build_static_catalogue.py (+--render) → data/static_catalogue.json   (asset URLs)
   download_static.py        → data/raw/static/{section}/{file}.xlsx
   build_static_parquet.py   → matrix engine (spec + provenance) → SAME long schema (source='static')
   → data/clean/static/{table_id}.parquet                              (one per spec'd table)

Sharable mart (analyst-facing):
   → data/marts/cmhc_rental.duckdb            (star + materialized metric tables)
   → source-agnostic mart builder unions HMIP + static parquet, joins geography
     via normalize_name() for both
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
9. **Static data tables** (`housing-data/data-tables/`). Most of this surface (mortgage delinquency, credit scores, demographic cuts of core housing need, Indigenous housing, long-range household projections) is not served by HMIP. Separate harvest, shared schema — see "Static data tables: separate harvest, shared schema" above. Sequence:
   1. **Complete the inventory.** Run the `--render` pass to capture the JS-injected downloads, so the full set of files-to-parse is visible. ✅ done 2026-06-13 — 128 of 136 pages now have a captured asset URL; the 8 gaps are absorption tables that overlap HMIP's Scss (see DATA_DISCOVERY.md).
   2. **Fix the shared long-format contract + stand up `src/cmhc/static/`.** ✅ `schema.py` (contract), `catalogue.py` (provenance accessor over `static_catalogue.json`), `fastexcel` added.
   3. **Download + engine + parquet build.** ✅ `download_static.py` (all 128 assets on disk); the `matrix` engine + `specs` registry + `wide.py` shared primitive; `build_static_parquet.py` (idempotent) → `data/clean/static/`.
   4. **Spec the high-value tables, screened by the correctness gate.** ✅ 18 tables spec'd (mortgage delinquency + 17 household-characteristics), ~19k rows. ⏳ remaining: mortgage-and-debt + rental-market uniques next; then a **multi-dimension engine mode** to unlock the ~20 multi-dim household-characteristics tables (Tenure/Age/Quintile breakdowns) currently deferred.
   5. **Source-agnostic mart builder.** ⏳ union `data/clean/` (HMIP) + `data/clean/static/`, reconcile geography via `normalize_name()`.
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
