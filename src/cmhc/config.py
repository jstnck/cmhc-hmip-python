"""Shared paths and knobs for scripts.

Resolved relative to the project root (the directory containing pyproject.toml),
so scripts work regardless of CWD.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
EMPTY_DIR = RAW_DIR / "_empty"
LOG_DIR = PROJECT_ROOT / "data" / "logs"

# Static-data-tables surface (the xlsx/xls download surface, separate from HMIP).
STATIC_RAW_DIR = RAW_DIR / "static"
STATIC_CATALOGUE = PROJECT_ROOT / "data" / "static_catalogue.json"

# Polite delay between HMIP requests, in seconds. Async pullers hold this
# delay inside the concurrency semaphore so the effective max throughput is
# CONCURRENCY / (response_time + REQUEST_DELAY).
REQUEST_DELAY = 0.2

# Max concurrent in-flight HMIP requests for async pullers.
CONCURRENCY = 5
