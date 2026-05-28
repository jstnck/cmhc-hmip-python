"""Catalogue × geographies bulk-pull orchestration.

The single async entrypoint used by all pull scripts. Walks the catalogue,
filters by `is_valid_for_geo`, fetches in parallel under a semaphore, writes
each CSV to data/raw/{survey}/{table_id}/{geo}.csv. Idempotent — re-runs skip
files already on disk (or marked empty in data/raw/_empty/).

Per-run JSONL log written to data/logs/{label}_{utc_timestamp}.jsonl with one
record per non-skipped attempt. Queryable post-hoc via DuckDB:

    SELECT table_id, count(*) FROM 'data/logs/*.jsonl'
    WHERE outcome = 'error' GROUP BY 1 ORDER BY 2 DESC;
"""

import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from cmhc.catalogue import CATALOGUE, Table
from cmhc.config import CONCURRENCY, EMPTY_DIR, LOG_DIR, PROJECT_ROOT, RAW_DIR, REQUEST_DELAY
from cmhc.geographies import Geography
from cmhc.hmip import aclose, fetch_table_async, is_empty_response
from cmhc.validity import is_valid_for_geo


def _safe_name(s: str) -> str:
    return s.replace("/", "-").replace(" ", "_")


def _output_path(table: Table, geo: Geography) -> Path:
    return RAW_DIR / table.survey / table.table_id / f"{_safe_name(geo.name)}.csv"


def _empty_marker(table: Table, geo: Geography) -> Path:
    return EMPTY_DIR / table.survey / table.table_id / f"{_safe_name(geo.name)}.txt"


def _already_done(table: Table, geo: Geography) -> bool:
    return _output_path(table, geo).exists() or _empty_marker(table, geo).exists()


@dataclass
class PullResult:
    outcome: str  # 'ok' | 'empty' | 'error' | 'skipped'
    latency_s: float | None = None
    error_class: str | None = None
    error_msg: str | None = None


async def _pull_one(table: Table, geo: Geography, sem: asyncio.Semaphore) -> PullResult:
    if _already_done(table, geo):
        return PullResult(outcome="skipped")
    async with sem:
        t0 = time.monotonic()
        try:
            raw = await fetch_table_async(table, geo)
        except Exception as e:
            return PullResult(
                outcome="error",
                latency_s=time.monotonic() - t0,
                error_class=type(e).__name__,
                error_msg=str(e),
            )
        latency_s = time.monotonic() - t0
        # Polite-rate delay held inside the semaphore so the slot is occupied
        # for at least REQUEST_DELAY after each completed request.
        await asyncio.sleep(REQUEST_DELAY)

    if is_empty_response(raw):
        path = _empty_marker(table, geo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("No data available\n")
        return PullResult(outcome="empty", latency_s=latency_s)

    path = _output_path(table, geo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return PullResult(outcome="ok", latency_s=latency_s)


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f} min ({seconds:.0f}s)"


def _format_result(r: PullResult) -> str:
    if r.outcome == "error":
        return f"error: {r.error_class}: {r.error_msg}"
    return r.outcome


async def bulk_pull(
    geographies: Iterable[Geography],
    *,
    label: str = "geo",
    surveys: Iterable[str] | None = None,
    concurrency: int | None = None,
    refresh_empty_days: int | None = None,
) -> dict[str, int]:
    """Pull every valid (table, geography) combination. Returns counts.

    `label`              — used for the upfront log line and the log filename.
    `surveys`            — if given, restrict to tables in these surveys (e.g. ['Rms', 'Srms']).
    `concurrency`        — override the config default for this run.
    `refresh_empty_days` — before pulling, delete empty markers older than N days
                           for the in-scope (table, geo) jobs. Lets HMIP re-confirm
                           that combos still have no data, without wholesale wiping
                           the empty-marker cache.
    """
    geos = list(geographies)
    survey_set = set(surveys) if surveys else None
    catalogue = [t for t in CATALOGUE if survey_set is None or t.survey in survey_set]
    jobs: list[tuple[Table, Geography]] = [
        (t, g) for t in catalogue for g in geos if is_valid_for_geo(t, g)
    ]

    effective_conc = concurrency if concurrency is not None else CONCURRENCY
    survey_note = f" surveys={sorted(survey_set)}" if survey_set else ""
    print(f"{len(jobs)} valid (table, {label}) jobs queued (concurrency={effective_conc}){survey_note}")

    if refresh_empty_days is not None:
        cutoff = time.time() - refresh_empty_days * 86400
        removed = 0
        for t, g in jobs:
            marker = _empty_marker(t, g)
            if marker.exists() and marker.stat().st_mtime < cutoff:
                marker.unlink()
                removed += 1
        print(f"Refreshed: removed {removed} empty markers older than {refresh_empty_days} days "
              f"(those combos will be re-attempted this run)")
    counts = {"ok": 0, "empty": 0, "skipped": 0, "error": 0}
    sem = asyncio.Semaphore(effective_conc)
    start = time.monotonic()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{_safe_name(label)}_{stamp}.jsonl"
    print(f"Logging to {log_path.relative_to(PROJECT_ROOT)}")
    log_file: TextIO = log_path.open("w")
    log_lock = asyncio.Lock()

    async def run_one(i: int, table: Table, geo: Geography) -> None:
        result = await _pull_one(table, geo, sem)
        counts[result.outcome] += 1
        if result.outcome == "skipped":
            return
        print(f"[{i}/{len(jobs)}] {table.survey} {table.table_id} {geo.name}: {_format_result(result)}")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "survey": table.survey,
            "table_id": table.table_id,
            "geography": geo.name,
            "outcome": result.outcome,
            "latency_s": round(result.latency_s, 3) if result.latency_s is not None else None,
            "error_class": result.error_class,
            "error_msg": result.error_msg,
        }
        async with log_lock:
            log_file.write(json.dumps(record) + "\n")
            log_file.flush()

    try:
        await asyncio.gather(*(run_one(i, t, g) for i, (t, g) in enumerate(jobs, 1)))
    finally:
        log_file.close()
        await aclose()

    print()
    print(f"Done in {_fmt_elapsed(time.monotonic() - start)}.")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return counts
