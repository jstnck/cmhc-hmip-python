"""Resolve a static table's spec + provenance and parse its file.

The single entry point for turning a downloaded static file into the shared
long schema: look up the parse recipe (specs) and the provenance (catalogue)
by slug, run the engine.
"""

from pathlib import Path

import polars as pl

from cmhc.static import catalogue
from cmhc.static.matrix import run
from cmhc.static.specs import SPECS


def parse(table_id: str, path: Path) -> pl.DataFrame:
    """Parse the static file at `path` for the table identified by `table_id`."""
    if table_id not in SPECS:
        raise KeyError(f"No parse spec for static table {table_id!r}")
    return run(SPECS[table_id], catalogue.get(table_id), path)
