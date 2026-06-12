# CMHC Rental Data Mart

A single-file DuckDB extract of CMHC rental market data for Ontario. Built from the project's parquet archive (`data/clean/Rms/*.parquet`, `data/clean/Srms/*.parquet`) into one portable `.duckdb` file that an analyst can query directly with SQL.

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

- Ontario province (where the underlying table publishes at province level)
- 42 Ontario CMAs in the data (8 of them publish Srms; the rest are Rms-only)
- 147 Ontario CSDs with at least one published value, plus 20 placeholder rows for CMA-member CSDs CMHC publishes nothing for
- Of those 147, 22 belong to Census Agglomerations rather than CMAs — their CMHC publication is at the CSD level but they have no parent CMA, so these rows carry `cma = NULL` (see column conventions). They are a subset of the 147, not additional to it.
- **No Census Tracts.** ~2,382 Ontario CTs exist; the CT pull is queued in PROGRESS.md but not yet run. Neighbourhood-level rental is unavailable in this mart.

Total: **210 geographies** — 1 province + 42 CMAs + 147 CSDs with data + 20 placeholder CSDs.

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
| `dimension` | VARCHAR | `'Bedroom Type'`, `'Year of Construction'`, `'Structure Size'`, `'Rent Range'`, `'Rent Quartile'`, or NULL for summary stats |
| `category` | VARCHAR | Value within the dimension — e.g. `'2 Bedroom'`, `'Before 1960'`, `'Total'` |
| `value` | DOUBLE | NULL when `is_suppressed` is TRUE |
| `reliability` | CHAR(1) | `'a'` (excellent) → `'d'` (poor); NULL when suppressed |
| `is_suppressed` | BOOLEAN | TRUE when CMHC withheld for confidentiality (`**` in raw) |
| `source_survey` | VARCHAR | `'Rms'` or `'Srms'` |
| `table_id` | VARCHAR | CMHC coordinate — e.g. `'2.1.1.2'`. You don't need it for queries; it's there for HMIP cross-reference |
| `updated_at` | TIMESTAMP | mtime of the source parquet; same value for every row sharing a `table_id` |

### `metrics`

The metric inventory. `SELECT * FROM metrics` is the catalogue.

| column | type | notes |
|---|---|---|
| `metric_id` | SMALLINT | PK |
| `metric_name` | VARCHAR | `'Vacancy Rate'`, `'Average Rent'`, `'Condo Vacancy Rate'`, … |
| `market` | VARCHAR | `'Primary'` (Rms) or `'Secondary'` (Srms) |
| `source_survey` | VARCHAR | `'Rms'` or `'Srms'` |
| `unit` | VARCHAR | `'%'`, `'$'`, `'units'`, `'ratio'` |
| `description` | VARCHAR | One-line definition |
| `source_table_ids` | VARCHAR | Comma-separated HMIP table_ids that feed this metric |

### `geographies`

| column | type | notes |
|---|---|---|
| `geo_id` | VARCHAR | PK — canonical ID (`CSD:<CSDUID>`, `CMA:<CMA_UID>`, or `'ON'`) |
| `geo_name` | VARCHAR | Display name. Normalized to HMIP's hyphen form (`Guelph-Eramosa (TP)`, never `Guelph/Eramosa (TP)`) — see `cmhc.geographies.normalize_name` |
| `geo_level` | VARCHAR | `'Province'`, `'CMA'`, `'CSD'` |
| `province` | VARCHAR | Always `'Ontario'` |
| `cma` | VARCHAR | Parent CMA. **Populated only when the CSD is a member of a StatCan CMA.** ~22 Ontario CSDs publish rental data via CMHC but sit in Census Agglomerations (not CMAs) — those rows have `cma = NULL`. CMA rows have `cma = geo_name`; the Province row has `cma = NULL` |
| `csduid` | VARCHAR | StatCan CSDUID for CSD rows; NULL above |
| `cma_uid` | VARCHAR | StatCan CMA UID for CMA rows + CSD rows whose parent CMA is in StatCan's hierarchy |
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
| `n_observations` | BIGINT | Row count in `rental_observations` |
| `n_suppressed` | BIGINT | Count of rows with `is_suppressed = TRUE` |
| `n_cma` | BIGINT | Distinct Ontario CMAs present in `geographies` |
| `n_csd_with_data` | BIGINT | Ontario CSDs with at least one observation |
| `n_csd_no_data` | BIGINT | Placeholder Ontario CSDs (CMA-members CMHC publishes nothing for) |
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
| `avg_rent_dollars` (or `vacancy_pct`, etc.) | DOUBLE (the value, renamed + unit-suffixed) |
| `reliability` | CHAR(1) |
| `is_suppressed` | BOOLEAN |
| `source_survey`, `table_id`, `updated_at` | VARCHAR, VARCHAR, TIMESTAMP |

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

