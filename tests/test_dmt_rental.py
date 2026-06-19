"""Regression tests for the rental-mart correctness fixes (PROGRESS.md correctness pass).

Covers the three build-time helpers added to fix:
  #1 duplicate publication paths  -> _add_is_canonical
  #3 meaningless alphabetical sort -> _build_dimension_values
  #4 overloaded NULL reliability   -> _apply_reliability_sentinel

The helpers live in scripts/build_dmt_rental.py; we add scripts/ to the path to
import them without running main() (it's __main__-guarded).
"""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_dmt_rental as bm  # noqa: E402


# --- #3 sort_order ---------------------------------------------------------

def test_dimension_values_orders_studio_first_total_last():
    fact = pl.DataFrame({
        "dimension": ["Bedroom Type"] * 5,
        "category":  ["Total", "Studio", "2 Bedroom", "1 Bedroom", "3 Bedroom +"],
    })
    dv = bm._build_dimension_values(fact)
    order = dv.sort("sort_order")["category"].to_list()
    assert order == ["Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom +", "Total"]


def test_dimension_values_unknown_category_appended_not_dropped():
    fact = pl.DataFrame({
        "dimension": ["Bedroom Type", "Bedroom Type"],
        "category":  ["Total", "Surprise New Category"],
    })
    dv = bm._build_dimension_values(fact)
    cats = dv.sort("sort_order")["category"].to_list()
    assert cats == ["Total", "Surprise New Category"]  # known first, unknown after


# --- #4 reliability sentinel ----------------------------------------------

def test_reliability_sentinel_only_touches_non_suppressed_nulls():
    fact = pl.DataFrame({
        "value":         [100.0, 200.0, None],
        "reliability":   ["a",   None,  None],
        "is_suppressed": [False, False, True],
    })
    out = bm._apply_reliability_sentinel(fact)
    assert out["reliability"].to_list() == ["a", "n/a", None]
    # Invariant: reliability IS NULL <=> is_suppressed
    null_rel = out.filter(pl.col("reliability").is_null())
    assert null_rel["is_suppressed"].all()


# --- #1 is_canonical -------------------------------------------------------

def _obs(table_id, value, metric_id=1, geo_id="CMA:1", period="2025-10-01",
         dimension="Bedroom Type", category="2 Bedroom"):
    return {"metric_id": metric_id, "geo_id": geo_id, "period": period,
            "dimension": dimension, "category": category,
            "table_id": table_id, "value": value}


def test_is_canonical_keeps_exactly_one_per_logical_cell():
    fact = pl.DataFrame([
        _obs("2.1.1.2", 2.5),  # snapshot path
        _obs("2.1.1.2", 2.5),  # exact duplicate row
        _obs("2.2.1",   2.5),  # time-series path
    ])
    out = bm._add_is_canonical(fact, ts_table_ids={"2.2.1"})
    assert out["is_canonical"].sum() == 1
    # Time-series path wins the tie-break.
    canon = out.filter(pl.col("is_canonical"))
    assert canon["table_id"].to_list() == ["2.2.1"]


def test_is_canonical_independent_per_cell():
    fact = pl.DataFrame([
        _obs("2.2.1", 2.5, category="2 Bedroom"),
        _obs("2.1.1.2", 2.5, category="2 Bedroom"),
        _obs("2.2.1", 1.0, category="1 Bedroom"),
    ])
    out = bm._add_is_canonical(fact, ts_table_ids={"2.2.1"})
    # One canonical per distinct category.
    per_cat = out.filter(pl.col("is_canonical")).group_by("category").len()
    assert set(per_cat["len"].to_list()) == {1}
    assert out["is_canonical"].sum() == 2
