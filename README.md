# CMHC Data Portal

This project ports and extends Jens von Bergmann's [`cmhc` R package](https://github.com/mountainMath/cmhc) (mountainMath, Vancouver). That work reverse-engineered the Canada Mortgage and Housing Corporation's HMIP `ExportTable` endpoint and compiled the table catalogue this project depends on. Without it, none of this would exist. The catalogue at `src/cmhc/catalogue.py` is a direct port.

## Continued mapping

CMHC changes filter shapes and table identifiers without notice, and the R package's vignettes exercise only a narrow slice of the catalogue — so stale entries can sit unnoticed for years upstream. This project adds a discovery toolchain (`scripts/probe_table.py`) and a dated log of finds ([docs/DATA_DISCOVERY.md](docs/DATA_DISCOVERY.md)) to surface and repair them. Coverage and recent finds are summarized in [docs/PROGRESS.md](docs/PROGRESS.md).

## Data mart

`data/marts/cmhc_rental.duckdb` is a single-file DuckDB extract of Ontario rental data (Rental Market Survey + Secondary Rental Market Survey). ~17 MB. Star schema plus materialized metric tables for the common cross-sections; reliability codes, suppression markers, and CMHC table provenance carried through to every row.

Schema, column conventions, and example queries: [docs/DATAMART.md](docs/DATAMART.md).

## Documentation

- [docs/PLAN.md](docs/PLAN.md) — architecture and design decisions
- [docs/PROGRESS.md](docs/PROGRESS.md) — current data coverage
- [docs/DATA_DISCOVERY.md](docs/DATA_DISCOVERY.md) — running log of catalogue drift and data-quality finds
- [docs/DATAMART.md](docs/DATAMART.md) — rental data mart schema and usage
- [docs/RESEARCH.md](docs/RESEARCH.md) — scouting notes on CMHC's data surfaces
- [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) — project values and missing-data protocol

## Attribution

Source: Canada Mortgage and Housing Corporation (CMHC), Housing Market Information Portal (HMIP), various reference dates. This information is reproduced and distributed on an "as is" basis with the permission of CMHC.
