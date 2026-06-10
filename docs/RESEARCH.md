# CMHC Data Portal — Scouting Notes

Scope: figure out what CMHC data exists, how to get it programmatically, and where the landmines are. This is reconnaissance — see [PLAN.md](PLAN.md) for design and [PROGRESS.md](PROGRESS.md) for current state.

This file has two parts:

1. **CMHC data surfaces** — what publishes where, how to fetch it (HMIP, Open Gov, static data tables, PDFs).
2. **Mapping framework evaluation** — historical comparison of Dash / Streamlit / Shiny for Python / Panel for the Ontario choropleth app. Shiny for Python was chosen and built (`app/shiny/`).

## Decisions locked in

- **Stack:** Python + uv + DuckDB (+ Parquet for raw storage)
- **First move:** map the HMIP table catalogue before pulling any data
- **Historical depth:** everything available

---

## Three data surfaces

CMHC publishes data through three distinct channels. Each has different mechanics, formats, and reliability.

### 1. HMIP — Housing Market Information Portal
**URL:** https://www03.cmhc-schl.gc.ca/hmip-pimh/

The main event. Looks like a dashboard, but underneath it's an ASP.NET app with a clean CSV export endpoint. Data is **not** locked inside the visualizations — CSVs come out the back door.

**Coverage:**
- Rental Market Survey (Rms) — annual, vacancy / rent / turnover
- Starts and Completions Survey (Scss) — monthly
- Secondary Rental Market Survey (Srms) — condos, secondary suites
- Seniors Housing Survey

**Geographic granularity:** national → province → CMA → CSD → census tract → neighbourhood.

**The endpoint** (reverse-engineered by mountainMath/cmhc):

```
POST https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/ExportTable
Content-Type: application/x-www-form-urlencoded

TableId=<e.g. 1.1.2.9>
GeographyId=<numeric>
GeographyTypeId=<1=Canada, 3=CMA, etc.>
Frequency=<Annual|Quarterly|Monthly>
ForTimePeriod.Year=<YYYY>
ForTimePeriod.Quarter=<1-4>      (optional)
ForTimePeriod.Month=<1-12>       (optional)
AppliedFilters[0].Key=<filter name>
AppliedFilters[0].Value=<filter value>
exportType=csv
```

A `(survey, series, dimension, breakdown, geoFilter)` tuple maps to one `TableId`. The full mapping table lives in `cmhc_tables.R` in the mountainMath package — we will mirror it into Python.

**Gotchas:**
- Response is **latin1 encoded**, not UTF-8
- Cookie/session may be required (mountainMath ships a hardcoded `ORDERDESKSID` — likely no real auth, just needs *something* present)
- No formal API, no SLA, no rate limit doc → be polite (sleep, parallel cap, retry-with-backoff)
- "Historical Time Periods" as breakdown returns time series; otherwise returns a snapshot

### 2. Open Government Portal
**URL:** https://search.open.canada.ca/opendata/?owner_org=cmhc-schl

~37 datasets in CSV / XML / HTML. Mostly aggregate national/provincial:
- Housing Starts in Urban Centres (10k+) — monthly
- Newly Completed and Unoccupied Housing — monthly
- Housing Starts — All Areas / All Rural — monthly
- Vacancy Rates — Apartment Structures (6+ units) — quarterly
- Several others

**Why it matters:** stable, documented, citeable, machine-readable. Lower resolution than HMIP but no scraping risk. Easy v1 win.

### 3. Static data tables (housing-data/data-tables/)
**URL:** https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/housing-data/data-tables/

Revised from initial scouting — this is **not** the loose "scattered PDFs" surface
it first appeared to be. It's a structured set of ~130 Excel workbooks across
four sections, with meaningful coverage NOT served by HMIP.

**Structure:**

| Section | Tables | Overlap with HMIP |
|---|---|---|
| Household Characteristics | 52 | **Mostly unique** (income/wealth/demographics/Indigenous/seniors/visible-minority cuts; core housing need broken down many ways) |
| Housing Market Data | ~40 | Largely overlaps `Scss` |
| Mortgage and Debt | 19 | **Unique** (delinquency, credit scores, debt loads, payment data) |
| Rental Market | 19 | Largely overlaps `Rms`; rural rental + non-resident condo ownership + social/affordable rental are unique |

**Format & storage:**
- All `.xlsx`. No CSV, no JSON, no API.
- Files hosted at `https://assets.cmhc-schl.gc.ca/sites/cmhc/professional/housing-markets-data-and-research/housing-data-tables/{section}/{slug}/...`
- URL convention is slug-based but **inconsistent** — some end `-en.xlsx`, others `-2023-en.xlsx`. Cannot construct URLs deterministically; must scrape each leaf page.
- Asset URLs are **not in the rendered HTML** by default — they're embedded in a Sitecore data island. Grep for `assets.cmhc-schl.gc.ca/...\.xlsx` against page source works.

