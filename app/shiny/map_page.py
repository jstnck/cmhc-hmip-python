"""Ontario CSD rent choropleth.

Sidebar lets the user pick a bedroom type; the map colors each CSD by its
latest-period average rent for that bedroom. CSDs without published data
for the selection show as grey.
"""

import plotly.graph_objects as go
import polars as pl
from shiny import reactive, ui
from shinywidgets import output_widget, render_widget

from data import BEDROOM_TYPES, csd_boundaries, csd_rent_latest


def map_ui():
    return ui.layout_sidebar(
        ui.sidebar(
            ui.input_select(
                "bedroom", "Bedroom type",
                choices=BEDROOM_TYPES, selected="Total",
            ),
            ui.markdown(
                "Average rent per CSD, most recent CMHC release per geography. "
                "CSDs with no color either weren't surveyed or had values "
                "suppressed by CMHC for confidentiality (small samples)."
            ),
            width=300,
        ),
        output_widget("rent_map"),
    )


def map_server(input, output, session):
    @reactive.calc
    def selected():
        return (
            csd_rent_latest()
            .filter((pl.col("category") == input.bedroom()) & pl.col("value").is_not_null())
            .with_columns(
                pl.col("reliability").fill_null("—"),
                pl.col("period").dt.strftime("%b %Y").alias("period"),
            )
        )

    @render_widget
    def rent_map():
        df = selected()
        fig = go.Figure(go.Choroplethmap(
            geojson=csd_boundaries(),
            locations=df["csduid"].to_list(),
            z=df["value"].to_list(),
            featureidkey="properties.CSDUID",
            colorscale="Viridis",
            colorbar={"title": "Avg rent ($)"},
            marker={"opacity": 0.85, "line": {"width": 0.3, "color": "white"}},
            text=df["csd_name"].to_list(),
            customdata=list(zip(df["reliability"].to_list(), df["period"].to_list())),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Avg rent: $%{z:,.0f}<br>"
                "Reliability: %{customdata[0]}<br>"
                "%{customdata[1]}<extra></extra>"
            ),
        ))
        fig.update_layout(
            map={"style": "carto-positron", "center": {"lat": 45.5, "lon": -80.0}, "zoom": 4.5},
            margin={"r": 0, "t": 0, "l": 0, "b": 0},
            height=700,
        )
        return fig

    return rent_map
