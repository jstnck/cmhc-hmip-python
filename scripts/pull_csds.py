"""Pull HMIP tables at Census Subdivision (CSD) level.

Defaults to CMA-member Ontario CSDs (~168) — the set where Rms and Srms publish
data. Pass --all to hit all ~574 Ontario CSDs (useful for Census-style surveys
that publish at every CSD).

    # Rental data for the CMA-member CSDs
    uv run python scripts/pull_csds.py --surveys Rms,Srms --concurrency 10

    # Census + Core Housing Need for all Ontario CSDs
    uv run python scripts/pull_csds.py --all --surveys Census,'Core Housing Need'
"""

import argparse
import asyncio

from cmhc.bulk import bulk_pull
from cmhc.geographies import CSDS_ONTARIO, CSDS_ONTARIO_CMA


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="Pull all ~574 Ontario CSDs (default: ~168 CMA-member CSDs).")
    parser.add_argument("--surveys", default=None,
                        help="Comma-separated survey names to include (default: all). "
                             "Choices: Rms, Srms, Scss, Census, Seniors, 'Core Housing Need'.")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override max concurrent requests (default: cmhc.config.CONCURRENCY).")
    parser.add_argument("--refresh-empty-days", type=int, default=None, metavar="N",
                        help="Re-attempt combos whose empty marker is older than N days.")
    args = parser.parse_args()

    geos = CSDS_ONTARIO if args.all else CSDS_ONTARIO_CMA
    label = "Ontario CSD (all)" if args.all else "Ontario CSD (CMA-member)"
    surveys = [s.strip() for s in args.surveys.split(",")] if args.surveys else None

    asyncio.run(bulk_pull(
        geos.values(), label=label, surveys=surveys,
        concurrency=args.concurrency,
        refresh_empty_days=args.refresh_empty_days,
    ))


if __name__ == "__main__":
    main()
