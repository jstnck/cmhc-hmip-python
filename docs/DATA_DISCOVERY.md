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

### 2026-06-13 — Static parser: "parses without error" silently over-counts; two correctness gates needed

**Context.** Building the static-table parsing layer (`cmhc.static.matrix` engine + `specs.py` registry, see PLAN.md). Most static files are the same wide matrix — geographies down the rows, periods or categories across the columns, with multi-sheet files carrying one extra dimension in the sheet name (census year, tenure). One configurable engine reads a typed `MatrixSpec` per table. The question was which of the downloaded files the flat engine actually parses *correctly*.

**Finding — error-free ≠ correct.** Auto-detecting a spec per file and checking only that it threw no exception claimed **35 of 52** household-characteristics files parsed. Inspection showed many were silently wrong, in two distinct ways, each needing its own gate:

1. **Melted text-dimension column → an all-null `category`.** Tables like `senior-led-households` have a leading `Age group` / `Category` text column on top of geography × year. The flat engine melts that column as if it were a value column; every cell is non-numeric, so it becomes a `category` that is **100% null**. *Gate: reject any output with an all-null category.*
2. **Dropped leading-dimension column → duplicate keys.** The income-**quintile** tables put a `Quintile` column between `Geography` and the year columns. `Quintile` isn't a period and isn't reliability, so the engine **drops** it — collapsing the 5 quintiles into duplicate rows with identical `(geography, period, category)` but different values. No error, plausible row counts, wrong data. The all-null gate misses it entirely. *Gate: reject any output with duplicate `(geography, period, category)` keys.*

