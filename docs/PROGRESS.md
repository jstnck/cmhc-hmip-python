# Progress Summary

Snapshot of where the project stands. Companion to [RESEARCH.md](RESEARCH.md) (scouting) and [PLAN.md](PLAN.md) (design).

---

## What's built

End-to-end pipeline from HMIP endpoint → queryable parquet archive → portable analyst-facing data mart:

```
catalogue (285 entries)
   ↓
bulk_pull(geographies)  →  asyncio.Semaphore(5) → fetch_table_async()
   ↓                                                ↓
   ↓                                            data/raw/{survey}/{table_id}/{geo}.csv
   ↓                                                ↓
   ↓                                            data/logs/{label}_{ts}.jsonl  (one record / attempt)
   ↓
build_parquet  →  tidy()  →  data/clean/{survey}/{table_id}.parquet
   ↓
DuckDB ad-hoc (reads parquet directly — no warehouse layer)
   ↓
build_dmt_rental.py  →  data/marts/cmhc_rental.duckdb  (Ontario rental, star + materialized metrics)
```

### Modules (all in `src/cmhc/`)

| File | Purpose |
|---|---|
| `catalogue.py` | 285 `(survey, series, dimension, breakdown, geo_filter) → table_id` entries. Ported from mountainMath/cmhc's R package. |
| `geographies.py` | Canada + 13 provinces/territories + 153 CMAs + 574 Ontario CSDs (168 CMA-member subset) + 2,382 Ontario CTs. `Geography.geography_id` is a string (preserves leading zeros in METCODE and the dotted compound CT id). Also exports `normalize_name()` for slash/hyphen drift across StatCan ↔ CMHC name spaces. |
| `hmip.py` | Sync `fetch_table` + async `fetch_table_async` (shared `_build_form`, separate `httpx.Client` / `AsyncClient`). Exponential-backoff retries on 5xx + transport errors. `is_empty_response()` detects HMIP "No data available" / "archived" sentinels. |
| `tidy.py` | Wide CSV → long polars DataFrame. Handles reliability codes, suppression sentinels, snapshot vs time-series shapes. `_parse_period` converts `'Feb 1990'` / `'1990 March'` / `'1991/Q1'` to start-of-period `date`. Snapshot tables get their period from the CSV subtitle via `_extract_subtitle_period`. Single-geo query rows (empty first cell) preserved as `sub_geography=null`. |
| `validity.py` | `is_valid_for_geo(table, geo)` filters job lists for Canada/Province/CMA/CSD/CT geos (HMIP silently returns garbage for invalid combos). `BROKEN_TABLE_IDS` denylist for known-bad table_ids. |
| `bulk.py` | The async orchestrator. `bulk_pull(geographies, *, label, surveys=None, concurrency=None, refresh_empty_days=None)` walks catalogue × geos, filters via `is_valid_for_geo` (and optional survey allowlist), fetches in parallel under a semaphore, writes CSVs / empty markers, emits per-attempt JSONL log. |
| `config.py` | `PROJECT_ROOT`, `RAW_DIR`, `CLEAN_DIR`, `EMPTY_DIR`, `LOG_DIR`, `REQUEST_DELAY`, `CONCURRENCY`. |

### Scripts (thin entrypoints over `cmhc.*`)