**Volume:** verified one sample at 1.6 MB. Roughly 130 × 1 MB ≈ ~100-150 MB total. Downloadable in minutes.

**Parseability — medium.** No reverse-engineering, no rate limits, public CDN. But each xlsx has merged title cells, banner rows, multi-row headers, and footnotes — needs per-table parsers (~10-20 lines each). Probably 2× the wrangling vs HMIP CSVs.

**Update frequency:**
- Annual: most rental and household tables (post-RMS release, ~November)
- Quarterly: housing starts, mortgage performance
- Monthly: housing starts urban centres
- ~8 tables explicitly archived (no updates)

**Why it matters:**
Mortgage/debt and most of household-characteristics are simply not available
through HMIP. Long-range household projections (1976-2036), credit-score
distributions, mortgage delinquency by CMA, Indigenous housing — these are
the strongest reasons to pull this surface, not "extra coverage of stuff
HMIP also has."

### 4. Static publications (PDF reports)
**URL:** https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/market-reports

Excel + PDF market reports and publication archives at `publications.gc.ca/Collection-R/CMHC/`.
Mostly pre-formatted versions of data already in HMIP and the data tables above.
Lower priority unless we find a specific report that's not derivable from the structured surfaces.

---

## Prior art

### `cmhc` R package
- **Author:** Jens von Bergmann (mountainMath consulting, Vancouver)
- **Repo:** https://github.com/mountainMath/cmhc
- **What it does:** wraps the HMIP `ExportTable` POST as a pseudo-API
- **Why it matters to us:** validates the approach, gives us the URL pattern, parameter names, and the full TableId catalogue. We don't depend on it — we port the relevant pieces to Python.
- **Same author also wrote:** `cancensus` (Statistics Canada census wrapper), `tongfen` (cross-year census geography reconciliation). Both will be relevant if we want to join CMHC data to census attributes.

### Reseller signal
Commercial resellers (Urbanation, Rentals.ca, Altus, Yardi Matrix, Local Logic) repackage and enrich CMHC data. Their product pages are a **demand signal** — they tell us which slices have commercial value, which usually means:
- Granular rental by structure size / bedroom / age
- Condo pre-sale activity
- Micro-market vacancy
- Multi-year trended series with geography reconciled

Worth a separate pass once we have basic plumbing — they often surface CMHC datasets that aren't obvious from CMHC's own navigation.

---

## Known data quality issues
(per mountainMath blog, "Ins and outs of CMHC data")

- **Geocoding drifts** — same census tract shifts boundary year-over-year, creating phantom construction/demolition
- **Definitional creep** — "rental apartment" excludes co-ops, non-profits, condos rented out, student housing → silently undercounts true rental stock
- **Coverage gaps** — non-market housing, mixed-tenure buildings, student housing all under-enumerated
- **Census-year discontinuities** — geography definitions change with each census; harmonization is non-trivial (this is what `tongfen` exists to solve)

Implication: we need to (a) preserve raw exports immutably, (b) record the geography vintage on every row, (c) document definition changes in a `notes/` directory per survey.

---

## What we don't know yet

- Exact size of the full HMIP catalogue (how many `TableId` values × geographies × time periods)
- Whether the cookie really matters or is cargo-culted from an old version
- Whether `ExportTable` will rate-limit or block at scale
- Whether French (`/fr/`) endpoints return different/extra data
- How HMIP handles suppressed cells (privacy redactions) in the CSV — likely empty strings or sentinel values, needs to be handled in cleaning

Stage 0 (catalogue mapping) answers most of these.

---

## Reference URLs

- HMIP root: https://www03.cmhc-schl.gc.ca/hmip-pimh/
- HMIP TableMapChart entry: https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart
- HMIP export endpoint: https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/ExportTable
- HMIP user guide (BC gov mirror, 2014 vintage): https://catalogue.data.gov.bc.ca/dataset/aa7a5049-4a47-4182-b2c5-f4737f1913e0/resource/f75efa01-a9bb-4849-a88f-263c7e304a7a/download/cmhc-1935576_hmip_fullguide.pdf
- Open Gov CMHC list: https://search.open.canada.ca/opendata/?owner_org=cmhc-schl
- mountainMath cmhc (R): https://github.com/mountainMath/cmhc
- mountainMath blog post on CMHC data: https://doodles.mountainmath.ca/posts/2022-06-12-ins-and-outs-of-cmhc-data/
- CMHC data tables hub: https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/housing-data/data-tables
- Publications archive: https://www.publications.gc.ca/Collection-R/CMHC/index-e.html

---

# Part 2 — Mapping framework evaluation

> **Status (2026-06):** Shiny for Python was chosen and the app is built. Source: `app/shiny/`. Three pages live: CSD rent choropleth, CMA rent choropleth, charts (vacancy time series, rent-band bars). Run with `uv run shiny run --reload app/shiny/app.py`. This section is preserved as historical reasoning.

