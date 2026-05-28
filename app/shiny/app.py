"""Shiny entry point. Three pages: Ontario CSD rent choropleth, Ontario
CMA rent choropleth, and a Charts page (vacancy time series + rent-band
bars). All data comes from the parquet files built by
`scripts/build_parquet.py`.

Run with:
    uv run shiny run --reload app/shiny/app.py
"""

from shiny import App, ui

from charts_page import charts_server, charts_ui
from cma_map_page import cma_map_server, cma_map_ui
from map_page import map_server, map_ui


app_ui = ui.page_navbar(
    ui.nav_panel("CSD rent (Ontario)", map_ui()),
    ui.nav_panel("CMA rent (Ontario)", cma_map_ui()),
    ui.nav_panel("Charts", charts_ui()),
    title="CMHC Data Portal",
    fillable=True,
)


def server(input, output, session):
    map_server(input, output, session)
    cma_map_server(input, output, session)
    charts_server(input, output, session)


app = App(app_ui, server)