| File | Purpose |
|---|---|
| `pull_canada_and_provinces.py` | Pull at Canada + provincial scope. |
| `pull_cmas.py` | `--province NAME` (filters by `cma_uid` prefix), `--surveys`, `--concurrency`, `--refresh-empty-days`. |
| `pull_csds.py` | Ontario CSDs. Defaults to ~168 CMA-member subset; `--all` for all ~574. Same `--surveys` / `--concurrency` / `--refresh-empty-days` flags. |
| `pull_cts.py` | Ontario CTs (~2,382). Same flags as `pull_csds.py` (no `--all`; CTs only exist inside CMAs). |
| `extract_geo_lookups.py` | One-shot: download `.rda` lookup tables from mountainMath/cmhc, write Ontario-filtered CSVs into `src/cmhc/data/`. Re-run when the R package updates. |
| `build_boundaries.py` | One-shot: download Statistics Canada 2021 cartographic boundary files (CSD + CT), filter to Ontario, reproject to WGS84, topology-simplify via `topojson` package, write GeoJSON to `data/clean/boundaries_*.geojson`. |
| `build_parquet.py` | Walk raw, tidy, concat by table_id, write parquet. Mtime-idempotent — full rebuild requires `rm -rf data/clean/` (needed when `tidy.py` schema changes). |
| `build_dmt_rental.py` | Tidy parquet → single-file DuckDB data mart for Ontario rental (Rms + Srms). Star schema + materialized metric tables. ~4 s build, ~17 MB output. See [DATAMART.md](DATAMART.md). |
| `example_queries.py` | DuckDB query demos against the cleaned parquet. |
| `build_static_catalogue.py` | Discover static-data-table `.xlsx` assets on cmhc-schl.gc.ca. |
| `probe_table.py` | Diagnostic single-table prober. `probe_table.py <table_id> --geo <name>` tries bare + catalogue + leave-one-out filter variants; identifies stale catalogue filters. See [DATA_DISCOVERY.md](DATA_DISCOVERY.md). |

### Geo lookups (`src/cmhc/data/`)

| File | Source | Rows |
|---|---|---|
| `cmas.csv` | `cmhc_cma_translation_data.rda` | 153 (Canada-wide) |
| `csds_ontario.csv` | `cmhc_csd_translation_data.rda` filtered to PR=35 | 574 |
| `csds_ontario_cma_members.csv` | `cmhc_csd_translation_data_2023.rda` filtered to PR=35, joined to name/type | 168 |
| `cts_ontario.csv` | `cmhc_ct_translation_data.rda` filtered to PR=35, with precomputed `GeographyId = METCODE + NBHDCODE + CMHC_CT` | 2,382 (15 source rows with whitespace-only CTUID dropped) |

### Geographic boundaries (`data/clean/`)

GeoJSON polygons from Statistics Canada 2021 Cartographic Boundary Files, simplified topologically (preserves shared edges) via the `topojson` package. Used for choropleth rendering.

| File | Features | Size | Join key |
|---|---|---|---|
| `boundaries_csd_ontario.geojson` | 577 CSDs (PR=35) | 1.9 MB | `CSDUID` (568 match `csds_ontario.csv`) |
| `boundaries_ct_ontario.geojson` | 2,533 CTs (PR=35) | 2.0 MB | `CTUID` (2,265 match `cts_ontario.csv`; mismatch is 2016 vs 2021 CT vintage) |

Raw zips cached at `data/raw/boundaries/` to avoid re-downloading (~50 MB). Build with `uv run python scripts/build_boundaries.py`.

### Tests

55 unit tests covering catalogue, geographies (incl. Ontario CSD + CT lookups), hmip, tidy (period parsing, snapshot CSV shape, subtitle period extraction, single-geo row preservation), validity. All green.

---

## Current data

**187 logical tables, 1,135,502 rows across 203 distinct geographies** (Canada + 13 provinces + 42 CMAs + 147 Ontario CSDs):

| Survey | Tables | Rows | Coverage |
|---|---|---|---|
| Rms | 121 | 569,455 | Vacancy / Availability / Rent / Universe — by Bedroom Type, Year of Construction, Structure Size, Rent Range, Rent Quartile, plus Summary Statistics. Canada + provinces + Ontario CMAs + Ontario CMA-member CSDs. Massive 2026-06-09 recovery: bedroom-filter + tidy fixes plus `--refresh-empty-days 0` re-pull at all sub-CMA levels recovered ~461k rows hidden by stale empty markers. |
| Scss | 34 | 548,165 | Starts, Completions, Intended Market, Unabsorbed Inventory — snapshot, Canada time-series, + Ontario CMAs |
| Census | 12 | 10,409 | Census-derived counts (Ontario CMAs) |
| Srms | 7 | 3,432 | Secondary Rental Market (condo / suite) — 8 Ontario CMAs that publish Srms: Barrie, Hamilton, Kitchener – Cambridge – Waterloo, London, Ottawa, St. Catharines – Niagara, Toronto, Windsor. The other 35 Ontario CMAs return empty (confirmed 2026-06-09). |
| Core Housing Need | 3 | 2,466 | Core housing need indicators — Ontario CMAs |
| Seniors | 10 | 1,575 | Seniors housing — Ontario CMAs (incl. snapshot-shape tables) |

