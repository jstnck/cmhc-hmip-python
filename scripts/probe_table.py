"""Probe a single (TableId, Geography) combo against HMIP to diagnose stale catalogue
entries.

Hits ExportTable with the bare form, the catalogue's full filter set, and the
catalogue filters minus each individual filter. Reports which combinations return
data vs. "No data available" vs. HTTP errors. Identifies the minimal working filter
set, or flags the filter that's breaking things.

Usage:
    uv run python scripts/probe_table.py 2.2.33 --geo "Prince Edward Island"
    uv run python scripts/probe_table.py 2.1.11.4 --geo Toronto
    uv run python scripts/probe_table.py 9.9.9 --geo Canada --raw  # ad-hoc, no catalogue lookup

When the bare form works but the catalogue filters return empty, that's a stale
filter — log it in docs/DATA_DISCOVERY.md, fix catalogue.py, re-pull.
"""

import argparse
import asyncio

import httpx

from cmhc.catalogue import CATALOGUE, Table
from cmhc.geographies import CANADA, CMAS, CSDS_ONTARIO, CSDS_ONTARIO_CMA, CTS_ONTARIO, PROVINCES, Geography
from cmhc.hmip import EXPORT_URL, _build_form, is_empty_response


def _find_geo(name: str) -> Geography:
    if name == "Canada":
        return CANADA
    for d in (PROVINCES, CMAS, CSDS_ONTARIO, CSDS_ONTARIO_CMA, CTS_ONTARIO):
        if name in d:
            return d[name]
    # Fall back to substring match on CMAs / CSDs
    for d in (CMAS, CSDS_ONTARIO):
        hits = [g for k, g in d.items() if name.lower() in k.lower() or name.lower() in g.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SystemExit(f"Ambiguous geography {name!r}: {[h.name for h in hits[:5]]}")
    raise SystemExit(f"Unknown geography: {name!r}")


def _find_table(table_id: str) -> Table | None:
    hits = [t for t in CATALOGUE if t.table_id == table_id]
    return hits[0] if hits else None


def _classify(response: httpx.Response) -> tuple[str, int]:
    body = response.content
    n_lines = len([l for l in body.decode("latin1", errors="replace").splitlines() if l.strip()])
    if response.status_code >= 400:
        return f"http_{response.status_code}", n_lines
    if is_empty_response(body):
        return "empty", n_lines
    return "ok", n_lines


async def _post(client: httpx.AsyncClient, table: Table, geo: Geography) -> tuple[str, int, int]:
    form = _build_form(table, geo, None, None, None, None)
    r = await client.post(EXPORT_URL, data=form)
    outcome, n_lines = _classify(r)
    return outcome, n_lines, len(r.content)


async def probe(table_id: str, geo: Geography, raw: bool) -> None:
    catalogue_entry = None if raw else _find_table(table_id)
    if catalogue_entry is None and not raw:
        print(f"TableId {table_id!r} not in catalogue. Pass --raw to probe with no filters.")
        return

    print(f"TableId:    {table_id}")
    if catalogue_entry:
        print(f"Catalogue:  {catalogue_entry.survey} / {catalogue_entry.series} / "
              f"{catalogue_entry.dimension} / {catalogue_entry.breakdown}")
    print(f"Geography:  {geo.name} (id={geo.geography_id}, type={geo.geography_type_id})")
    print()

    variants: list[tuple[str, Table]] = []
    # Always probe the bare request — establishes whether the (table, geo) pair
    # has any data at all, independent of filters.
    bare = Table(
        survey=catalogue_entry.survey if catalogue_entry else "?",
        series="?", dimension=None, breakdown="?",
        table_id=table_id, filters={},
    )
    variants.append(("bare (no filters)", bare))

    if catalogue_entry and catalogue_entry.filters:
        variants.append(("catalogue (all filters)", catalogue_entry))
        # Leave-one-out: which single filter is responsible?
        for key in catalogue_entry.filters:
            reduced = {k: v for k, v in catalogue_entry.filters.items() if k != key}
            variants.append((
                f"  minus {key!r}",
                Table(catalogue_entry.survey, catalogue_entry.series, catalogue_entry.dimension,
                      catalogue_entry.breakdown, catalogue_entry.table_id,
                      catalogue_entry.geo_filter, reduced),
            ))

    async with httpx.AsyncClient(headers={"Cookie": "DoNotShowIntro=true"}, timeout=30) as client:
        results: list[tuple[str, str, int, int]] = []
        for label, t in variants:
            outcome, n_lines, n_bytes = await _post(client, t, geo)
            results.append((label, outcome, n_lines, n_bytes))
            tag = "✓" if outcome == "ok" else " "
            print(f"  {tag} {label:30}  {outcome:10}  {n_lines:>4} lines / {n_bytes:>5} bytes")

    print()
    bare_ok = results[0][1] == "ok"
    cat_ok = catalogue_entry and len(results) > 1 and results[1][1] == "ok"

    if bare_ok and catalogue_entry and not cat_ok:
        # A leave-one-out variant that flipped to OK fingers the offender.
        culprits = [label.strip().replace("minus ", "") for label, out, _, _ in results[2:] if out == "ok"]
        if culprits:
            print(f"DIAGNOSIS: data exists but catalogue filters break it. "
                  f"Removing {', '.join(culprits)} makes it work.")
        else:
            print("DIAGNOSIS: data exists for bare request but no single-filter removal recovers it.")
    elif not bare_ok:
        print("DIAGNOSIS: even the bare request fails. Likely a dead table_id or invalid (table, geo) combo.")
    elif cat_ok:
        print("DIAGNOSIS: catalogue filters work as-is.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("table_id", help="HMIP TableId, e.g. 2.2.33")
    parser.add_argument("--geo", required=True, help="Geography name (e.g. 'Toronto', 'Prince Edward Island', 'Canada').")
    parser.add_argument("--raw", action="store_true",
                        help="Skip catalogue lookup; probe with no filters. Use for unknown TableIds.")
    args = parser.parse_args()

    asyncio.run(probe(args.table_id, _find_geo(args.geo), args.raw))


if __name__ == "__main__":
    main()
