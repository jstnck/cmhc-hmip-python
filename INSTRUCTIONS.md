# Instructions for Claude

This project's two non-negotiable values are **accuracy** and **completeness**. Velocity, feature count, cleverness — none of that matters next to those two. A shipped result that misrepresents the data (silently dropping rows, mismatching joins, accepting nulls without checking) is worse than no result at all. A half-empty map that *looks* authoritative makes us look like fools when someone notices the data does exist; a sparse map that's *honest* about what's missing is fine.

---

## The missing-data rule

> Missing data is acceptable ONLY when it is actually missing at the source. Apparent absence is a hypothesis, not a conclusion.

Before ever accepting a null, empty cell, grey polygon, sparse table, or "no data available" as truth, walk this protocol:

1. **Verify against the upstream source.** Hit CMHC's HMIP web view for the table+geo combo. Hit the StatCan landing page. Open the raw CSV directly. If they show data and we don't, the gap is in our pipeline.
2. **Probe the request shape.** Run `scripts/probe_table.py <table_id> --geo <name>`. If the bare form returns data but the catalogue form doesn't, a stale filter is suppressing it silently — the bedroom-filter bug hid 9 RMS dimensions like this.
3. **Trace the parse.** Look at the raw CSV in `data/raw/`. If values are present in the CSV but null in parquet, the bug is in `tidy()` or `build_parquet`. If values are `**`, that's CMHC's confidentiality suppression (a real absence at the public surface).
4. **Check the join.** When integrating sources, confirm the join key actually links them. Name-based joins are brittle — CSDUID / CMAPUID / canonical IDs are the source of truth. The CSV-filename → display-name → lookup chain lost 5 CSDs to `/`-vs-`-` mangling.
5. **Check a sibling source.** CMHC suppresses small CSDs but Ontario's MMAH bulletin reconstructs them via census-division rollups. StatCan publishes series CMHC doesn't, and vice versa. When one source is sparse, ask whether another source covers the gap and how.

Only after all five does "the data genuinely doesn't exist" become a defensible conclusion.

The protocol and worked examples live in `DATA_DISCOVERY.md`.

---

## Evidence requirements

Every gap you accept (or any investigation you run) lands in `DATA_DISCOVERY.md` as a dated entry. The entry has to carry enough evidence that someone else can reproduce the finding without re-investigating from scratch:

- **What** is missing (specific geographies, rows, cells, table_ids)
- **Steps run** (which sources checked, which probes used)
- **Verbatim evidence** — quote the raw CSV excerpt, paste the HMIP web view URL, name the sibling source and its value
- **Conclusion** — suppressed by CMHC / not surveyed / vintage mismatch / catalogue bug / parse bug / etc.
- **Action taken** — nothing / denylisted / fixed / left for follow-up

Confirmed-absent data is worth logging too. Otherwise the next person redoes the same work.

---

## What we don't do

- **Don't hedge** on missing data. No "likely suppressed," "probably not in the dataset," "I think this is a vintage issue." Verify, then write it as fact with evidence, or say "I haven't investigated yet."
- **Don't accept apparent emptiness without verification.** If something looks sparse, the default action is to investigate, not to ship.
- **Don't fold non-CMHC data into the portal output.** This is the CMHC Data Portal. MMAH, StatCan tables, and other sources may be useful as *verification references* (their downloads can live under `data/raw/<source>/` and their findings can be documented), but they don't go into `data/clean/` parquet and they don't get rendered as portal pages.
- **Don't ship a map / chart / table with half the data missing** if the missing half is recoverable via a join fix, a parser fix, or a request-shape fix. Fix it or don't ship.

---

## Defaults

- Python 3.12+ — do NOT add `from __future__ import annotations`.
- DataFrames are polars — do NOT add pandas.
- Add a regression test when fixing a bug. The bedroom-filter bug and the `tidy()` snapshot-period bug both sat for months because nothing prevented their return.
- Update `DATA_DISCOVERY.md` after any non-trivial investigation (positive or negative result).
- Use canonical IDs (CSDUID, CMAPUID, CMA_UID) for joins, not display names.
- Before presenting options or building speculatively, ask the user. CLAUDE.md global rule: "When presenting the user with options, wait for the user response. DO NOT just start writing code."

---

## Reference docs at a glance

| File | Purpose | Audience |
|---|---|---|
| `INSTRUCTIONS.md` (this file) | Non-negotiable principles | Claude |
| `DATA_DISCOVERY.md` | Investigation protocol + dated log of finds | Claude + future maintainer |
| `PLAN.md` | Architecture intent + things deliberately not done | Claude + maintainer |
| `PROGRESS.md` | Current state (data counts, known issues, next steps) | Claude |
| `README.md` | Human onboarding: how to bootstrap a fresh clone | Users |
| `RESEARCH.md`, `RESEARCH_MAPPING.md` | Original scouting notes | Reference |