**14,646 raw CSVs + 3,802 empty markers** in `data/raw/`. Empty markers record (table, geo) combos that HMIP confirmed have no data — saves us from re-fetching them. The marker count dropped from a 2026-05-23 high of ~5,200 as the 2026-06-09 CSD re-pull converted thousands of stale markers into real CSVs.

### Data mart

`data/marts/cmhc_rental.duckdb` — Ontario rental extract for analyst handoff. 540,993 observations, 14 metrics, 210 geographies (190 with data + 20 placeholders for fully-suppressed CSDs), 25 materialized metric tables, ~17 MB, ~4 s build. See [DATAMART.md](DATAMART.md). Rebuild: `uv run python scripts/build_dmt_rental.py`.

**Parquet schema (uniform):**
```
period        date | null  start-of-period date. Populated for time-series (from the
                           data column) and for snapshots (from the CSV subtitle, e.g.
                           'October 2025 …'). Null only when HMIP returned no parseable
                           period. NOTE: snapshot tables can carry per-row period
                           variance — HMIP returns each geo's most recent release, and
                           sparser geos may sit on older data.
sub_geography str  | null  Provinces / Centres / CSD / etc. breakdowns. Null when the
                           table is queried at the geo itself (no sub-geos to list).
category      str          dimension value: 'Apartment', 'Studio', '$1,000 - $1,249', …
value         f64  | null  suppressed → null
reliability   str  | null  'a' excellent → 'd' poor
survey, table_id, geography  metadata; geography = the geo we queried
```

---

## Validated sanity checks

| Query | Result | Matches reality |
|---|---|---|
| Canada apartment starts 2025 (annual, summed from 5.7.2 quarterly) | 94,796 | ✓ |
| Canada apartment starts 2018 → 2025 trend | 63,771 → 94,796 | ✓ |
| Vancouver CMA total vacancy 2025 | 3.7% reliability `a` | ✓ |
| Provincial vacancy ranking 2025 | Alberta 4.2% > BC 3.5% > ... | ✓ |
| March 2026 provincial starts | Alberta 1,169 leads | ✓ |
| Toronto total vacancy 2023 → 2025 | 1.4 → 2.5 → 3.0 | ✓ |

---

## Run logs

Every `bulk_pull` writes a JSONL manifest to `data/logs/{label}_{utc_timestamp}.jsonl`. One record per non-skipped attempt:

```json
{"ts":"2026-05-23T03:04:27Z","survey":"Rms","table_id":"2.1.3.1","geography":"Canada",
 "outcome":"ok","latency_s":0.233,"error_class":null,"error_msg":null}
```

Cross-run analysis via DuckDB:

```sql
-- Most-frequent errors by table
SELECT table_id, count(*) AS n
FROM 'data/logs/*.jsonl'
WHERE outcome = 'error' GROUP BY 1 ORDER BY 2 DESC;

-- Latency distribution
SELECT outcome, count(*) AS n, avg(latency_s) AS avg_s, max(latency_s) AS max_s
FROM 'data/logs/*.jsonl' GROUP BY 1;
```

---

## Known issues / open items

Discovery write-ups live in [DATA_DISCOVERY.md](DATA_DISCOVERY.md) — append-only log of catalogue / tidy bugs, with `scripts/probe_table.py` as the diagnostic tool. Recent finds: slash vs hyphen CSD-name drift (2026-06-10, 5 CSDs recovered); Srms publishes for 8 Ontario CMAs not 4 (2026-06-09, 4 CMAs recovered); RMS bedroom-filter bug (2026-05-23, 9 dimensions recovered); `tidy()` snapshot/single-geo row loss (2026-05-23).

