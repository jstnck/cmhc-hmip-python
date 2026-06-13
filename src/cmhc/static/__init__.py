"""Parsers for CMHC's static data tables (the xlsx/xls download surface).

A configurable matrix engine (`matrix`) reads typed recipes (`specs`) to turn
bespoke spreadsheets into the shared long-format contract (`schema`). Provenance
comes from the catalogue (`catalogue`). `parse(table_id, path)` is the entry point.

Kept structurally separate from the HMIP library; converges only at the schema,
and reuses one pure shape primitive (`cmhc.wide`) that the HMIP path also uses.
"""

from cmhc.static.runner import parse

__all__ = ["parse"]
