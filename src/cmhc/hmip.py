"""HMIP ExportTable client.

Reverse-engineered from mountainMath/cmhc R package. The endpoint is a plain
POST that returns a CSV body. No auth; a cookie is sent for parity with the R
implementation but appears to be cargo-culted.
"""

import asyncio
import atexit
import time

import httpx

from cmhc.catalogue import Table
from cmhc.geographies import Geography


EXPORT_URL = "https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/ExportTable"

# Strings HMIP embeds in the response body to indicate no data, instead of
# returning an HTTP error. Callers check for these to distinguish empty-but-
# valid results from real data.
EMPTY_SENTINELS = (b"No data available", b"This data series is now archived")


def is_empty_response(raw: bytes) -> bool:
    return any(s in raw[:2000] for s in EMPTY_SENTINELS)


# Matches the cookie shipped by the R package. Likely unnecessary but cheap to send.
_COOKIE = "DoNotShowIntro=true"

# Shared sync client — connection reuse matters at thousands of requests.
_client = httpx.Client(headers={"Cookie": _COOKIE})
atexit.register(_client.close)

# Async client is lazily created on first use so importing this module from a
# sync-only context (notebook, REPL) doesn't require an event loop.
_async_client: httpx.AsyncClient | None = None


def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(headers={"Cookie": _COOKIE})
    return _async_client


async def aclose() -> None:
    """Close the async client. Call once at end of an async program."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


def _build_form(
    table: Table,
    geo: Geography,
    year: int | None,
    month: int | None,
    quarter: int | None,
    frequency: str | None,
) -> dict[str, str | int | list[str]]:
    form: dict[str, str | int | list[str]] = {
        "TableId": table.table_id,
        "GeographyId": geo.geography_id,
        "GeographyTypeId": geo.geography_type_id,
        "exportType": "csv",
    }
    if year is not None:
        form["ForTimePeriod.Year"] = year
        if frequency is None:
            frequency = "Annual"
    if month is not None:
        form["ForTimePeriod.Month"] = month
        if frequency is None:
            frequency = "Monthly"
    if quarter is not None:
        form["ForTimePeriod.Quarter"] = quarter
        if frequency is None:
            frequency = "Quarterly"
    if frequency is not None:
        form["Frequency"] = frequency

    for i, (key, value) in enumerate(table.filters.items()):
        # HMIP expects multi-value filters as repeated form keys, not comma-
        # joined. httpx serializes list values by repeating the key.
        form[f"AppliedFilters[{i}].Key"] = key
        form[f"AppliedFilters[{i}].Value"] = value

    return form


def fetch_table(
    table: Table,
    geo: Geography,
    year: int | None = None,
    month: int | None = None,
    quarter: int | None = None,
    frequency: str | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> bytes:
    """POST to ExportTable and return the raw CSV bytes (latin1 encoded).

    Retries up to `max_retries` times on transient 5xx and transport errors,
    with exponential backoff (1s, 2s, 4s, ...).
    """
    form = _build_form(table, geo, year, month, quarter, frequency)

    for attempt in range(max_retries + 1):
        try:
            response = _client.post(EXPORT_URL, data=form, timeout=timeout)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise
        except httpx.TransportError:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise


async def fetch_table_async(
    table: Table,
    geo: Geography,
    year: int | None = None,
    month: int | None = None,
    quarter: int | None = None,
    frequency: str | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> bytes:
    """Async counterpart to fetch_table. Same semantics, same retry policy."""
    form = _build_form(table, geo, year, month, quarter, frequency)
    client = _get_async_client()

    for attempt in range(max_retries + 1):
        try:
            response = await client.post(EXPORT_URL, data=form, timeout=timeout)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except httpx.TransportError:
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
