"""Pull HMIP tables at CMA level.

Optional --province narrows to CMAs within one province. Stats Canada CMA UIDs
encode the province as their first two digits (e.g. 35... = Ontario).

    uv run python scripts/pull_cmas.py
    uv run python scripts/pull_cmas.py --province Ontario
    uv run python scripts/pull_cmas.py --province Ontario --surveys Rms,Srms
"""

import argparse
import asyncio

from cmhc.bulk import bulk_pull
from cmhc.geographies import CMAS, PROVINCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--province", choices=sorted(PROVINCES.keys()),
                        help="Restrict to CMAs in this province (default: all CMAs).")
    parser.add_argument("--surveys", default=None,
                        help="Comma-separated survey names to include (default: all). "
                             "Choices: Rms, Srms, Scss, Census, Seniors, 'Core Housing Need'.")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override max concurrent requests (default: cmhc.config.CONCURRENCY).")
    parser.add_argument("--refresh-empty-days", type=int, default=None, metavar="N",
                        help="Re-attempt combos whose empty marker is older than N days.")
    args = parser.parse_args()

    cmas = list(CMAS.values())
    if args.province:
        prefix = str(PROVINCES[args.province].geography_id)
        cmas = [c for c in cmas if c.cma_uid and c.cma_uid.startswith(prefix)]
        print(f"Filtering to {len(cmas)} CMAs in {args.province} (cma_uid prefix {prefix!r}).")

    surveys = [s.strip() for s in args.surveys.split(",")] if args.surveys else None
    asyncio.run(bulk_pull(
        cmas, label="CMA", surveys=surveys,
        concurrency=args.concurrency,
        refresh_empty_days=args.refresh_empty_days,
    ))


if __name__ == "__main__":
    main()
