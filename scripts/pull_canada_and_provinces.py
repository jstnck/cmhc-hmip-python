"""Pull HMIP tables at Canada + provincial scope.

    uv run python scripts/pull_canada_and_provinces.py
    uv run python scripts/pull_canada_and_provinces.py --surveys Rms,Scss
"""

import argparse
import asyncio

from cmhc.bulk import bulk_pull
from cmhc.geographies import CANADA, PROVINCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--surveys", default=None,
                        help="Comma-separated survey names to include (default: all). "
                             "Choices: Rms, Srms, Scss, Census, Seniors, 'Core Housing Need'.")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override max concurrent requests (default: cmhc.config.CONCURRENCY).")
    parser.add_argument("--refresh-empty-days", type=int, default=None, metavar="N",
                        help="Re-attempt combos whose empty marker is older than N days.")
    args = parser.parse_args()

    surveys = [s.strip() for s in args.surveys.split(",")] if args.surveys else None
    asyncio.run(bulk_pull(
        [CANADA, *PROVINCES.values()], label="geography", surveys=surveys,
        concurrency=args.concurrency,
        refresh_empty_days=args.refresh_empty_days,
    ))


if __name__ == "__main__":
    main()