| # | Issue | Severity | Notes |
|---|---|---|---|
| 1 | 2 endpoints return persistent 500s at every geography (`1.16.3.4`, `1.16.3.5`) | Low | Stale R catalogue entries; HMIP-side bug. Denylisted in `validity.BROKEN_TABLE_IDS`. |
| 1b | 12 Scss tables 500 for a *specific* set of 8 CMAs (per-province) | Low–Medium | See "HMIP 500s on specific (table, CMA) pairs" below. NOT denylisted — they work for ~35 other CMAs each. |
| 2 | ~~No CSD / CT / neighbourhood lookup~~ | RESOLVED for Ontario CSD + CT | Ported via `extract_geo_lookups.py`. Neighbourhoods + Survey Zones derivable from the CT crosswalk (columns present in `cts_ontario.csv`); not yet exposed as separate Geography sets. |
| 3 | French (`/fr/`) endpoints not pulled | Low | Possibly different metadata, possibly redundant. Defer. |
| 4 | No Open Government Portal sweep | Medium | ~37 CMHC datasets there in CSV/XML, easy to scrape, gives national-level cross-check data. |
| 5 | ~~Snapshot CSVs with no leading-comma header~~ | RESOLVED | `_tidy_snapshot()` fallback. |
| 6 | ~~RMS bedroom filter silently suppressing 9 dimensions~~ | RESOLVED 2026-05-23 | Dropped from `_RMS_SERIES`. Recovered Rent Ranges / Rent Quartiles / Year of Construction etc. — see DATA_DISCOVERY.md. |
| 7 | ~~`tidy()` losing snapshot period + single-geo rows~~ | RESOLVED 2026-05-23 | Added subtitle period extraction; preserve empty-index rows when no other rows exist. Recovered ~30k previously-silenced parquet rows. |
| 8 | ~~CSD/CT pulls done before issues 6+7 fixed → stale empty markers~~ | RESOLVED 2026-06-09 | `pull_csds.py --refresh-empty-days 0` rerun; CSD-level Rms now expanded from ~140k rows to ~440k. CT pull still pending (item 9). |
| 9 | ~~CMHC vs StatCan CSD-name slash/hyphen drift dropping 5 CSDs from mart~~ | RESOLVED 2026-06-10 | Added `cmhc.geographies.normalize_name()`. Mart now matches both forms. |
| 10 | Ontario CT pull never run | Medium | ~230k requests, overnight job at `--concurrency 3`. Biggest remaining Ontario-rental coverage gap. |

### HMIP 500s on specific (table, CMA) pairs

First full Ontario CMA pull (2026-05-22) surfaced **96 deterministic HTTP 500s** that survived the 3-retry/exponential-backoff:

- **12 Scss tables** affected (all `breakdown=Historical Time Periods`, `geo_filter=Default`):
  - `1.2.1, 1.2.2, 1.2.3, 1.2.4, 1.2.5, 1.2.6, 1.2.7, 1.2.8`
  - `1.9.3`
  - `1.16.1, 1.16.2, 1.16.5`
- **Same 8 CMAs** error across all 12 tables — alphabetically the first 8 Ontario CMAs:
  > Barrie, Belleville – Quinte West, Brantford, Guelph, Hamilton, Kingston, Kitchener – Cambridge – Waterloo, London
- Same 12 tables succeed (`ok`) for the other ~35 Ontario CMAs — so the tables themselves work.
- Cause unknown. Hypotheses: HMIP backend has per-(table, CMA) data gaps that error instead of returning empty; or these CMAs trigger a bug in HMIP's table-generation code. Worth re-checking on a future provincial pull to see if the same pattern holds in other provinces (which would suggest a position-in-batch artifact rather than CMA-specific data gaps).

We deliberately do **not** denylist these — denylisting at the `table_id` level would block ~315 valid datasets (35 working CMAs × 9 tables). A pair-level denylist (`set[(table_id, geo_name)]`) is the right tool if the noise becomes painful; deferred until we see whether the pattern is stable across provinces.

---

## Proposed next steps (in rough priority order)

