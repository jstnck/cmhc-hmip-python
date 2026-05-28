"""Two charts:
    1. CMA vacancy time series — user picks one or more Ontario CMAs and a
       bedroom type; lines per CMA over October each year.
    2. Province rent-band vacancy distribution — user picks a province; bars
       show vacancy rate by rent band for the latest published period.
"""

import plotly.express as px
import polars as pl
from shiny import reactive, ui
from shinywidgets import output_widget, render_widget

from data import BEDROOM_TYPES, cma_names, cma_vacancy_timeseries, province_names, province_rent_bands


# Rent-band ordering for the bar chart — HMIP returns them in a non-sortable
# order; we want low → high left to right.
_RENT_BAND_ORDER = [
    "Less Than $750",
    "$750 - $999",
    "$1,000 - $1,249",
    "$1,250 - $1,499",
    "$1,500 +",
    "Non-Market/Unknown",
    "Total",
]


def charts_ui():
    return ui.layout_columns(
        ui.card(
            ui.card_header("Vacancy rate over time — Ontario CMAs"),
            ui.layout_sidebar(
                ui.sidebar(
                    ui.input_selectize(
                        "cmas", "CMAs",
                        choices=cma_names(),
                        selected=["Toronto", "Ottawa", "Hamilton"],
                        multiple=True,
                    ),
                    ui.input_select(
                        "vac_bedroom", "Bedroom type",
                        choices=BEDROOM_TYPES, selected="Total",
                    ),
                    width=250,
                ),
                output_widget("vacancy_ts"),
            ),
        ),
        ui.card(
            ui.card_header("Vacancy by rent band — provincial snapshot"),
            ui.layout_sidebar(
                ui.sidebar(
                    ui.input_select(
                        "rent_province", "Province",
                        choices=province_names(), selected="Ontario",
                    ),
                    width=250,
                ),
                output_widget("rent_band_bars"),
            ),
        ),
        col_widths=[12, 12],
    )


def charts_server(input, output, session):
    @reactive.calc
    def vacancy_data():
        if not input.cmas():
            return None
        return cma_vacancy_timeseries().filter(
            pl.col("geography").is_in(list(input.cmas()))
            & (pl.col("category") == input.vac_bedroom())
            & pl.col("value").is_not_null()
        )

    @render_widget
    def vacancy_ts():
        df = vacancy_data()
        if df is None or df.is_empty():
            return px.line(title="No data — select at least one CMA")
        fig = px.line(
            df, x="period", y="value", color="geography",
            markers=True,
            labels={"value": "Vacancy rate (%)", "period": "", "geography": "CMA"},
        )
        fig.update_layout(height=420, margin={"t": 30, "l": 40, "r": 10, "b": 40})
        return fig

    @reactive.calc
    def rent_band_data():
        # Null-filter for other provinces — small geos suppress some bands and
        # NaN breaks plotly's JSON encoder.
        return province_rent_bands().filter(
            (pl.col("sub_geography") == input.rent_province())
            & pl.col("value").is_not_null()
        )

    @render_widget
    def rent_band_bars():
        df = rent_band_data()
        if df.is_empty():
            return px.bar(title="No data")
        period = df["period"][0]
        present_cats = set(df["category"].to_list())
        order = [b for b in _RENT_BAND_ORDER if b in present_cats]
        fig = px.bar(
            df, x="category", y="value",
            category_orders={"category": order},
            labels={"value": "Vacancy rate (%)", "category": "Monthly rent band"},
            title=f"As of {period:%B %Y}",
        )
        fig.update_layout(height=420, margin={"t": 50, "l": 40, "r": 10, "b": 40})
        return fig

    return vacancy_ts, rent_band_bars
