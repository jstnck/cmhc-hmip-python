# Mapping Frameworks — Evaluation

Goal: build an interactive web app that maps the CMHC Ontario data we've pulled. Polygon layers: 577 CSDs (168 CMA-member subset has the Rms/Srms data) and 2,533 Census Tracts. Time-series + cross-sectional metrics from Rms, Srms, etc. Polars + DuckDB + Parquet already in place; boundaries now live at `data/clean/boundaries_*.geojson` (built via `scripts/build_boundaries.py`).

Frameworks compared: **Dash**, **Streamlit**, **Shiny for Python**, **Panel (HoloViz)**.

---

## TL;DR recommendation

**Shiny for Python.** Best fit for this specific use case:

- Reactive execution model is the right shape for filter-heavy dashboards (change year → only the map recomputes; not the whole script).
- `ipyleaflet` / `leaflet.widgets` are first-class supported, with full event round-tripping (clicks on polygons → server-side state).
- Performs well with thousands of polygons because re-renders are scoped, not whole-script.
- Posit (the company) is actively investing; documentation is the cleanest of the four.

Alternatives are not bad — Streamlit will get you a prototype fastest, Dash is what you'd pick if this needed to scale to many concurrent users, Panel is what you'd pick if you needed Datashader-class rendering for millions of polygons. None of those apply here.

---

## Use case framing

What the app actually has to do:

1. Render Ontario polygons (CSDs and/or CTs) as a choropleth.
2. Let the user pick a survey + table + metric + time period from dropdowns.
3. Click a polygon → see a time-series chart for that geography in a side panel.
4. Be runnable locally (`uv run ...`) by one person, occasionally shared.
5. Stay close to our existing Polars + DuckDB pipeline (no rewrites to pandas).

Non-requirements (intentionally cut):
- Multi-user auth, sessions, RBAC.
- High concurrency / horizontal scaling.
- Mobile responsiveness.
- A polished design system.

This is an internal analysis tool, not a product. That shapes everything below.

---

## The four frameworks

### 1. Dash (Plotly)

**What it is.** A Flask-based framework where you declare a component tree, wire interactions through explicit `@callback(Output, Input)` decorators. Most mature of the four.

**Mapping story.** `dash-leaflet` is solid; `plotly.graph_objects.Choroplethmapbox` / `Choropleth` are native. Good fit for choropleths driven by a GeoJSON feature collection.

**Strengths.**
- Largest third-party component ecosystem.
- Production-deployable; people run Dash apps at scale.
- Explicit callback graph makes complex interactivity legible.
- Plotly figures are first-class — easy to drop a time-series chart next to the map.

**Weaknesses.**
- Verbose. A small app is a lot of boilerplate (layout tree + ids + callbacks).
- Callback model is rigid; refactoring multi-output flows is painful.
- Server-side state is awkward (you end up using `dcc.Store` or external caches).

**Verdict for our use case.** Overkill. We don't need its production scaling story, and we'd pay the boilerplate tax up front.

---

### 2. Streamlit

**What it is.** Top-to-bottom script rerun on every interaction. Adopted hard by the data-science community because the mental model is "write a script, get an app."

**Mapping story.** `st.map` (basic), `streamlit-folium` (Leaflet via Folium), `pydeck_chart` (deck.gl). `streamlit-folium` is the usual choice for choropleths.

**Strengths.**
- Fastest path to a v0 prototype — likely a working choropleth in under an hour.
- Caching primitives (`@st.cache_data`, `@st.cache_resource`) are well-designed and play nicely with DuckDB / Polars.
- Active community; gallery of examples for almost anything.

**Weaknesses.**
- **Full-script rerun on every widget change.** For a map with 2,533 polygons, this means the GeoJSON gets re-serialized to the browser on every dropdown change unless you carefully cache. The map flickers / loses zoom state without extra work.
- Click events on map features are awkward; `streamlit-folium` returns the last-clicked feature as a dict that you then have to react to via more reruns.
- Layout control is limited (columns + containers, not real grids).
- Session state is bolted on (`st.session_state`), not core.

**Verdict for our use case.** Tempting for a weekend prototype, but the rerun model fights you the moment you want a click-driven side panel. The "map keeps resetting on filter change" problem is well-known and frustrating.

---

### 3. Shiny for Python

**What it is.** Posit's port of R Shiny. Reactive execution: when an input changes, only the reactive expressions that depend on it recompute, and only the outputs that depend on those recompute. Released ~2022; Posit considers it production-ready as of 2025.

**Mapping story.** `shinywidgets` provides bridge to `ipyleaflet` (full Leaflet feature set including GeoJSON layers, click events, layer groups). Also supports any ipywidget, so `pydeck` + others are available. Posit publishes a documented `Map (ipyleaflet)` component example.

**Strengths.**
- **Reactive model is the right shape for this app.** Changing the year recomputes only the choropleth — the map element doesn't re-render, just its layer.
- Click events on map features are first-class via `ipyleaflet` event handlers.
- Less boilerplate than Dash. More structure than Streamlit.
- Two flavors — Shiny Core (closer to R Shiny) and Shiny Express (Streamlit-like single-file). Pick Core for anything non-trivial.
- Posit is investing — `shinylive` ships apps as static WASM, free hosting via `shinyapps.io`.

**Weaknesses.**
- Smaller ecosystem than Dash or Streamlit. Fewer Stack Overflow answers.
- Reactive mental model takes a session to internalize if you've never used R Shiny.
- Licensing nuance: Shiny Server Pro features are commercial; Shiny itself is permissive (MIT). For local/internal use this is irrelevant.

