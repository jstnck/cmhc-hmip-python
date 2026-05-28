# Data Discovery

How we figure out what HMIP actually serves, vs. what our catalogue *claims* it serves.

`catalogue.py` is a port of mountainMath/cmhc's `cmhc_tables.R`. That R package is still actively maintained (last commit 2026-03), but its vignettes only exercise a narrow slice of the catalogue — Bedroom Type, Dwelling Type — so stale filter sets and dead table_ids in less-used dimensions can sit there for years without anyone noticing. This file is the running log of those finds.

---

## How HMIP signals "no data"

HMIP returns `200 OK` with `"No data available"` in the body for both *no data exists* and *your request is malformed*. `is_empty_response()` catches both. When an entire dimension returns empty across every geography, suspect the request shape, not the data.

| Symptom | Likely cause |
|---|---|
| `200 OK` + `"No data available"` | Genuinely empty *or* over-constrained filters |
| `200 OK` + `"This data series is now archived"` | CMHC retired the series |
| `HTTP 500` for one (table, geo) only | HMIP backend bug for that combo |
| `HTTP 500` for every geo of a table | Dead table_id — add to `validity.BROKEN_TABLE_IDS` |

---

## Default stance: missing data is suspicious, not accepted

When a parquet, map, or chart comes back with conspicuously sparse data — half the rows missing, a whole region greyed out, a column that's null everywhere — **do not assume the data is genuinely unavailable.** That's the same assumption that hid the bedroom-filter bug for years upstream. The default behavior is to investigate; assumed-empty is a hypothesis that has to be defended.

Order of operations when something looks empty:

1. **Verify against the source.** Hit the HMIP web view, the StatsCan table page, or the originating CSV directly. If the source shows data and we don't have it, the gap is in our pipeline.
2. **Probe the request shape.** Run `scripts/probe_table.py <id> --geo <name>`. If the bare form returns data but the catalogue form doesn't, a stale filter is suppressing the result silently.
3. **Trace the parse.** Open the raw CSV in `data/raw/`. If the values are `**`, that's CMHC's confidentiality suppression — real absence. If the values are present in the CSV but missing from parquet, the bug is in `tidy()` or `build_parquet`.
4. **Check the join.** When integrating across data sources (e.g. CMHC parquet vs Ontario MMA bulletin vs StatCan boundaries), confirm the join key actually links them. Name-based joins are brittle; CSDUID is the source of truth.
5. **Check for sibling sources.** CMHC suppresses small CSDs but Ontario's MMA bulletin reconstructs them via census-division rollups. StatCan publishes some series CMHC doesn't, and vice versa. When one source is sparse, ask which other source covers the gap.

Only after all five does "the data genuinely doesn't exist" become a defensible conclusion. Log the investigation below regardless of outcome — even confirmed-absences are useful so the next person doesn't redo the same work.

---

## Probe protocol

Use `scripts/probe_table.py` when a whole dimension returns empty everywhere, a table_id 500s deterministically, or the CMHC web view shows data we can't fetch.

```bash
uv run python scripts/probe_table.py 2.2.33 --geo "Prince Edward Island"
uv run python scripts/probe_table.py 9.9.9  --geo Canada --raw    # no catalogue lookup
```

It tries the bare form, the catalogue's filters, and leave-one-out variants — then prints which filter (if any) is the culprit. When it fingers a stale filter: log it below, fix `catalogue.py`, re-pull with `--refresh-empty-days 0`.

---

## Discovery surfaces beyond ExportTable

HMIP exposes a table-picker we can scrape to find TableIds the R package never knew about:

```
GET .../TableMapChart/TableMatchingCriteria?GeographyType=Province&GeographyId=24
    &CategoryLevel1=Primary+Rental+Market&CategoryLevel2=Vacancy+Rate+(%25)
    &ColumnField=RENTQUARTILE&RowField=20
```

Returns HTML listing every TableId matching the (geo, category, column, row) tuple. Worth a sweep when we want net-new dimensions.