With both gates, the real count is **17 clean** of 52: the rest are genuinely **multi-dimensional** (~20: a Tenure / Age group / Quintile / Visible-minority breakdown the flat engine can't represent — these await a multi-dimension engine mode) or **structural** (~15: no detectable header row, or one-sheet-per-province "by-geography" files).

**Secondary bug — the metric banner.** For single-metric tables the engine took `category` from the sheet's first cell. On the CMHC "Canadian Housing Observer" files that cell is a branding banner (`CANADIAN HOUSING OBSERVER`, or the French `L'OBSERVATEUR DU LOGEMENT AU CANADA`), not the metric. Fixed by sourcing the metric from the catalogue **page title** instead (reliable, scraped `<h1>`).

**Also seen.** One rental-market file (`rms-8-urban-vacancy-rate-by-rental-quintile-2019-10.xls`) is legacy **SpreadsheetML-2003 XML**, which calamine/`fastexcel` cannot open (`Invalid OLE signature`). Deferred; it overlaps HMIP Rms anyway.

**Action taken.** Engine + registry shipped; **18 tables spec'd** (mortgage delinquency + 17 household-characteristics), ~19k rows in `data/clean/static/`. `specs.py` documents that entries are admitted only after passing both correctness gates. housing-market-data (~38 files) excluded by design (HMIP/Scss overlap).

**Implications for future work.** The correctness gates are the admission test for every new static spec — re-run them when adding tables. The multi-dimension engine mode is the largest remaining static coverage lever (~20 household-characteristics tables, plus similar shapes in mortgage-and-debt). A flat-engine parse that "works" should never be trusted on a table with a leading non-geography text column.

**Refs.** `src/cmhc/static/matrix.py` (engine), `specs.py` (registry + gate note), `cmhc/wide.py` (shared melt primitive). Prior: 2026-06-11 (JS-injected downloads), 2026-06-13 render pass (below).

---

### 2026-06-13 — Full `--render` pass captured 66 JS-injected downloads; only 8 leaf pages still asset-less (all absorption tables)

**Finding.** Ran the queued full headless-render pass across the static-data-tables surface (`uv run --group scrape python scripts/build_static_catalogue.py --render`). It recovered the JS-injected downloads the 2026-06-11 entry predicted, taking the catalogue from 62 to **128 of 136** pages with a captured asset.

| | before | after |
|---|---|---|
| pages with assets | 62 | 128 |
| total assets | 62 | 128 |
| discovery `html` / `render` / none | 62 / 0 / 74 | 62 / 66 / 8 |

By section, the three highest-value domains are now fully captured: household-characteristics 52/52, mortgage-and-debt 18/18, rental-market 20/20. Only **housing-market-data** still has gaps (38/46).

**The 8 remaining asset-less pages** are all in housing-market-data and all describe **absorption / unabsorbed inventory**:

- `average-months-unabsorbed-life-inventory-{dwelling-type, intended-market}`
- `units-absorbed-{dwelling-type, intended-market}`
- `units-unabsorbed-{dwelling-type, intended-market}`
- `units-unabsorbed-for-more-than-one-month-{dwelling-type, intended-market}`

**Likely not a real loss.** This is absorption data, which the **Scss** survey already covers via HMIP (starts / completions / unabsorbed inventory — see PROGRESS.md). The 8 are the one static domain that *overlaps* HMIP rather than extending it, so a render miss here costs little. Two plausible causes for the empty result, not yet distinguished: (a) the page embeds an interactive widget with no downloadable xlsx, or (b) the asset loads through a DOM path the network-idle wait + DOM regex doesn't reach. Flagged for a manual DevTools look rather than chased — low priority given the HMIP overlap.

**Action taken.** `data/static_catalogue.json` rewritten with the full asset set + per-page `discovery` field. Run log: `data/logs/static_render_*.log`. The 2026-06-11 caveat ("treat a 0-asset entry as not-yet-discovered") now applies only to those 8 housing-market-data pages.

**Implications for future work.** The inventory step (PLAN.md static sequence #1) is done — the full set of files-to-parse is now visible. Next is fixing the shared long-format contract + standing up `src/cmhc/static/`, then the mortgage-delinquency parser as the first vertical slice. The 8 absorption pages should be diffed against existing Scss coverage before deciding whether they're worth a manual capture at all.

**Refs.** Scraper: `scripts/build_static_catalogue.py` (`--render`). Catalogue: `data/static_catalogue.json`. Prior entry: 2026-06-11.

---

### 2026-06-11 — Half the static-data-table downloads are JS-injected, invisible to the httpx scraper (mortgage delinquency among them)

**Finding.** Asked whether CMHC publishes **mortgage delinquency** data. It does — but not where the project was looking.

- **HMIP: no.** The HMIP catalogue (`catalogue.py`) covers Rms, Scss, Srms, Census, Seniors, Core Housing Need only. No mortgage / delinquency / arrears / credit series. Confirmed by grep; consistent with RESEARCH.md's note that the Mortgage-and-Debt domain is "Unique — not served by HMIP."
- **Static data tables: yes.** A dedicated leaf page, *"Mortgage Delinquency Rate: Canada, Provinces and CMAs"* (`mortgage-and-debt` section), already in `data/static_catalogue.json` — but with **zero captured asset URLs**.

The deeper finding is structural: `build_static_catalogue.py`'s discovery method (httpx GET the leaf page, regex the HTML for `assets.cmhc…/….xlsx`) only works for pages that embed the asset URL in **server-rendered** HTML. **74 of 136 leaf pages** carry no asset URL in the server HTML at all — the download link is injected into the DOM by **client-side JavaScript** (the delinquency page's only inline `$.ajax` calls go to account/folder/email endpoints; the table itself is an Equifax-sourced widget rendered after load). The httpx scraper is blind to all 74.

| Section | Leaf pages | 0-asset (JS-injected) |
|---|---|---|
| household-characteristics | 52 | 43 |
| mortgage-and-debt | 18 | 13 |
| housing-market-data | 46 | 9 |
| rental-market | 20 | 9 |
| **All** | **136** | **74** (3 are archived stubs) |

The 74 are concentrated in exactly the domains RESEARCH.md flagged as the *strongest reasons to pull this surface* — demographic/income/Indigenous cuts (household-characteristics) and credit/delinquency (mortgage-and-debt). This is the unique, non-HMIP data, not filler.

**Steps run.**
1. Grepped `catalogue.py` + `src/` for mortgage/delinquency/arrears/credit → nothing (not in HMIP).
2. Found the delinquency leaf in `static_catalogue.json` with 0 assets; counted 0-asset pages across all sections (74/136).
3. `curl` of the delinquency page → HTTP 200, correct `<title>`, but **no `.xlsx` anywhere in the HTML**; only loader GIFs + a GTM iframe; inline `$.ajax` targets are account/folder/email only.
4. Confirmed via human DevTools: a Download button is present on the *rendered* page, with a real `href` (Copy-link-address worked). The href lives in the post-JS DOM, not the server HTML.
5. Built and validated a headless-render fallback (see Action) — it recovered the exact asset URL.
6. Verified the file: `…/mortgage-delinquency-rate-ca-prov-cmas-2012-q3-2025-q4-en.xlsx` → HTTP 200, 41,129 bytes, `Last-Modified: Wed, 25 Mar 2026`. Coverage 2012-Q3 → 2025-Q4. (polars can't open it yet — no `fastexcel` in the env; parsing is a later concern.)

**The URL is not constructible.** The pasted link was
`…/mortgage-delinquency-rate-ca-prov-cmas-2012-q3-2025-q4-en.xlsx?rev=960e3a3e-…`.
The asset slug is **abbreviated** (`ca-prov-cmas`, not the page slug `canada-provinces-cmas`), carries an **embedded date range** that shifts each release, and a **`?rev=` Sitecore GUID** cache-buster. Four guessed URLs from the page-slug convention all 404'd — confirming RESEARCH.md's "cannot construct URLs deterministically; must scrape each leaf page." The render fallback stores the clean no-`?rev=` form, which resolves fine (200).

**Action taken.**
- Added a **headless-render fallback** to `scripts/build_static_catalogue.py` (opt-in `--render`): the httpx pass runs as before, then for every 0-asset page it loads the page in headless Chromium, waits for network-idle, and regexes the live DOM (+ iframes) for the asset URL. Each leaf now records a `discovery` field (`"html"` | `"render"` | `null`). Playwright lives in a separate **`scrape` dependency group** so the headless-browser dep never touches the HMIP scraper or the default install. The script is already independent of `src/cmhc/` (the HMIP library) — the JS-rendering concern stays fully out of the HMIP path.
- Validated on the delinquency page: render recovered the asset URL on the first try.
- **Not yet run at scale** — the full 74-page render pass is queued, not done (see PROGRESS.md "Next steps"). This entry records the mechanism and the confirmed single case.

**Implications for future work.**
- The static catalogue is **understated by up to ~71 tables** (74 minus the 3 archived stubs) until the render pass runs. Anyone reading `static_catalogue.json` today should treat a 0-asset entry as "not yet discovered," not "no file exists."
- This is the project's first genuine need for a headless browser — exactly the trigger PLAN.md's "On headless browsers" section reserved it for. It is scoped to this one scraper; HMIP remains a plain POST.
- Once assets are captured, the per-table xlsx parsers are the next lift (merged title cells, multi-row headers — RESEARCH.md estimates ~10–20 lines each). Mortgage delinquency is the natural first parser given it's confirmed and small.

**Refs.** Leaf page: `…/data-tables/mortgage-and-debt/mortgage-delinquency-rate-canada-provinces-cmas`. Asset: `…/mortgage-debt/mortgage-delinquency-rate-canada-provinces-cmas/mortgage-delinquency-rate-ca-prov-cmas-2012-q3-2025-q4-en.xlsx`. Scraper: `scripts/build_static_catalogue.py` (`_render_assets`, `--render`). Source attribution: Equifax (named on the CMHC page).

---

### 2026-06-10 — CMHC vs StatCan name format drift (slash vs hyphen) was silently dropping 5 Ontario CSDs from the mart

**Finding.** While auditing the first build of `data/marts/cmhc_rental.duckdb` (the Ontario rental data mart), 5 CMA-member Ontario CSDs known to publish rental data were absent from the mart's `geographies` table:

| CSD (StatCan form) | CSD (CMHC HMIP form, in our parquets) |
|---|---|
| `Guelph/Eramosa (TP)` | `Guelph-Eramosa (TP)` |
| `Greater Sudbury / Grand Sudbury (CV)` | `Greater Sudbury - Grand Sudbury (CV)` |
| `McNab/Braeside (TP)` | `McNab-Braeside (TP)` |
| `The Nation / La Nation (M)` | `The Nation - La Nation (M)` |
| `West Nipissing / Nipissing Ouest (M)` | `West Nipissing - Nipissing Ouest (M)` |

The mart's filter was constructing `"{CSDNAME} ({CSDTYPE})"` directly from `src/cmhc/data/csds_ontario_cma_members.csv` (StatCan-sourced) and matching against the parquet's `geography` column (HMIP-sourced). The slash-vs-hyphen drift dropped the 5 CSDs silently — they had real data in the parquets but never landed in the mart.

**Why.** StatCan's CSD reference data uses forward slashes for compound names (`Guelph/Eramosa`, `Greater Sudbury / Grand Sudbury`); CMHC's HMIP renders the same names with hyphens. Both forms refer to the same CSDUID. No documentation flags the transformation — you only discover it when joining the two name spaces.

**Steps run.**
1. Compared lookup labels (570) against parquet `geo_name` set (374 distinct values), found 26 lookup entries with no parquet match.
2. Applied `s.replace("/", "-")` to lookup labels — 5 matches recovered, 21 still missing.
3. Confirmed the 21 still-missing CSDs have zero parquet rows across all 128 Rms+Srms tables = genuine CMHC non-publication, not a name bug. Those are now `has_data = FALSE` placeholder rows in the mart.

**Action taken.**
- Added `cmhc.geographies.normalize_name(name)` (`src/cmhc/geographies.py`) as the canonical name-normalization helper. Any future script joining StatCan reference data against HMIP-sourced parquets should import this rather than inline a replace.
- Updated `scripts/build_dmt_rental.py` to normalize on both sides of the comparison and store geo_name in HMIP's hyphen form (the analyst-visible canonical).
- Rebuilt mart: 5 CSDs recovered, +13,033 observations, `n_csd_with_data` 142 → 147.
- Logged here.

**Implications for future work.**
- This is one instance of a general failure mode: any time we join CMHC parquets against StatCan reference data (or vice versa), name-format drift is a likely source of silent row loss. The safer join keys are canonical IDs (`CSDUID`, `CMA_UID`, `CT UID`) — text-name joins should always normalize.
- A proper geographic concordance catalogue sourced from StatCan's SGC/GAF and vintage-tagged is a longer-term need (PLAN.md defers; flagged in mart audit). For now: `normalize_name` covers the known cases; revisit if new drift patterns surface.

**Refs.** `cmhc.geographies.normalize_name`; mart build log; pre-fix mart `geographies` count 142 → post-fix 147; placeholder CSDs visible via `SELECT * FROM geographies WHERE NOT has_data`.

---

### 2026-06-09 — Srms publishes for 8 Ontario CMAs, not 4 — stale empty markers were hiding half

**Finding.** Prior pulls had Srms data for only 4 Ontario CMAs (Toronto, Ottawa, Windsor, St. Catharines – Niagara). Re-running `pull_cmas.py --province Ontario --surveys Srms --refresh-empty-days 0` recovered 4 additional publishing CMAs: **Barrie, Hamilton, Kitchener – Cambridge – Waterloo, London**. All 7 Srms tables now populate for 8 Ontario CMAs.

| Status | CMAs |
|---|---|
| Publishing (8) | Barrie, Hamilton, Kitchener – Cambridge – Waterloo, London, Ottawa, St. Catharines – Niagara, Toronto, Windsor |
| Empty (35) | The other 35 Ontario CMAs — confirmed `empty` across all 7 Srms tables |

**Why hidden.** Empty markers from a pre-fix pull were never expiring; default `pull_cmas.py` skips combos with existing markers. The 4 missing publishing CMAs (Barrie, Hamilton, KCW, London) all had stale markers from when CMHC had not yet published or when an earlier request shape returned empty.

**Steps run.**
1. `uv run python scripts/pull_cmas.py --province Ontario --surveys Srms` → 0 ok, 0 empty, **301 skipped** (all combos already had cached markers).
2. `uv run python scripts/pull_cmas.py --province Ontario --surveys Srms --refresh-empty-days 0` → 28 newly-`ok` (4 CMAs × 7 tables) + 28 skipped (the 4 already-`ok` CMAs × 7 tables) + 245 still-`empty` + 0 errors. Log: `data/logs/CMA_20260609T235800Z.jsonl`.
3. Rebuilt parquet. Srms rows: **1,716 → 3,432**.

**Implications.**
- The previous PROGRESS / DATA_DISCOVERY claim that "the 4 publishing CMAs are Toronto, Ottawa, Hamilton, Kitchener" was wrong — Hamilton and Kitchener do publish, but so do Barrie and London, and the prior pull happened to have Windsor + St. Catharines – Niagara instead. Real list is 8.
- **Other surveys with Ontario CMA scope (Census, Seniors, Core Housing Need) likely have the same problem.** Their coverage numbers in PROGRESS.md reflect the pre-fix empty markers; a `--refresh-empty-days 0` pass for those surveys at Ontario CMAs should be done before treating the row counts as final.
- If we ever add a per-(table, geo) validity filter to short-circuit Srms at the 35 non-publishing CMAs, the publishing-CMA list is now the 8 above.

**Action taken.** Parquet rebuilt; PROGRESS.md updated with new Srms row count + correct 8-CMA list; this entry logged. Not denylisting the 35 non-publishing CMAs yet — the validity filter optimization is tracked as PROGRESS.md issue #8.

**Refs.** Run log: `data/logs/CMA_20260609T235800Z.jsonl`. Catalogue rows: `src/cmhc/catalogue.py:191-197`.

---

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
