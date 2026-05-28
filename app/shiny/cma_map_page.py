"""National CMA-level rent choropleth.

CMHC publishes RMS at the CMA level with full coverage (no suppression), so
this map fills cleanly — every CMA in the boundary file gets a colour.
"""

import plotly.graph_objects as go
import polars as pl
from shiny import reactive, ui
from shinywidgets import output_widget, render_widget

from data import BEDROOM_TYPES, cma_boundaries, cma_rent_latest


def cma_map_ui():
    return ui.layout_sidebar(
        ui.sidebar(
            ui.input_select(
                "cma_bedroom", "Bedroom type",
                choices=BEDROOM_TYPES, selected="Total",
            ),
            ui.markdown(
                "Average rent per Ontario CMA, latest CMHC release. "
                "43 CMAs / Census Agglomerations with mapped boundaries; "
                "smaller centres surveyed by CMHC aren't mapped here."
            ),
            width=300,
        ),
        output_widget("cma_rent_map"),
    )


def cma_map_server(input, output, session):
    @reactive.calc
    def selected():
        return (
            cma_rent_latest()
            .filter((pl.col("category") == input.cma_bedroom()) & pl.col("value").is_not_null())
            .with_columns(
                pl.col("reliability").fill_null("—"),
                pl.col("period").dt.strftime("%b %Y").alias("period"),
            )
        )

    @render_widget
    def cma_rent_map():
        df = selected()
        fig = go.Figure(go.Choroplethmap(
            geojson=cma_boundaries(),
            locations=df["cma_uid"].to_list(),
            z=df["value"].to_list(),
            featureidkey="properties.CMAPUID",
            colorscale="Viridis",
            colorbar={"title": "Avg rent ($)"},
            marker={"opacity": 0.85, "line": {"width": 0.5, "color": "white"}},
            text=df["cma_name"].to_list(),
            customdata=list(zip(df["reliability"].to_list(), df["period"].to_list())),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Avg rent: $%{z:,.0f}<br>"
                "Reliability: %{customdata[0]}<br>"
                "%{customdata[1]}<extra></extra>"
            ),
        ))
        fig.update_layout(
            map={"style": "carto-positron", "center": {"lat": 47.5, "lon": -83.0}, "zoom": 4.2},
            margin={"r": 0, "t": 0, "l": 0, "b": 0},
            height=700,
        )
        return fig
