"""Accessor over data/static_catalogue.json — the discovered static-table inventory.

Single source of truth for static-table provenance: section → survey label,
page slug → table_id, and the asset URL / size / last-modified. Parsers and the
mart builder resolve provenance here instead of restating it, so a parser never
hardcodes anything that can be looked up from the catalogue.

The catalogue is built by `scripts/build_static_catalogue.py`. Each entry's
page-slug (last path segment of `page_url`) is its `table_id`, and is unique
across the whole surface.
"""

import json
from dataclasses import dataclass
from functools import cache

from cmhc.config import STATIC_CATALOGUE

# Section slug → human-readable survey label (the `survey` column value).
SECTION_SURVEY = {
    "household-characteristics": "Household Characteristics",
    "housing-market-data": "Housing Market Data",
    "mortgage-and-debt": "Mortgage and Debt",
    "rental-market": "Rental Market",
}


@dataclass(frozen=True)
class StaticTable:
    table_id: str           # page slug, unique across the surface
    survey: str             # human-readable section label
    section: str            # section slug
    title: str | None
    page_url: str
    asset_url: str | None   # None for the few pages with no discovered download
    size_bytes: int | None
    last_modified: str | None


def _slug(page_url: str) -> str:
    return page_url.rstrip("/").rsplit("/", 1)[-1]


@cache
def _load() -> dict[str, StaticTable]:
    raw = json.loads(STATIC_CATALOGUE.read_text())
    out: dict[str, StaticTable] = {}
    for t in raw["tables"]:
        slug = _slug(t["page_url"])
        if slug in out:
            raise ValueError(f"Duplicate static-table slug {slug!r} in {STATIC_CATALOGUE.name}")
        asset = t["assets"][0] if t["assets"] else None
        out[slug] = StaticTable(
            table_id=slug,
            survey=SECTION_SURVEY.get(t["section"], t["section"]),
            section=t["section"],
            title=t.get("title"),
            page_url=t["page_url"],
            asset_url=asset["url"] if asset else None,
            size_bytes=asset.get("size_bytes") if asset else None,
            last_modified=asset.get("last_modified") if asset else None,
        )
    return out


def get(table_id: str) -> StaticTable:
    """Look up one static table by its slug. Raises KeyError if absent."""
    try:
        return _load()[table_id]
    except KeyError:
        raise KeyError(f"No static table {table_id!r} in {STATIC_CATALOGUE.name}")


def all_tables() -> list[StaticTable]:
    return list(_load().values())