**Verdict for our use case.** Strong fit. Reactive execution avoids the Streamlit re-render trap; ipyleaflet handles 2,533 polygons fine; one-person local app maps directly onto how Shiny apps are written.

---

### 4. Panel (HoloViz)

**What it is.** Part of the HoloViz stack (Panel + HoloViews + GeoViews + Datashader + Bokeh). Panel is the app layer; the rest is the visualization stack.

**Mapping story.** The strongest of the four for serious geospatial work. GeoViews wraps cartopy projections; Datashader rasterizes millions of polygons or points server-side. For choropleths under ~10k features you'd use GeoViews / hvPlot directly; above that, Datashader.

**Strengths.**
- Best raw geospatial capability — only one of the four that scales to millions of features without breaking a sweat.
- Works equally well in notebooks and as a deployed app (same code).
- hvPlot lets you go `df.hvplot.polygons(...)` on a GeoDataFrame.
- Bokeh server underneath = real bidirectional streaming, not just request/response.

**Weaknesses.**
- The HoloViz stack is powerful but **the API surface is large** — Panel + HoloViews + GeoViews + hvPlot all overlap; figuring out the canonical way to do something takes longer.
- Documentation is comprehensive but fragmented across the sub-projects.
- Smaller community than the other three for app-building specifically (the community leans toward analysis/notebooks).
- Steeper learning curve than Shiny for the same end result, at our data volume.

**Verdict for our use case.** Overpowered. Datashader is wasted on 2,533 polygons. We'd be paying the learning-curve cost for capability we don't need. Would revisit if we ever pulled all-of-Canada CTs (~57k polygons).

---

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
| Production deployability           | **High**  | High      | High             | High        |
| Right-sized for "one-person tool"  | No        | Yes       | **Yes**          | Overkill    |

\* Streamlit is fine for static choropleths; the problem is the rerun model causing map state loss when filters change, which we *will* hit immediately given the filter-heavy design.

---

## Recommendation, with reasoning

**Build the first cut in Shiny for Python.**

The decisive factors:

1. **Reactive model matches the interaction pattern.** Our app is filter-heavy: pick year → pick metric → see updated choropleth. Shiny's reactive graph recomputes only the affected outputs. Streamlit's full-script rerun fights this. Dash's explicit callback graph works but is more verbose for the same effect.

2. **ipyleaflet event handling is mature.** Click-on-polygon → time-series-in-side-panel is a 10-line pattern in Shiny + ipyleaflet. In Streamlit it's a polling-style hack.

3. **Right complexity for the audience.** This is your analysis tool, not a public product. Dash's production scaling story is wasted. Panel's geospatial firepower is wasted at this polygon count.

4. **Plays well with Polars + DuckDB.** Shiny doesn't care what dataframe library you use; everything stays in Polars until the moment of rendering, where you produce a GeoJSON for the map and a `polars.DataFrame` → `plotly` chart for the side panel.

**Where I'd switch to a different one:**

- If you decide this needs to be a public-facing site with multiple concurrent users → **Dash**.
- If you scope creep into all-of-Canada CTs (~57k polygons) or block-level data → **Panel** (for Datashader).
- If you genuinely just want a one-screen "show this metric on a map" widget and never iterate → **Streamlit**.

---

## Concrete next steps (if you go with Shiny)

1. ~~Add deps + write a boundaries builder.~~ **Done.** `geopandas` + `topojson` installed; `scripts/build_boundaries.py` downloads the StatCan 2021 cartographic boundary files (CSD + CT), filters to Ontario, reprojects to WGS84, runs topology-preserving simplification, writes `data/clean/boundaries_{csd,ct}_ontario.geojson` (~2 MB each).
2. Add deps for the app: `uv add shiny shinywidgets ipyleaflet`.
3. Scaffold a single-page Shiny Core app at `app/app.py` with: geography-level toggle (CSD / CT) → survey/table/metric pickers → ipyleaflet choropleth + side-panel time-series. Document `uv run shiny run app/app.py --reload`.

## Open questions before building

- **Boundary source.** Statistics Canada cartographic vs digital boundary files differ in detail. Cartographic files are typically the right pick for choropleths (lighter, generalized coastlines). Confirm this before downloading.
- **What metric(s) do you actually want to map first?** "Rental vacancy rate by CT, latest available year" is a sensible MVP; everything else is a follow-on. Worth nailing down to avoid building generic-everything-pickers when one or two views would do.

---

## Sources

- [Dash vs Shiny vs Streamlit (Plotly)](https://plotly.com/comparing-dash-shiny-streamlit/)
- [Map (ipyleaflet) — Shiny for Python](https://shiny.posit.co/py/components/outputs/map-ipyleaflet/)
- [Panel (HoloViz) GitHub](https://github.com/holoviz/panel)
- [HoloViz Geospatial Examples](https://examples.holoviz.org/gallery/geospatial.html)
- [Datashader Geography User Guide](https://datashader.org/user_guide/Geography.html)
- [Streamlit vs Dash vs Shiny vs Voila (Data Revenue)](https://www.datarevenue.com/en-blog/data-dashboarding-streamlit-vs-dash-vs-shiny-vs-voila)
- [A Survey of Python Frameworks (Ploomber)](https://ploomber.io/blog/survey-python-frameworks/)