### Immediate
1. **Ontario CT pull (Rms + Srms)** — `uv run python scripts/pull_cts.py --surveys Rms,Srms --concurrency 3`. ~230k requests, overnight at the safer rate. Unlocks neighbourhood-level rental. Biggest remaining Ontario-rental coverage gap.
2. **Refresh Census / Seniors / Core Housing Need at Ontario CMA** with `--refresh-empty-days 0`. The 2026-06-09 Srms recovery showed pre-fix empty markers were hiding 4 of 8 publishing CMAs; the same drift likely applies to these surveys.
3. **Pull remaining provinces' CMAs** — `pull_cmas.py --province NAME` for BC, Alberta, Quebec, etc. Each ~10 min at concurrency=5. Currently optional; widen scope only on explicit ask.

### Soon after
4. **Static data tables — render pass + first parsers.** The catalogue scraper (`build_static_catalogue.py`) is built, but 74 of 136 leaf pages inject their xlsx download via JS and show 0 assets to the httpx pass (see DATA_DISCOVERY.md 2026-06-11). A `--render` headless-browser fallback is added and validated on one page; **run it across all 74** to capture the missing downloads (`uv run --group scrape python scripts/build_static_catalogue.py --render`). Then write per-table xlsx parsers — **mortgage delinquency first** (confirmed: `…/mortgage-delinquency-rate-ca-prov-cmas-2012-q3-2025-q4-en.xlsx`, 41 KB, Canada/provinces/CMAs, Equifax-sourced). Add `fastexcel` to deps for polars xlsx reading. This unlocks the mortgage/debt + household-characteristics domains that HMIP does not serve.
5. **Catalogue probe sweep** — use `scripts/probe_table.py` against other dimensions with non-trivial filter sets. The bedroom-filter and slash/hyphen bugs almost certainly aren't the last stale entries.
6. **Open Government Portal sweep** — separate `pull_opengov.py`. Direct CSV downloads, gives independent cross-check data.
7. **Pair-level denylist** if the per-(table, CMA) 500 noise becomes painful across provinces (see issue 1b).
8. **Srms sub-CMA validity filter** — restrict to the 8 publishing Ontario CMAs (Barrie, Hamilton, Kitchener – Cambridge – Waterloo, London, Ottawa, St. Catharines – Niagara, Toronto, Windsor). Mirrors the 2026-05-23 non-CMA-CSD optimization. See DATA_DISCOVERY.md for context.

### Later
9. **Geography vintage tagging** — record census year per row to flag boundary changes (matters more once we have CT data spanning multiple census vintages).
10. **StatCan-sourced concordance catalogue** — vintage-tagged CSD → CMA / CD / Province crosswalk from StatCan SGC/GAF. Would replace the current ad-hoc lookups and let the mart populate `cma` for Census-Agglomeration CSDs that currently have `cma=NULL`. Multi-week effort; deferred.
11. **Neighbourhoods + Survey Zones** as first-class Geography sets (data already in `cts_ontario.csv` via NBHDCODE / ZONECODE columns).
12. **Other-domain data marts** — Scss (housing starts), Census, Core Housing Need each warrant their own DuckDB mart following the rental mart's pattern. On demand.
13. **Static publications crawler** — only if HMIP + Open Gov together leave gaps worth filling.

### Done since last revision
- ✅ Massive CSD-level Rms recovery (2026-06-09): re-pulled with `--refresh-empty-days 0`, +9,326 new (table, CSD) combos returning data, +461k rows in the parquet.
- ✅ Ontario rental data mart shipped (2026-06-10): `data/marts/cmhc_rental.duckdb`. See [DATAMART.md](DATAMART.md).
- ✅ Shiny for Python choropleth app (`app/shiny/`) — three pages live: CSD rent map, CMA rent map, charts.
- ✅ Srms 4 → 8 Ontario CMA recovery (2026-06-09).
- ✅ Slash/hyphen CSD name normalization (2026-06-10) — `cmhc.geographies.normalize_name`.

### Explicitly not doing
See [PLAN.md](PLAN.md) "Things we are deliberately not doing."
