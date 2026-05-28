"""Reflex page components. One function per page; navigation is a top-bar
of links inside a shared layout, mirroring the Shiny `page_navbar` setup."""

import reflex as rx

from .data import BEDROOM_TYPES, cma_names, province_names
from .state import State


def _layout(active: str, content: rx.Component) -> rx.Component:
    """Header + nav + page body. `active` is the current route label."""
    def nav(label: str, href: str) -> rx.Component:
        weight = "bold" if label == active else "regular"
        return rx.link(label, href=href, weight=weight, padding_x="3")

    return rx.box(
        rx.hstack(
            rx.heading("CMHC Data Portal", size="5"),
            rx.spacer(),
            nav("CSD rent (Ontario)", "/"),
            nav("CMA rent (Ontario)", "/cma"),
            nav("Charts", "/charts"),
            padding="3",
            border_bottom="1px solid #e2e8f0",
            align="center",
        ),
        content,
    )


def _sidebar_layout(sidebar: rx.Component, body: rx.Component) -> rx.Component:
    return rx.flex(
        rx.box(sidebar, padding="3", width="300px", border_right="1px solid #e2e8f0"),
        rx.box(body, padding="3", flex_grow="1"),
        height="calc(100vh - 60px)",
    )


def csd_page() -> rx.Component:
    sidebar = rx.vstack(
        rx.text("Bedroom type", weight="bold"),
        rx.select(
            BEDROOM_TYPES,
            value=State.csd_bedroom,
            on_change=State.set_csd_bedroom,
        ),
        rx.text(
            "Average rent per CSD, most recent CMHC release per geography. "
            "CSDs with no color either weren't surveyed or had values "
            "suppressed by CMHC for confidentiality (small samples).",
            size="2",
            color="gray",
        ),
        spacing="3",
    )
    body = rx.plotly(data=State.csd_rent_fig, width="100%", height="700px")
    return _layout("CSD rent (Ontario)", _sidebar_layout(sidebar, body))


def cma_page() -> rx.Component:
    sidebar = rx.vstack(
        rx.text("Bedroom type", weight="bold"),
        rx.select(
            BEDROOM_TYPES,
            value=State.cma_bedroom,
            on_change=State.set_cma_bedroom,
        ),
        rx.text(
            "Average rent per Ontario CMA, latest CMHC release. "
            "43 CMAs / Census Agglomerations with mapped boundaries; "
            "smaller centres surveyed by CMHC aren't mapped here.",
            size="2",
            color="gray",
        ),
        spacing="3",
    )
    body = rx.plotly(data=State.cma_rent_fig, width="100%", height="700px")
    return _layout("CMA rent (Ontario)", _sidebar_layout(sidebar, body))


def charts_page() -> rx.Component:
    vacancy = rx.card(
        rx.heading("Vacancy rate over time — Ontario CMAs", size="4"),
        rx.flex(
            rx.vstack(
                rx.text("CMA", weight="bold"),
                rx.select(
                    cma_names(),
                    value=State.vacancy_cma,
                    on_change=State.set_vacancy_cma,
                ),
                rx.text("Bedroom type", weight="bold"),
                rx.select(
                    BEDROOM_TYPES,
                    value=State.vacancy_bedroom,
                    on_change=State.set_vacancy_bedroom,
                ),
                spacing="3",
                width="250px",
                padding_right="3",
            ),
            rx.box(
                rx.plotly(data=State.vacancy_fig, width="100%", height="420px"),
                flex_grow="1",
            ),
        ),
    )
    rent_bands = rx.card(
        rx.heading("Vacancy by rent band — provincial snapshot", size="4"),
        rx.flex(
            rx.vstack(
                rx.text("Province", weight="bold"),
                rx.select(
                    province_names(),
                    value=State.rent_province,
                    on_change=State.set_rent_province,
                ),
                spacing="3",
                width="250px",
                padding_right="3",
            ),
            rx.box(
                rx.plotly(data=State.rent_band_fig, width="100%", height="420px"),
                flex_grow="1",
            ),
        ),
    )
    body = rx.vstack(vacancy, rent_bands, spacing="3", padding="3", width="100%")
    return _layout("Charts", body)
