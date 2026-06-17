"""Pull HMIP tables at Census Tract (CT) level.

Ontario only (~2,382 CTs). 15 Rms tables/CT are valid (after the leaf-redundancy
guard in cmhc.validity); 11 catalogued tables 500 at CT level and must be skipped
this run — 4 derived Rms series + all 7 Srms (a CMA-level survey with no tract
data). See docs/DATA_DISCOVERY.md 2026-06-17.

    uv run python scripts/pull_cts.py --surveys Rms --concurrency 3 \\
      --exclude-tables 2.2.4,2.2.33,2.2.12,2.2.31,4.2.1,4.2.3,4.2.4,4.2.5,4.4.2,4.6.1,4.6.2
"""

import argparse
import asyncio

from cmhc.bulk import bulk_pull
from cmhc.geographies import CTS_ONTARIO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--surveys", default=None,
                        help="Comma-separated survey names to include (default: all). "
                             "Choices: Rms, Srms, Scss, Census, Seniors, 'Core Housing Need'.")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override max concurrent requests (default: cmhc.config.CONCURRENCY).")
    parser.add_argument("--refresh-empty-days", type=int, default=None, metavar="N",
                        help="Re-attempt combos whose empty marker is older than N days.")
    parser.add_argument("--exclude-tables", default=None, metavar="IDS",
                        help="Comma-separated table_ids to skip this run (e.g. tables HMIP "
                             "500s on at CT level). Run-time only — leaves the catalogue and "
                             "validity rules unchanged; drop the flag and re-run to re-attempt.")
    args = parser.parse_args()

    surveys = [s.strip() for s in args.surveys.split(",")] if args.surveys else None
    exclude = [s.strip() for s in args.exclude_tables.split(",")] if args.exclude_tables else None
    asyncio.run(bulk_pull(
        CTS_ONTARIO.values(), label="Ontario CT", surveys=surveys,
        concurrency=args.concurrency,
        refresh_empty_days=args.refresh_empty_days,
        exclude_tables=exclude,
    ))


if __name__ == "__main__":
    main()
