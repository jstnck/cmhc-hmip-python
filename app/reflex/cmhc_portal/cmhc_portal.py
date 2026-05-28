"""Reflex entry point. Three routes:
    /        — CSD rent (Ontario)
    /cma     — CMA rent (Ontario)
    /charts  — CMA vacancy time series + provincial rent-band bars

Run with:
    cd app/reflex && uv run reflex run
"""

import reflex as rx

from .pages import charts_page, cma_page, csd_page


app = rx.App()
app.add_page(csd_page, route="/", title="CSD rent (Ontario)")
app.add_page(cma_page, route="/cma", title="CMA rent (Ontario)")
app.add_page(charts_page, route="/charts", title="Charts")
