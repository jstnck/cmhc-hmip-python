"""Tests for the static matrix engine + registry.

Engine mechanics are tested hermetically: synthetic xlsx fixtures (built with
xlsxwriter) exercise the three layout families through `matrix.run` with a fake
provenance row. A separate integration test confirms `runner.parse` wires a real
catalogue slug to its spec and stamps provenance from the committed catalogue.
"""

from datetime import date

import polars as pl
import pytest
import xlsxwriter

from cmhc.static import catalogue, parse
from cmhc.static.catalogue import StaticTable
from cmhc.static.matrix import MatrixSpec, Sheets, parse_period, parse_value, run
from cmhc.static.schema import COLUMNS

FAKE = StaticTable(
    table_id="fake-table", survey="Test Survey", section="test",
    title="Test Metric", page_url="", asset_url=None, size_bytes=None, last_modified=None,
)


def _write(path, sheets):
    """sheets: dict[sheet_name, list[row]]. None cells are left blank."""
    wb = xlsxwriter.Workbook(path)
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for r, cells in enumerate(rows):
            for c, val in enumerate(cells):
                if val is not None:
                    ws.write(r, c, val)
    wb.close()
    return path


# --- unit: parsers ---------------------------------------------------------

def test_parse_period():
    assert parse_period("2012Q3") == date(2012, 7, 1)
    assert parse_period("2012/Q1") == date(2012, 1, 1)
    assert parse_period("2021") == date(2021, 1, 1)
    assert parse_period(None) is None
    assert parse_period("Notes") is None
    assert parse_period("2012Q5") is None


def test_parse_value():
    s = frozenset({"", "[x]", "x"})
    assert parse_value("1,234.5", s) == 1234.5
    assert parse_value("0.38", s) == 0.38
    assert parse_value("[x]", s) is None
    assert parse_value("", s) is None
    assert parse_value(None, s) is None


# --- family A: single sheet, geographies × periods, metric from title ------

@pytest.fixture
def file_a(tmp_path):
    return _write(tmp_path / "a.xlsx", {"Sheet1": [
        ["Metric (%)"], ["subtitle"],
        ["Geography", "2012Q3", "2012Q4"],
        ["Canada", 0.38, 0.37],
        ["Provinces"],                 # divider — no values
        ["Ontario", 0.31, 0.30],
        ["Source: somewhere"],          # footnote — no values
    ]})


def test_family_a(file_a):
    spec = MatrixSpec(sheets=Sheets(mode="single"), axis="period")
    df = run(spec, FAKE, file_a)
    assert df.columns == COLUMNS
    assert df.shape == (4, 9)  # Canada×2 + Ontario×2; divider + footnote dropped
    assert set(df["geography"].unique()) == {"Canada", "Ontario"}
    assert df["category"].unique().to_list() == ["Test Metric"]  # from catalogue title
    assert set(df["period"].unique()) == {date(2012, 7, 1), date(2012, 10, 1)}
    assert df["survey"].unique().to_list() == ["Test Survey"]    # from provenance


# --- family B: one sheet per period, geographies × categories --------------

@pytest.fixture
def file_b_year(tmp_path):
    sheet = lambda a, b: [["Title"], ["Geography1", "CatA", "CatB"],
                          ["Canada", a, b], ["Ontario", a + 1, b + 1]]
    return _write(tmp_path / "byyear.xlsx", {
        "Notes": [["ignore me"]], "2021": sheet(10, 20), "2016": sheet(1, 2),
    })


def test_family_b_year(file_b_year):
    spec = MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="period", skip=frozenset({"Notes"})),
        axis="category", header_marker="Geography1",
    )
    df = run(spec, FAKE, file_b_year)
    assert df.shape == (8, 9)  # 2 years × 2 geos × 2 categories
    assert set(df["period"].unique()) == {date(2021, 1, 1), date(2016, 1, 1)}
    assert set(df["category"].unique()) == {"CatA", "CatB"}
    assert "Notes" not in df["category"]  # Notes sheet skipped


# --- family B: one sheet per category (tenure), geographies × periods,
#               with interleaved reliability columns ------------------------

@pytest.fixture
def file_b_tenure(tmp_path):
    sheet = [["Title"],
             ["Geography", "2006", "Data quality", "2007", "Data quality"],
             ["Canada", 100, "a", 110, "b"]]
    return _write(tmp_path / "bytenure.xlsx", {
        "Notes": [["x"]], "Renter": sheet, "Owner": sheet,
    })


def test_family_b_tenure(file_b_tenure):
    spec = MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=frozenset({"Notes"})),
        axis="period", header_marker="Geography", reliability="Data quality",
    )
    df = run(spec, FAKE, file_b_tenure)
    assert df.shape == (4, 9)  # 2 tenures × 1 geo × 2 periods
    assert set(df["category"].unique()) == {"Renter", "Owner"}  # from sheet name
    assert set(df["period"].unique()) == {date(2006, 1, 1), date(2007, 1, 1)}
    assert set(df["reliability"].unique()) == {"a", "b"}  # Data quality columns


def test_reliability_columns_not_treated_as_values(file_b_tenure):
    spec = MatrixSpec(
        sheets=Sheets(mode="per_sheet", dimension="category", skip=frozenset({"Notes"})),
        axis="period", header_marker="Geography", reliability="Data quality",
    )
    df = run(spec, FAKE, file_b_tenure)
    # 'Data quality' must not leak in as a period/value row.
    assert df["period"].null_count() == 0
    assert df.filter(pl.col("value").is_null()).height == 0


# --- integration: registry + catalogue wiring ------------------------------

def test_runner_resolves_spec_and_provenance(tmp_path):
    # A real catalogue slug + a synthetic file: provenance must come from the
    # committed catalogue, not be hardcoded.
    slug = "mortgage-delinquency-rate-canada-provinces-cmas"
    path = _write(tmp_path / "m.xlsx", {"Mortgage delinquency rate": [
        ["Mortgage delinquency rate (%)"], ["sub"],
        ["Geography", "2012Q3", "2012Q4"], ["Canada", 0.38, 0.37],
    ]})
    df = parse(slug, path)
    expected = catalogue.get(slug)
    assert df["survey"].unique().to_list() == [expected.survey]
    assert df["table_id"].unique().to_list() == [expected.table_id]
    assert df["source"].unique().to_list() == ["static"]


def test_runner_unknown_slug(tmp_path):
    with pytest.raises(KeyError):
        parse("not-a-real-table", tmp_path / "x.xlsx")
