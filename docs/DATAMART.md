# CMHC Rental Data Mart

A single-file DuckDB extract of CMHC rental market data for Ontario. Built from the project's parquet archive (`data/clean/Rms/*.parquet`, `data/clean/Srms/*.parquet`) into one portable `.duckdb` file that an analyst can query directly with SQL.

> Looking for "how do I query X"? See the [DATAMART_GUIDE.md](../data/marts/DATAMART_GUIDE.md) —
> an ERD plus an example-query cookbook. This doc is the schema/conventions reference.

This doc covers:
- What's in the file and how it's structured
- Column conventions the analyst needs to know
- How to query it (worked examples)
- What's deliberately excluded
- How the file is rebuilt and refreshed

---

## Scope

**Sources:** two surveys.

| Survey | What it covers |
|---|---|
| Rms (Primary Rental Market Survey) | Purpose-built rental — vacancy, availability, rent, universe, by bedroom type / year of construction / structure size / rent range / rent quartile |
| Srms (Secondary Rental Market Survey) | Condo and secondary suites — vacancy, average rent, universe |

**Geographic coverage:** Ontario only.

- Ontario province. Vacancy Rate carries the full 1990–2025 October history across all five dimensions (Bedroom Type, Year of Construction, Structure Size, Rent Ranges, and Rent Quartiles from 2012). The other six Rms metrics are still snapshot-only at this level — see "Known limits"
- 42 Ontario CMAs in the data (8 of them publish Srms; the rest are Rms-only)
- 147 Ontario CSDs with at least one published value, plus 20 placeholder rows for CMA-member CSDs CMHC publishes nothing for
- Of those 147, 22 belong to Census Agglomerations rather than CMAs — their CMHC publication is at the CSD level but they have no parent CMA, so these rows carry `cma = NULL` (see column conventions). They are a subset of the 147, not additional to it.
- **1,589 Census Tracts** with at least one published value (Rms only — CT-level Srms and the derived Rms series rent-range/rent-quartile vacancy, rent change, summary stats don't publish at tract granularity). Each CT rolls up to its parent CMA and CSD (`cma`, `cma_uid`, `csduid` populated). CT rental is heavily confidentiality-suppressed — ~38% of CT cells carry a value, the rest are `**`. CTs that returned no data at all are not in the mart (no placeholder rows at CT level).

Total: **1,799 geographies** — 1 province + 42 CMAs + 147 CSDs with data + 20 placeholder CSDs + 1,589 CTs.

**Out of scope:** Canada and other provinces are dropped to keep the mart Ontario-focused; query the project's full parquet archive directly if you need national comparison.

---

## Layered design

Two layers, both in the same DuckDB file:

1. **Star core** — long fact + small dimension tables. Full flexibility for any question, including ones we didn't pre-bake a metric table for.
2. **Materialized metric tables** — denormalized projections of the star, one per (series × dimension). Zero joins, tab-completable table names. The 80% case for analysts.

Pick whichever layer fits the question. Both reference the same underlying rows.

---

## Star core schema

### `rental_observations` (fact)

One row per (metric, geography, period, dimension, category) cell.

| column | type | notes |
|---|---|---|
| `metric_id` | SMALLINT | FK → `metrics` |
| `geo_id` | VARCHAR | FK → `geographies` |
| `period` | DATE | Start-of-period date |
| `dimension` | VARCHAR | `'Bedroom Type'`, `'Year of Construction'`, `'Structure Size'`, `'Rent Ranges'`, `'Rent Quartiles'`, `'Dwelling Type'` |
| `category` | VARCHAR | Value within the dimension — e.g. `'2 Bedroom'`, `'Before 1960'`, `'Total'` |
| `value` | DOUBLE | NULL when `is_suppressed` is TRUE |
| `reliability` | VARCHAR | `'a'` (excellent) → `'d'` (poor); `'n/a'` when the table carries no reliability info (e.g. universe counts, rent change); NULL **only** when suppressed |
| `is_suppressed` | BOOLEAN | TRUE when CMHC withheld for confidentiality (`**` in raw). Invariant: `reliability IS NULL` ⇔ `is_suppressed` |
| `is_canonical` | BOOLEAN | TRUE for exactly one row per `(metric_id, geo_id, period, dimension, category)`. CMHC publishes the same value through several `table_id` paths; the star keeps them all, this flags the canonical one. **Filter `WHERE is_canonical` before any SUM/COUNT.** The materialized metric tables are already deduplicated to canonical rows. See "Duplicate rows" below |
| `source_survey` | VARCHAR | `'Rms'` or `'Srms'` |
| `table_id` | VARCHAR | CMHC coordinate — e.g. `'2.1.1.2'`. You don't need it for queries; it's there for HMIP cross-reference |
| `updated_at` | TIMESTAMP WITH TIME ZONE | mtime of the source parquet; same value for every row sharing a `table_id` |

### `metrics`

The metric inventory. `SELECT * FROM metrics` is the catalogue.

| column | type | notes |
|---|---|---|
| `metric_id` | BIGINT | PK |
| `metric_name` | VARCHAR | `'Vacancy Rate'`, `'Average Rent'`, `'Condo Vacancy Rate'`, … |
| `market` | VARCHAR | `'Primary'` (Rms) or `'Secondary'` (Srms) |
| `source_survey` | VARCHAR | `'Rms'` or `'Srms'` |
| `unit` | VARCHAR | `'%'`, `'$'`, `'units'`, `'ratio'` |
| `description` | VARCHAR | One-line definition |
| `source_table_ids` | VARCHAR | Comma-separated HMIP table_ids that feed this metric |

### `geographies`

| column | type | notes |
|---|---|---|
| `geo_id` | VARCHAR | PK — canonical ID (`CT:<GeographyId>`, `CSD:<CSDUID>`, `CMA:<CMA_UID>`, or `'ON'`). CT uses the unique CMHC GeographyId, not CTUID, since 6 CTUIDs split across two CSDs |
| `geo_name` | VARCHAR | Display name. Normalized to HMIP's hyphen form (`Guelph-Eramosa (TP)`, never `Guelph/Eramosa (TP)`) — see `cmhc.geographies.normalize_name`. CTs are `CT <CTUID> (<CSDNAME>)` |
| `geo_level` | VARCHAR | `'Province'`, `'CMA'`, `'CSD'`, `'CT'` |
| `province` | VARCHAR | Always `'Ontario'` |
| `cma` | VARCHAR | Parent CMA. **Populated only when the CSD is a member of a StatCan CMA.** ~22 Ontario CSDs publish rental data via CMHC but sit in Census Agglomerations (not CMAs) — those rows have `cma = NULL`. CMA rows have `cma = geo_name`; CT rows carry their parent CMA (all CTs sit inside a CMA); the Province row has `cma = NULL` |
| `csduid` | VARCHAR | StatCan CSDUID. The CSD's own UID for CSD rows; the **parent** CSDUID for CT rows; NULL for CMA/Province |
| `cma_uid` | VARCHAR | StatCan CMA UID for CMA rows + CSD/CT rows whose parent CMA is in StatCan's hierarchy |
| `has_data` | BOOLEAN | TRUE if the geography has any rows in `rental_observations`. FALSE for **placeholder** rows added for CMA-member Ontario CSDs that CMHC withholds entirely (no rental data published at any breakdown). See "Placeholder rows" below |

### `dimension_values`

Sort-order and label lookup for the long fact's `(dimension, category)` pairs.

### `_meta`

Single-row table with build provenance.

| column | type | notes |
|---|---|---|
| `built_at_utc` | TIMESTAMP | When `build_dmt_rental.py` ran |
| `source_parquet_newest` | TIMESTAMP | Newest source-parquet mtime — the freshest possible data in this file |
| `portal_commit` | VARCHAR | git rev of the portal repo at build time |
| `n_observations` | BIGINT | Row count in `rental_observations` (all publication paths) |
| `n_canonical` | BIGINT | Rows with `is_canonical = TRUE` — one per logical observation; the rest are duplicate publication paths |
| `n_suppressed` | BIGINT | Count of rows with `is_suppressed = TRUE` |
| `n_cma` | BIGINT | Distinct Ontario CMAs present in `geographies` |
| `n_csd_with_data` | BIGINT | Ontario CSDs with at least one observation |
| `n_csd_no_data` | BIGINT | Placeholder Ontario CSDs (CMA-members CMHC publishes nothing for) |
| `n_ct` | BIGINT | Ontario Census Tracts with data (Rms only) |
| `coverage_summary` | VARCHAR | Human-readable scope statement |

---

## Materialized metric tables

Each table is a flat denormalization of the star — geographies joined in, the dimension column renamed to its concrete meaning, the value column renamed to the metric. No SQL joins required.

Common column shape (illustrated for `average_rent_by_bedroom`):

| column | type |
|---|---|
| `geo_level`, `geo_name`, `province`, `cma` | VARCHAR (geography, pre-joined) |
| `period`, `period_year` | DATE, SMALLINT |
| `bedroom_type` | VARCHAR (the metric's dimension, renamed) |
| `sort_order` | SMALLINT (display order for the dimension — `ORDER BY sort_order` for logical, not alphabetical, output) |
| `avg_rent_dollars` (or `vacancy_pct`, etc.) | DOUBLE (the value, renamed + unit-suffixed) |
| `reliability` | VARCHAR (`'a'`–`'d'`, or `'n/a'` when no reliability info) |
| `is_suppressed` | BOOLEAN |
| `source_survey`, `table_id`, `updated_at` | VARCHAR, VARCHAR, TIMESTAMP |

Rows are already deduplicated to one canonical path per logical cell (the star's `WHERE is_canonical`), so SUM/COUNT/AVG over these tables are safe without a window function.

**Table list** (25 materialized metric tables; authoritative source is `SHOW TABLES` + `SELECT * FROM metrics`):

Rms (18):
- `vacancy_rate_by_bedroom`
- `vacancy_rate_by_year_of_construction`
- `vacancy_rate_by_structure_size`
- `vacancy_rate_by_rent_range`
- `vacancy_rate_by_rent_quartile`
- `availability_rate_by_bedroom`
- `availability_rate_by_year_of_construction`
- `availability_rate_by_structure_size`
- `average_rent_by_bedroom`
- `average_rent_by_year_of_construction`
- `average_rent_by_structure_size`
- `average_rent_change_by_bedroom`
- `median_rent_by_bedroom`
- `median_rent_by_year_of_construction`
- `median_rent_by_structure_size`
- `rental_universe_by_bedroom`
- `rental_universe_by_year_of_construction`
- `rental_universe_by_structure_size`

Srms (7):
- `condo_vacancy_rate_by_structure_size`
- `condo_average_rent_by_bedroom`
- `condo_universe_by_structure_size`
- `rental_condo_universe_by_structure_size`
- `percent_condo_used_as_rental_by_structure_size`
- `other_secondary_rental_universe_by_dwelling_type`
- `other_secondary_rental_average_rent_by_dwelling_type`

---

## Column conventions

A handful of decisions are baked into every row. Worth knowing before writing queries.

**Suppression.** `is_suppressed = TRUE` when CMHC withheld the cell for confidentiality (raw `**`). Detected as `value IS NULL AND reliability IS NULL` — both fields go null together in the suppression case. Other nulls (rare) get `is_suppressed = FALSE`. Always check `is_suppressed` before treating a missing value as zero or interpolating; CMHC suppression is concentrated in small CSDs and tracts, and would bias any aggregate computed without awareness of it. Since CT data was added, suppressed cells are a *majority* of the fact table — CT rental is heavily withheld for confidentiality — so this is not an edge case. See `_meta.n_suppressed` / `_meta.n_observations` for the live counts rather than assuming a figure.

**Reliability codes.** `'a'` (excellent) → `'d'` (poor), based on CMHC's published reliability framework. Filter `WHERE reliability IN ('a','b')` for higher-confidence analyses. Tables that don't carry reliability at all (universe counts, average rent change) use the sentinel `'n/a'`; `NULL` reliability now means **suppressed only** (`reliability IS NULL` ⇔ `is_suppressed`). This split matters: before it existed, `WHERE reliability IN ('a','b')` silently discarded ~335k present-but-unrated values that looked indistinguishable from suppressed ones.

**Period.** Start-of-period date. RMS readings are annually surveyed in October (most rows are dated Oct 1); SRMS is published per release. Use `period_year` on the metric tables for groupings — already extracted.

**Geography parents.** `province` and `cma` are precomputed on every row so you can filter without joining. For a CMA row, `cma` equals its own name. For a CSD row, `cma` is the parent CMA. `province` is always `'Ontario'`.

**`table_id`.** The original CMHC coordinate — e.g. `'2.1.13.2'` for Avg Rent by Bedroom Type, CMA breakdown. You don't need it for queries; it's there if you want to cross-reference HMIP's web view (e.g. `https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/Table?TableId=2.1.13.2&GeographyId=35&GeographyTypeId=2`).

**`updated_at`.** mtime of the source parquet — i.e. when `build_parquet.py` last rebuilt that specific table from raw CSVs. All rows sharing a `table_id` share an `updated_at`. CMHC publishes Rms annually (≈ November release) and Srms quarterly, so expect this to be reasonably old for most rows most of the time.

**Geography name normalization.** StatCan's reference data uses forward-slash compound names (`Guelph/Eramosa`, `Greater Sudbury / Grand Sudbury`); CMHC's HMIP returns the hyphen form (`Guelph-Eramosa`, `Greater Sudbury - Grand Sudbury`). The mart canonicalizes on the hyphen form via `cmhc.geographies.normalize_name`. If you join external StatCan data against this mart on `geo_name`, normalize the StatCan side first. The full `csduid` / `cma_uid` are the safer join keys.

**Duplicate rows per logical observation.** A single CMHC measurement can land in the star multiple times. CMHC publishes the same value through several `table_id` paths — e.g., the 2025 Toronto 2-bedroom average rent appears in `2.1.11.2` (Ontario province queried at CMA breakdown), `2.2.11` (Toronto queried as a time series), and similar combinations. Up to 5× duplication occurs, **concentrated in the recent cross-sectional data analysts query most** (the latest snapshot is published through every path; deep history through only one). Each path is a separate row in `rental_observations` with the same `geo_name`, `period`, `dimension`, `category`, `value`, and `reliability` — but a different `table_id`.

This is handled for you:

- **The materialized metric tables are deduplicated** — they select the star's canonical row, so they hold exactly one row per logical cell. SUM/COUNT/AVG over them are correct, no window function needed.
- **The star (`rental_observations`) keeps every path** for full provenance, and flags the canonical one with `is_canonical`. When querying the star, add `WHERE is_canonical` before any SUM/COUNT (AVG/MIN/MAX over identical values are unaffected). Example:

```sql
SELECT geo_name, period, value
FROM   rental_observations o
JOIN   geographies g USING (geo_id)
JOIN   metrics     m USING (metric_id)
WHERE  m.metric_name = 'Average Rent' AND o.dimension = 'Bedroom Type'
  AND  o.category = '2 Bedroom'
  AND  o.is_canonical;
```

Canonical choice: one row per `(metric_id, geo_id, period, dimension, category)`, preferring the Historical Time Periods (time-series) `table_id` for uniform provenance across a series, then the smallest `table_id`. `_meta.n_canonical` vs `_meta.n_observations` reports how many rows the flag selects.

### Placeholder rows in `geographies`

The mart includes rows in `geographies` for Ontario CSDs that are members of an Ontario CMA but for which CMHC publishes zero rental data — every (table, CSD) request returned "No data available." These are typically small townships where CMHC's sample size is below the publication threshold for every metric.

| Property | Value |
|---|---|
| `has_data` | `FALSE` |
| Observations in `rental_observations` | None — the `geo_id` doesn't appear in the fact |
| `csduid`, `cma`, `cma_uid` | All populated from StatCan reference |
| Materialized metric tables | None — they JOIN through `rental_observations` so placeholders don't appear |

The point of including them: an analyst asking "which CMA-member CSDs is rental data published for?" sees the complete universe rather than discovering 20+ CSDs are missing only by counting against an external reference. Filter via `WHERE has_data` to exclude.

---

## Example queries

```sql
-- 1. Latest 2BR rent by Ontario CMA
SELECT geo_name, period, avg_rent_dollars, reliability
FROM   average_rent_by_bedroom
WHERE  geo_level    = 'CMA'
  AND  bedroom_type = '2 Bedroom'
  AND  period_year  = 2025
ORDER BY avg_rent_dollars DESC;

-- 2. Toronto vacancy trend (Total bedrooms)
SELECT period, vacancy_pct, reliability, is_suppressed
FROM   vacancy_rate_by_bedroom
WHERE  geo_name     = 'Toronto'
  AND  bedroom_type = 'Total'
ORDER BY period;

-- 3. Where is data suppressed? (sanity check before aggregating)
SELECT geo_name, period, bedroom_type
FROM   average_rent_by_bedroom
WHERE  is_suppressed
  AND  geo_level = 'CSD'
LIMIT 100;

-- 4. High-confidence only
SELECT geo_name, avg_rent_dollars
FROM   average_rent_by_bedroom
WHERE  geo_level    = 'CSD'
  AND  bedroom_type = '2 Bedroom'
  AND  period_year  = 2025
  AND  reliability IN ('a','b');

-- 5. Cross-reference the underlying HMIP table
SELECT DISTINCT table_id, source_survey, updated_at
FROM   average_rent_by_bedroom
WHERE  geo_name = 'Toronto';

-- 6. Primary vs Secondary rental in Toronto, 2BR
SELECT r.period,
       r.avg_rent_dollars AS primary_2br,
       s.condo_avg_rent_dollars AS condo_2br
FROM       average_rent_by_bedroom         r
LEFT JOIN  condo_average_rent_by_bedroom   s
       ON  s.geo_name     = r.geo_name
      AND  s.period       = r.period
      AND  s.bedroom_type = r.bedroom_type
WHERE  r.geo_name     = 'Toronto'
  AND  r.bedroom_type = '2 Bedroom'
ORDER BY r.period;

-- 7. Census-tract rents within a CMA (high-confidence only)
--    geo_level = 'CT' and the cma rollup column scope it to one metro.
--    CT rental is heavily suppressed, so filter on reliability before ranking.
SELECT geo_name, avg_rent_dollars, reliability
FROM   average_rent_by_bedroom
WHERE  geo_level    = 'CT'
  AND  cma          = 'Toronto'
  AND  bedroom_type = '2 Bedroom'
  AND  period_year  = 2025
  AND  reliability IN ('a','b')
ORDER BY avg_rent_dollars DESC;

-- 8. Browse the metric catalogue
SELECT metric_name, market, unit, description
FROM   metrics
ORDER BY market, metric_name;

-- 9. What's the most recent data in this file?
SELECT MAX(updated_at) AS freshest, MIN(updated_at) AS stalest
FROM   rental_observations;
```

---

## Querying via the star (when the metric tables aren't enough)

The materialized metric tables cover the obvious cross-sections. For anything else (e.g. combining multiple metrics, building an unusual pivot, joining against external geography data), drop to the star:

```sql
SELECT g.geo_name, o.period, m.metric_name, o.category, o.value
FROM   rental_observations o
JOIN   metrics      m USING (metric_id)
JOIN   geographies  g USING (geo_id)
WHERE  g.province = 'Ontario'
  AND  m.metric_name IN ('Vacancy Rate', 'Average Rent')
  AND  o.dimension = 'Bedroom Type'
  AND  o.category  = '2 Bedroom'
  AND  o.period_year = 2025;
```

The metric tables are just pre-baked combinations of these three tables. Anything they can do, the star can do; the star will do more, at the cost of one or two more joins.

---

## What's NOT in the file

- **Other surveys.** Census, Scss (starts and completions), Seniors housing, Core Housing Need — different domain, would warrant a separate mart.
- **Canada or non-Ontario rows.** This mart is scoped to Ontario. Use the parquet archive directly for cross-province work.
- **Geographic boundary polygons.** Use `data/clean/boundaries_*.geojson` from the parent project and join on `csduid` / `cma_uid`.
- **Census-vintage harmonization.** Boundaries change between censuses (2016 vs 2021 CSD/CT definitions). The mart preserves whatever CMHC published — boundary reconciliation across vintages is the analyst's job.
- **Non-CMHC data.** The Ontario MMAH Affordable Residential Units Bulletin (referenced in `DATA_DISCOVERY.md`) is not folded in. CMHC-only by design.
- **Forecasts or model outputs.** Source data only.

---

## Rebuilding

```bash
uv run python scripts/build_dmt_rental.py
```

Reads from `data/clean/Rms/*.parquet` + `data/clean/Srms/*.parquet`. Writes to `data/marts/cmhc_rental.duckdb`. Idempotent — re-run anytime; no HMIP traffic. The script overwrites the previous file.

Typical full refresh sequence when CMHC publishes new data:

```bash
# 1. Pull fresh CSVs from HMIP (only re-fetches what's missing / expired)
uv run python scripts/pull_canada_and_provinces.py --surveys Rms,Srms
uv run python scripts/pull_cmas.py            --province Ontario --surveys Rms,Srms
uv run python scripts/pull_csds.py            --surveys Rms

# 2. Rebuild parquet (mtimes here populate per-row `updated_at`)
uv run python scripts/build_parquet.py

# 3. Rebuild mart
uv run python scripts/build_dmt_rental.py
```

After the rebuild, `_meta.built_at_utc` tracks step 3 and per-row `updated_at` tracks step 2. The freshness of any particular row depends on when CMHC last published it upstream — see `MAX(updated_at)` per `table_id` for a per-metric freshness picture.

---

## Known limits

- The HMIP catalogue (mapping CMHC table_ids to metric names) drifts over time without notice. See `DATA_DISCOVERY.md` for the recurring patterns. If a metric you expect is missing, check whether the upstream catalogue knows about it before assuming the data is absent.
- HMIP suppresses data for small CSDs. The MMAH Affordable Residential Units Bulletin substitutes census-division rollups for these cells; that estimate is NOT in this mart (CMHC-only scope). See `DATA_DISCOVERY.md` 2026-05-23 entries for the verification methodology.
- Snapshot tables carry per-row period variance: HMIP returns each geo's most recent published value, and sparser geos may sit on older readings. The `updated_at` column tracks our archive's last refresh, not CMHC's reference date.
- The mart is a snapshot. CMHC continually updates its archive; nothing in this file pushes updates. Rebuild on a cadence that matches your tolerance for staleness (Rms annually; Srms quarterly).
- **Province level is uneven.** Vacancy Rate has the full 1990–2025 history; Availability Rate, Average Rent, Average Rent Change, Median Rent and Rental Universe still hold only the latest snapshot period at `geo_id = 'ON'`. The provincial series exist on HMIP — run `uv run python scripts/backfill_province_timeseries.py --series all` to add them. See `DATA_DISCOVERY.md` 2026-09-02.
- **October only.** RMS also ran an April survey from 2007 to 2015. HMIP honours only the first value of its `season` filter and the catalogue lists October first, so no spring reading is in this file. Unfixed; needs a second catalogue entry per time-series table.
- **No Neighbourhood or Survey Zone rows.** CMHC publishes RMS for named neighbourhoods (≈110 in the Toronto CMA) and survey zones (≈20). The pipeline fetches them, but `build_dmt_rental._assign_geo_level` keeps only rows whose name matches the Province/CMA/CSD/CT sets, so they are dropped at build time. Adding them needs two new `geo_level` values in `geographies`.
- **Mutating the mart in place bloats the file.** DuckDB does not return freed pages to the OS, so any script that rewrites tables (`backfill_province_timeseries.py` drops and recreates all 25 metric tables) leaves dead pages behind — one run grew the file by 23 MB. Since the mart is tracked in git, run `uv run python scripts/compact_mart.py` before committing it.
