"""CMHC data portal — scrape and organize CMHC housing data."""

from cmhc.catalogue import CATALOGUE, Table, find, surveys
from cmhc.geographies import CANADA, CMAS, PROVINCES, Geography, get
from cmhc.hmip import fetch_table
from cmhc.tidy import tidy
from cmhc.validity import is_valid_for_geo

__all__ = [
    "CANADA",
    "CATALOGUE",
    "CMAS",
    "Geography",
    "PROVINCES",
    "Table",
    "fetch_table",
    "find",
    "get",
    "is_valid_for_geo",
    "surveys",
    "tidy",
]
