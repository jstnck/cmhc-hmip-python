"""Reflex State holding selections and the four derived Plotly figures.

Reflex pattern (vs Shiny reactive.calc): state vars are class attributes;
derived values are `@rx.var(cache=True)` methods that recompute when any
referenced state var changes.
"""

import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import reflex as rx

from .data import (
    cma_boundaries,
    cma_rent_latest,
    cma_vacancy_timeseries,
    csd_boundaries,
    csd_rent_latest,
    province_rent_bands,
)


_RENT_BAND_ORDER = [
    "Less Than $750",
    "$750 - $999",
    "$1,000 - $1,249",
    "$1,250 - $1,499",
    "$1,500 +",
    "Non-Market/Unknown",
    "Total",
]


def _choropleth(df: pl.DataFrame, *, loc_col: str, name_col: str,
                geojson: dict, featureidkey: str,
                center: dict, zoom: float) -> go.Figure:
    fig = go.Figure(go.Choroplethmap(
        geojson=geojson,
        locations=df[loc_col].to_list(),
        z=df["value"].to_list(),
        featureidkey=featureidkey,
        colorscale="Viridis",
        colorbar={"title": "Avg rent ($)"},
        marker={"opacity": 0.85, "line": {"width": 0.3, "color": "white"}},
        text=df[name_col].to_list(),
        customdata=list(zip(df["reliability"].to_list(), df["period"].to_list())),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Avg rent: $%{z:,.0f}<br>"
            "Reliability: %{customdata[0]}<br>"
            "%{customdata[1]}<extra></extra>"
        ),
    ))
    fig.update_layout(
        map={"style": "carto-positron", "center": center, "zoom": zoom},
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=700,
    )
    return fig


class State(rx.State):
    csd_bedroom: str = "Total"
    cma_bedroom: str = "Total"
    # Single-CMA selection — Reflex's rx.select is single-value, so we lose
    # the Shiny multiselect here. Document this as a framework limitation.
    vacancy_cma: str = "Toronto"
    vacancy_bedroom: str = "Total"
    rent_province: str = "Ontario"

    # Explicit setters — Reflex 0.9 no longer auto-generates `set_<var>`.
    @rx.event
    def set_csd_bedroom(self, v: str): self.csd_bedroom = v
    @rx.event
    def set_cma_bedroom(self, v: str): self.cma_bedroom = v
    @rx.event
    def set_vacancy_cma(self, v: str): self.vacancy_cma = v
    @rx.event
    def set_vacancy_bedroom(self, v: str): self.vacancy_bedroom = v
    @rx.event
    def set_rent_province(self, v: str): self.rent_province = v

    @rx.var(cache=True)
    def csd_rent_fig(self) -> go.Figure:
        df = (
            csd_rent_latest()
            .filter((pl.col("category") == self.csd_bedroom) & pl.col("value").is_not_null())
            .with_columns(
                pl.col("reliability").fill_null("—"),
                pl.col("period").dt.strftime("%b %Y").alias("period"),
            )
        )
        return _choropleth(
            df, loc_col="csduid", name_col="csd_name",
            geojson=csd_boundaries(), featureidkey="properties.CSDUID",
            center={"lat": 45.5, "lon": -80.0}, zoom=4.5,
        )

    @rx.var(cache=True)
    def cma_rent_fig(self) -> go.Figure:
        df = (
            cma_rent_latest()
            .filter((pl.col("category") == self.cma_bedroom) & pl.col("value").is_not_null())
            .with_columns(
                pl.col("reliability").fill_null("—"),
                pl.col("period").dt.strftime("%b %Y").alias("period"),
            )
        )
        return _choropleth(
            df, loc_col="cma_uid", name_col="cma_name",
            geojson=cma_boundaries(), featureidkey="properties.CMAPUID",
            center={"lat": 47.5, "lon": -83.0}, zoom=4.2,
        )

    @rx.var(cache=True)
    def vacancy_fig(self) -> go.Figure:
        df = cma_vacancy_timeseries().filter(
            (pl.col("geography") == self.vacancy_cma)
            & (pl.col("category") == self.vacancy_bedroom)
            & pl.col("value").is_not_null()
        )
        if df.is_empty():
            return px.line(title="No data")
        fig = px.line(
            df, x="period", y="value",
            markers=True,
            labels={"value": "Vacancy rate (%)", "period": ""},
            title=f"{self.vacancy_cma} — {self.vacancy_bedroom}",
        )
        fig.update_layout(height=420, margin={"t": 50, "l": 40, "r": 10, "b": 40})
        return fig

    @rx.var(cache=True)
    def rent_band_fig(self) -> go.Figure:
        df = province_rent_bands().filter(
            (pl.col("sub_geography") == self.rent_province)
            & pl.col("value").is_not_null()
        )
        if df.is_empty():
            return px.bar(title="No data")
        present = set(df["category"].to_list())
        order = [b for b in _RENT_BAND_ORDER if b in present]
        period = df["period"][0]
        fig = px.bar(
            df, x="category", y="value",
            category_orders={"category": order},
            labels={"value": "Vacancy rate (%)", "category": "Monthly rent band"},
            title=f"As of {period:%B %Y}",
        )
        fig.update_layout(height=420, margin={"t": 50, "l": 40, "r": 10, "b": 40})
        return fig