---

## Sibling sources worth knowing about

This is the **CMHC data portal**, not a general housing-data portal. Other sources don't belong in our parquet output. But they're invaluable for *validating* whether a gap in our data is real or an artifact of pipeline / collection bugs. The pattern: if CMHC shows nothing, check a sibling that uses CMHC as input — if they have a number, the gap is real but reconstructable; if they also have nothing, the data genuinely doesn't exist.

| Source | What it gives | Why useful for verification |
|---|---|---|
| Ontario Ministry of Municipal Affairs and Housing — *Affordable Residential Units Bulletin* ([data.ontario.ca](https://data.ontario.ca/dataset/affordable-residential-units-for-the-purposes-of-the-development-charges-act-1997-bulletin)) | Avg market rent + affordability thresholds per Ontario LT/ST municipality (~414 rows). One CSV per release; current 2025-08 onward. | Built on top of CMHC RMS but uses **Census Division and MMAH Region substitutions** to fill in CSDs CMHC suppresses for confidentiality. The `Threshold applied` column documents the substitution method (`Rent; Base` = CMHC direct, `Rent; CD` = census-division rollup, `Rent; Region` = regional fallback, `Income; Base` = income-based when no market data). Great for confirming whether a CMHC-suppressed CSD genuinely lacks data or just lacks a publishable sample. |

A copy of the bulletin lives under `data/raw/ontario_gov/affordable_units/` for reference. It is **not** wired into the portal, parquet output, or app — those remain CMHC-only.

---

## Discovery log

Append-only. Newest at top.

### 2026-05-23 — Validity filter: skip CMA-scoped breakdowns for non-CMA CSDs

**Decision.** Tightened `is_valid_for_geo` to short-circuit non-CMA-member CSDs against Survey Zones / Neighbourhoods / Census Tracts breakdowns. Documented as a **removable optimization** in `validity.py`'s module docstring.

**Rationale.** Survey Zones, Neighbourhoods, and Census Tracts are statistical geographies defined inside CMAs — querying them at a CSD that doesn't belong to any CMA returns empty because the sub-units don't exist. The 881-sample evidence above shows 100% empty for these breakdowns on non-CMA CSDs (across 3 of ~17 RMS table_ids). Skipping them saves **21,571 requests off a full Ontario CSD sweep (52,234 → 30,663 jobs, –41%)**.

**Scope.** Currently Ontario only — the filter checks `geo.province_code == "35"` and consults `CSDS_ONTARIO_CMA` for membership. Other provinces fall through to the previous permissive behavior; the filter extends automatically once we add CSD-CMA-member lookups for them.

**What's preserved.** The filter still allows:
- `Census Subdivision` breakdown at non-CMA CSDs (returns the queried CSD itself; the single-empty-row case).
- `Historical Time Periods` at non-CMA CSDs (per-CSD time series; not yet sample-tested for emptiness, so we don't block it).

**How to undo.** Remove the `CMA_SCOPED_BREAKDOWNS` guard inside `is_valid_for_geo`'s CSD/CT branch. Reverts to the original permissive behavior. Useful if:
- CMHC starts publishing RMS at finer-than-CMA granularity for non-CMA areas.
- We want to re-run the 881-sample test to confirm continued emptiness.
- The CSD-CMA-member lookup goes stale (e.g., a 2026 census reshuffles CMAs and ours is from 2021).

Tests: `tests/test_validity.py`.

---

### 2026-05-23 — Non-CMA-member Ontario CSDs: 881-sample partial evidence of no RMS data

**Hypothesis under test.** Does CMHC's RMS publish rent / vacancy data for any of the ~406 Ontario CSDs that sit *outside* a Census Metropolitan Area? Our default pull (`pull_csds.py`) targets only the 168 CMA-member CSDs; this would matter for completeness if the non-CMA CSDs have publishable data we're missing.

**Test run.** `pull_csds.py --all --surveys Rms --concurrency 3`. Stopped after 881 requests because:
1. Cost projection was ~31 hours at observed throughput (~28 req/min, 7–10s per request) for the full 52,234-job scope.
2. The signal so far was unambiguous and continuing would just confirm a clear pattern.

**Results so far (881 requests over ~30 min, log file `data/logs/Ontario_CSD_(all)_20260524T024330Z.jsonl`):**
- **403 distinct Ontario CSDs sampled** (of 574 total) — a 70% sample of the universe, spanning all county clusters and CSD types (TP townships, IRI Indian reserves, MU municipalities, VL villages, T towns).
- **3 of ~17 RMS table_ids hit** so far (`2.1.1.3`, `2.1.1.4`, `2.1.1.5` — Vacancy Rate by Bedroom Type across Survey Zone / CSD / Neighbourhood breakdowns). The pull processes tables sequentially, so it hadn't reached the rent / availability / structure-size tables.
- **881 of 881 returned `empty`** (HMIP "No data available"). Zero `ok`, zero errors.

**What this is evidence for.** CMHC's RMS appears not to publish vacancy data at the CSD or Survey-Zone breakdown for non-CMA-member CSDs. The 100% empty rate across a diverse 70% sample is strong directional evidence.

**What this is NOT evidence for.** Several things could still be true:
- The remaining 14 RMS table_ids (Average Rent, Median Rent, Availability Rate, Structure Size, Year of Construction, Rent Ranges, etc.) might surface non-empty results for some non-CMA CSDs. We didn't test them.
- The remaining 171 unsampled CSDs (mostly later-alphabet names) might behave differently — unlikely given the spread of the 403 we did sample, but not proven.
- Non-RMS surveys at CSD scope (Census, Core Housing Need) were not in scope of this pull and could publish at non-CMA CSDs.

**Action.** Default of `pull_csds.py` stays at the 168-CMA-member subset (already the case — no change needed). The `--all` flag remains for anyone who wants to widen the sweep or test the remaining table_ids. **Not yet promoting this to a denylist rule** — the evidence is partial. If we ever revisit, the right finishing move is a smaller targeted run: all 17 RMS tables × ~30 sampled non-CMA CSDs to lock down the conclusion across the full RMS surface.

**Refs.** `data/logs/Ontario_CSD_(all)_20260524T024330Z.jsonl` (full 881-record log; queryable via DuckDB).

---

### 2026-05-23 — Census Division aggregates: MMAH has them, CMHC HMIP doesn't

**Finding.** Investigating whether we could build a CMHC-only Census Division (CD) map showed that **HMIP doesn't expose CD as a geography type at all.** CMHC's geo enum tops out at `Province → CMA → Survey Zone / CSD / Neighbourhood / CT`. No CD level. Survey Zones are CMHC's nearest-equivalent sub-CMA grouping but they don't align to StatCan census divisions.

The MMAH Affordable Residential Units Bulletin *does* publish CD-substituted values — and the substitution mechanic is documented in its data dictionary:

> Where data is unavailable, values are substituted based on the following:
> 1) price threshold for the upper-tier municipality or Census Division;
> 2) price threshold for the region.

Empirically verified by grouping LT/ST municipalities with `method = "Rent; CD"` in the Western MMAH region — they cluster perfectly by Census Division, every member of a CD sharing the same rent value:

| Census Division | 2BR rent | LT/ST municipalities |
|---|---|---|
| Wellington | $1,714 | Erin, Guelph-Eramosa, Mapleton, Minto, Puslinch |
| Bruce | $1,531 | Arran-Elderslie, Brockton, Northern Bruce Peninsula, South Bruce |
| Oxford | $1,477 | Blandford-Blenheim, East Zorra-Tavistock, Norwich, South-West Oxford, Zorra |
| Huron | $1,372 | Ashfield-Colborne-Wawanosh, Bluewater, Central Huron, Goderich, Howick, Huron East, Morris-Turnberry |
| Grey | $1,230 | Chatsworth, Georgian Bluffs, Grey Highlands, Hanover, Southgate, The Blue Mountains, West Grey |

**Where MMAH gets the CD value:** unclear and not documented in the bulletin or FAQ. The bulletin has zero UT (upper-tier) rows of its own — values only appear as substituted entries on LT/ST rows. Two plausible sources:
- A data-sharing arrangement giving MMAH access to CMHC RMS at the CD / upper-tier level (HMIP doesn't expose it publicly).
- MMAH computing CD rents themselves from underlying CSD data with suppression removed (they're a CMHC partner, may have un-suppressed access).

**Implication for this portal.** We can't replicate the CD rollup from CMHC public sources alone:
- Aggregating our public CSD parquet up to CDs would skip the ~44 suppressed CSDs and produce biased CD averages (a "Wellington County" computed only from Guelph would not reflect the rural townships at all).
- A CMHC-only CD map is therefore **not buildable accurately**. We deliberately skip it.

For map levels we *can* build accurately from CMHC alone:
- **CSD** (`2.1.11.4`) — sparse but honest. Suppression is CMHC's choice, not ours.
- **CMA** (`2.1.11.2` queried at the province level) — dense, full coverage at the CMA level.
- **Province** (`2.1.11.1`) — fully populated.
- **Survey Zone** (`2.1.11.3`) — sub-CMA, not investigated yet but available.

**Refs.** Data dictionary excerpt: `data/raw/ontario_gov/affordable_units/data_dictionary.csv` (rows describing TT&SUBST columns). Empirical analysis: see prior conversation; query is `bulletin.filter(method == "Rent; CD").group_by(rent).agg(municipalities)`.

---

### 2026-05-23 — CMHC suppresses small CSDs; MMAH bulletin confirms data exists

**Finding.** Many Ontario CSDs (e.g. Guelph/Eramosa, CSDUID 3523009) appeared as null in our rent parquet despite having raw CSVs on disk. Raw CSV shows CMHC returns `**` for every bedroom column — the confidentiality-suppression sentinel. 44 of 147 pulled CMA-member CSDs are fully suppressed across all bedrooms for the Total rent column.

Verified against the Ontario MMA Affordable Residential Units Bulletin (`data.ontario.ca`). It publishes per-municipality rents using CMHC RMS as its input — and where CMHC suppresses, MMAH substitutes the parent Census Division's rent (flagged `Rent; CD` in the bulletin's "Threshold applied" column). For Guelph/Eramosa the bulletin reports Studio $1,253 / 1BR $1,563 / 2BR $1,714 / 3BR+ $1,881 — all derived via Wellington CD rollup, none from the township's own sample.

**Why.** CMHC suppresses cells when survey sample size is too small to publish without identifying individual landlords. The data exists internally but isn't released at the CSD level for small townships. The MMAH bulletin's substitution methodology gives a *defensible estimate* for these CSDs but those are not CMHC's own measurements.

**Action.** None on the portal — we don't fold MMAH into our output because this is the CMHC portal. Logging the find so future investigations of "missing CSD rent" can confirm via MMAH before spending time hunting bugs.

**Refs.** Bulletin CSV: `data/raw/ontario_gov/affordable_units/bulletin_2025-08_onwards.csv`. Bulletin landing page: `https://data.ontario.ca/dataset/affordable-residential-units-for-the-purposes-of-the-development-charges-act-1997-bulletin`.

---

### 2026-05-23 — `tidy()` was dropping snapshot period + single-geo rows

**Finding.** Every snapshot-shape parquet row had `period = null` — a 2024 pull and a 2025 pull were indistinguishable. The period was always in the raw CSV (subtitle line like `October 2025 Row / Apartment Bedroom Type - Total`) but `tidy()` ignored anything above the comma-prefixed header. Deeper audit also found **22 logical Rms tables with 0 parquet rows despite 140–147 raw CSVs each**: when a sub-CMA breakdown is queried at the geo itself, HMIP returns a single row with an empty first cell, and our summary-row filter (`is_not_null() & != ""`) was wiping it. ~3,000 CSV files of sub-CMA pulls were silently disappearing.

**Why.** Two assumptions:
1. Period only lives in the data column. False — snapshot tables put it in the subtitle.
2. An empty first cell is always a summary row. False — when it's the only row, it represents the queried geo.

**Action.** `_slice_data_block` now returns `(body, lines, header_idx)`. New `_extract_subtitle_period` parses the subtitle (whole line, then 3/2/1-token prefixes). Summary-row filter only fires when other non-empty rows exist. Two regression tests added. Wiped `data/clean/` + rebuilt: **132 tables / 672,622 rows** (was 641,968), all snapshots now carrying a real `period`. Note: per-row period variance is real — HMIP returns each geo's most recent snapshot, and sparser geos sit on older data (e.g. `2.1.11.4` carries both 2012-10-01 and 2025-10-01).

**Pending.** CSD/CT pulls done before the bedroom-filter fix still have stale empty markers — re-run with `--refresh-empty-days 0 --surveys Rms`. Spot-check Scss/Srms/Seniors/Census parquets for the same `period = null` symptom.

**Refs.** `data/raw/Rms/2.1.4.1/Canada.csv` line 2 (subtitle); `tests/test_tidy.py::test_subtitle_period_attached_for_geo_breakdown`, `::test_single_geo_row_preserved`.

---

### 2026-05-23 — `bedroom_count_type_desc_en` filter was suppressing 9 RMS dimensions

**Finding.** Rent-ranges / rent-quartiles tables returned empty everywhere despite the CMHC web view showing PEI data from 2012–2025. Dropping `bedroom_count_type_desc_en` from AppliedFilters fixed it. Broadened the probe to every catalogue row that carried that filter (`extra="bedroom"` in `_RMS_SERIES`): **every one was being silently suppressed**.

| Series | Dimension | table_ids |
|---|---|---|
| Vacancy Rate | Year of Construction | 2.1.2.* / 2.2.2 |
| Vacancy Rate | Rent Ranges | 2.1.4.* / 2.2.4 |
| Vacancy Rate | Rent Quartiles | 2.1.33.* / 2.2.33 |
| Availability Rate | Year of Construction | 2.1.7.* / 2.2.7 |
| Availability Rate | Structure Size | 2.1.8.* / 2.2.8 |
| Average Rent | Year of Construction | 2.1.13.* / 2.2.13 |
| Median Rent | Year of Construction | 2.1.22.* / 2.2.22 |
| Rental Universe | Year of Construction | 2.1.27.* / 2.2.27 |
| Summary Statistics | (none) | 2.1.31 / 2.2.31 |

**Why.** These dimensions don't have bedroom as a sub-axis — the breakdown already partitions the rental stock. A bedroom filter on top narrows the cross-product to zero rows; HMIP returns `"No data available"`, indistinguishable from a real empty. The R package assigns `cmhc_bedroom_types` to every RMS Vacancy Rate row regardless of dimension — a bug we faithfully ported. No mountainMath/cmhc vignette exercises these dimensions, so it has sat there unnoticed upstream too. Worth filing an issue with mountainMath/cmhc when convenient.

**Action.** Dropped `bedroom_count_type_desc_en` from every RMS catalogue row. Re-pulled Canada + provinces with `--refresh-empty-days 0`: **96 previously-empty combos now return real data**, 23 legitimately empty, 0 errors. +14 logical tables / +16,306 rows from this fix alone.

**Pending.** Re-run CMA / CSD / CT pulls with the same flags for finer-geo gains. Probe other dimensions' filter sets — bedroom won't be the last stale R entry.

**Refs.** HMIP web view: `https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/Table?TableId=2.2.33&GeographyId=11&GeographyTypeId=2`. R source: `https://raw.githubusercontent.com/mountainMath/cmhc/master/R/cmhc_tables.R`. Probe: `scripts/probe_table.py 2.2.33 --geo "Prince Edward Island"`.