Goal at the time of the eval: an interactive web app for Ontario CMHC data. Polygon layers: 577 Ontario CSDs (168 CMA-member subset has the Rms/Srms data) and 2,533 Census Tracts. Filter-heavy (year × metric × dimension); click-polygon → side-panel time-series. Frameworks compared: **Dash**, **Streamlit**, **Shiny for Python**, **Panel (HoloViz)**.

## Use case framing

What the app needed to do:

1. Render Ontario polygons (CSDs / CTs) as a choropleth.
2. Pick survey + table + metric + period from dropdowns.
3. Click a polygon → time-series chart for that geography in a side panel.
4. Local single-user (`uv run …`).
5. Stay close to the existing Polars + DuckDB pipeline.

Non-requirements: multi-user auth, high concurrency, mobile, polished design system. Internal analysis tool, not a product.

## The four frameworks — one-paragraph summaries

**Dash (Plotly).** Flask-based; declare a component tree, wire `@callback(Output, Input)`. Largest third-party ecosystem; production-deployable at scale. Verbose — a small app is a lot of boilerplate. Callback model is rigid; server-side state is awkward. *Verdict: overkill for a one-person tool.*

**Streamlit.** Top-to-bottom script rerun on every interaction. Fastest path to a v0 prototype. Caching primitives (`@st.cache_data`) play nicely with DuckDB / Polars. *Critical drawback for this app*: full-script rerun on every widget change re-serializes the 2,533-polygon GeoJSON to the browser and breaks click-driven side panels. *Verdict: tempting but the rerun model fights the interaction pattern.*

**Shiny for Python.** Posit's port of R Shiny. Reactive execution: when an input changes, only the dependent reactive expressions and outputs recompute — not the whole script. `shinywidgets` bridges to `ipyleaflet` (full Leaflet support, click events, layer groups). Smaller ecosystem than Dash / Streamlit. Reactive mental model takes a session to internalize. *Verdict: strong fit — reactive execution avoids Streamlit's re-render trap; ipyleaflet event handling is mature.*

**Panel (HoloViz).** Panel + HoloViews + GeoViews + Datashader. Strongest raw geospatial capability — only one of the four that scales to millions of features. Large overlapping API surface; documentation fragmented across sub-projects. *Verdict: overpowered. Datashader is wasted on 2,533 polygons; would revisit only if we ever do all-of-Canada CTs (~57k).*

## Decision matrix

| Criterion                          | Dash      | Streamlit | Shiny for Python | Panel       |
|------------------------------------|-----------|-----------|------------------|-------------|
| Time to v0 choropleth              | Medium    | **Fast**  | Medium           | Medium-slow |
| Handles 2,533-polygon re-renders   | Good      | Poor*     | **Good**         | **Excellent** |
| Click-driven side panel ergonomics | Medium    | Poor      | **Good**         | Good        |
| Boilerplate                        | High      | **Low**   | Low-medium       | Medium      |
| Polars / DuckDB friendliness       | Good      | Good      | Good             | Good        |
| Ecosystem size                     | **Large** | Large     | Medium           | Medium      |
| Geospatial-specific tooling        | Medium    | Medium    | Medium           | **Excellent** |
| Right-sized for "one-person tool"  | No        | Yes       | **Yes**          | Overkill    |

\* Streamlit handles static choropleths fine; the problem is the rerun model causing map state loss when filters change.

## Recommendation (chosen)

**Shiny for Python.** Decisive factors:

1. Reactive model matches the filter-heavy interaction pattern — changing the year recomputes only the choropleth layer, not the whole script.
2. `ipyleaflet` click events are first-class; click-on-polygon → side-panel time-series is a 10-line pattern. Streamlit equivalent is a polling-style hack.
3. Right-sized for a one-person internal tool. Dash's production-scaling story is wasted; Panel's geospatial firepower is wasted at this polygon count.
4. Plays well with Polars + DuckDB — data stays in Polars all the way to the rendering boundary.

Conditions to revisit:
- Public-facing site with multiple concurrent users → reconsider Dash.
- All-of-Canada CTs (~57k polygons) → reconsider Panel (for Datashader).
- One-shot static widget that won't iterate → reconsider Streamlit.

## Sources

- [Dash vs Shiny vs Streamlit (Plotly)](https://plotly.com/comparing-dash-shiny-streamlit/)
- [Map (ipyleaflet) — Shiny for Python](https://shiny.posit.co/py/components/outputs/map-ipyleaflet/)
- [Panel (HoloViz) GitHub](https://github.com/holoviz/panel)
- [HoloViz Geospatial Examples](https://examples.holoviz.org/gallery/geospatial.html)
- [Datashader Geography User Guide](https://datashader.org/user_guide/Geography.html)
- [Streamlit vs Dash vs Shiny vs Voila (Data Revenue)](https://www.datarevenue.com/en-blog/data-dashboarding-streamlit-vs-dash-vs-shiny-vs-voila)
- [A Survey of Python Frameworks (Ploomber)](https://ploomber.io/blog/survey-python-frameworks/)