**Suppression.** `is_suppressed = TRUE` when CMHC withheld the cell for confidentiality (raw `**`). Detected as `value IS NULL AND reliability IS NULL` — both fields go null together in the suppression case. Other nulls (rare) get `is_suppressed = FALSE`. Always check `is_suppressed` before treating a missing value as zero or interpolating; CMHC suppression is concentrated in small CSDs and would bias any aggregate computed without awareness of it.

**Reliability codes.** `'a'` (excellent) → `'d'` (poor), based on CMHC's published reliability framework. Filter `WHERE reliability IN ('a','b')` for higher-confidence analyses. NULL means either suppressed or the table doesn't carry reliability information.

**Period.** Start-of-period date. RMS readings are annually surveyed in October (most rows are dated Oct 1); SRMS is published per release. Use `period_year` on the metric tables for groupings — already extracted.

**Geography parents.** `province` and `cma` are precomputed on every row so you can filter without joining. For a CMA row, `cma` equals its own name. For a CSD row, `cma` is the parent CMA. `province` is always `'Ontario'`.

**`table_id`.** The original CMHC coordinate — e.g. `'2.1.13.2'` for Avg Rent by Bedroom Type, CMA breakdown. You don't need it for queries; it's there if you want to cross-reference HMIP's web view (e.g. `https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/Table?TableId=2.1.13.2&GeographyId=35&GeographyTypeId=2`).

**`updated_at`.** mtime of the source parquet — i.e. when `build_parquet.py` last rebuilt that specific table from raw CSVs. All rows sharing a `table_id` share an `updated_at`. CMHC publishes Rms annually (≈ November release) and Srms quarterly, so expect this to be reasonably old for most rows most of the time.

**Geography name normalization.** StatCan's reference data uses forward-slash compound names (`Guelph/Eramosa`, `Greater Sudbury / Grand Sudbury`); CMHC's HMIP returns the hyphen form (`Guelph-Eramosa`, `Greater Sudbury - Grand Sudbury`). The mart canonicalizes on the hyphen form via `cmhc.geographies.normalize_name`. If you join external StatCan data against this mart on `geo_name`, normalize the StatCan side first. The full `csduid` / `cma_uid` are the safer join keys.

**Duplicate rows per logical observation.** A single CMHC measurement can land in the mart multiple times. CMHC publishes the same value through several `table_id` paths — e.g., the 2025 Toronto 2-bedroom average rent appears in `2.1.11.2` (Ontario province queried at CMA breakdown), `2.2.11` (Toronto queried as a time series), and similar combinations. Each path is a separate row in `rental_observations` (and therefore in the materialized metric tables) with the same `geo_name`, `period`, `dimension`, `category`, `value`, and `reliability` — but a different `table_id`.

What this means for queries:

- **Aggregations that average or take min/max are safe** — `AVG()` over identical values is still that value.
- **Aggregations that sum or count are NOT safe** without deduplication. `SELECT SUM(avg_rent_dollars) ...` will multiply by however many paths CMHC publishes the cell through (typically 2–3 for CMA rows).
- **To deduplicate**, pick one row per logical observation. The simplest rule is to keep the row with the smallest `table_id` per `(geo_name, period, dimension, category)` — or whichever subset of columns matches your grouping. Example:

```sql
SELECT * EXCLUDE (table_id) FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY geo_name, period, bedroom_type ORDER BY table_id
    ) AS rn
    FROM average_rent_by_bedroom
) WHERE rn = 1;
```

No `is_canonical` flag is provided — picking the canonical breakdown is task-specific (sometimes you want the time-series source, sometimes the snapshot-with-sub_geography one).

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

-- 7. Browse the metric catalogue
SELECT metric_name, market, unit, description
FROM   metrics
ORDER BY market, metric_name;

-- 8. What's the most recent data in this file?
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
