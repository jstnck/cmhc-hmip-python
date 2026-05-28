from datetime import date

import pytest

from cmhc.tidy import _parse_period, tidy


SIMPLE_CSV = b""" Historical Starts by Dwelling Type \r
1990 to 2025\r
\r
,Single,Row,Total,\r
Jan 1990,"6,367",238,"7,024",\r
Feb 1990,"4,879",143,"5,255",\r
\r
Notes\r
Source,CMHC\r
"""


RELIABILITY_CSV = b""" Historical Vacancy Rates by Bedroom Type \r
1990 to 2024 Row / Apartment,Row,Apartment October,April\r
\r
,Studio,,1 Bedroom,,Total,,\r
1990 October,0.7,a ,0.8,a ,0.9,a ,\r
1991 October,1.8,b ,2.1,a ,2.2,a ,\r
2023 October,**,d ,3.5,c ,3.4,b ,\r
\r
Notes\r
Source,CMHC Rental Market Survey\r
"""


def test_simple_format():
    df = tidy(SIMPLE_CSV, breakdown="Historical Time Periods")
    assert df.shape == (6, 5)  # 2 periods × 3 categories, 5 cols
    assert df.columns == ["period", "sub_geography", "category", "value", "reliability"]
    assert set(df["category"].unique()) == {"Single", "Row", "Total"}
    # Time series: period populated, sub_geography null throughout
    assert df["sub_geography"].is_null().all()
    # Numbers with commas parse correctly
    jan_single = df.filter((df["period"] == date(1990, 1, 1)) & (df["category"] == "Single"))
    assert jan_single["value"][0] == 6367.0
    # No reliability column in this format
    assert df["reliability"].is_null().all()


def test_reliability_format():
    df = tidy(RELIABILITY_CSV, breakdown="Historical Time Periods")
    assert df.shape == (9, 5)  # 3 periods × 3 categories
    assert set(df["category"].unique()) == {"Studio", "1 Bedroom", "Total"}
    # Reliability codes attach to the correct value column, with whitespace stripped
    studio_1991 = df.filter((df["period"] == date(1991, 10, 1)) & (df["category"] == "Studio"))
    assert studio_1991["value"][0] == 1.8
    assert studio_1991["reliability"][0] == "b"
    rel_values = set(df.filter(df["reliability"].is_not_null())["reliability"].to_list())
    assert rel_values == {"a", "b", "c", "d"}


def test_geo_breakdown_populates_sub_geography():
    """Snapshot/Provinces/Centres tables put a geo name in the first column."""
    csv = b"\r\n,Single,All,\r\nQuebec,180,359,\r\nOntario,433,1013,\r\n\r\n"
    df = tidy(csv, breakdown="Provinces")
    assert df.shape == (4, 5)
    assert df["period"].is_null().all()
    assert set(df["sub_geography"].unique()) == {"Quebec", "Ontario"}
    quebec_all = df.filter((df["sub_geography"] == "Quebec") & (df["category"] == "All"))
    assert quebec_all["value"][0] == 359.0


def test_suppression_becomes_null():
    df = tidy(RELIABILITY_CSV)
    # The Studio value for 2023 October is "**" — should parse to null
    suppressed = df.filter((df["period"] == date(2023, 10, 1)) & (df["category"] == "Studio"))
    assert suppressed["value"][0] is None
    # But reliability code is still captured
    assert suppressed["reliability"][0] == "d"


def test_drops_empty_index_rows():
    """HMIP sometimes has a summary row with an empty index — drop it."""
    csv = b"""\r
,A,B,\r
Jan 1990,1,2,\r
,99,100,\r
\r
"""
    df = tidy(csv)
    assert df.shape == (2, 5)  # one period × 2 cats; summary row dropped
    assert (df["period"] == date(1990, 1, 1)).all()


SNAPSHOT_GEO_BREAKDOWN_CSV = b""" Vacancy Rates by Rent Range by Provinces\r
October 2025 Row / Apartment\r
,Less Than $750,,$750 - $999,,Total,,\r
Ontario,1.4,a ,1.1,a ,3.2,a ,\r
Quebec,1.6,c ,1.1,a ,2.7,a ,\r
,1.5,a ,1.1,a ,3.0,a ,\r
\r
Notes\r
Source,CMHC\r
"""


def test_subtitle_period_attached_for_geo_breakdown():
    """Snapshot tables put the period in the subtitle above the header. We
    must extract it; otherwise a 2024 pull and a 2025 pull are indistinguishable
    in the parquet."""
    df = tidy(SNAPSHOT_GEO_BREAKDOWN_CSV, breakdown="Provinces")
    # Ontario + Quebec × 3 categories = 6 rows; summary row dropped
    assert df.shape == (6, 5)
    assert (df["period"] == date(2025, 10, 1)).all()
    assert set(df["sub_geography"].unique()) == {"Ontario", "Quebec"}


SINGLE_GEO_QUERY_CSV = b""" Average Rent by Bedroom Type by Census Subdivision\r
October 2025 Row / Apartment\r
,Studio,,1 Bedroom,,2 Bedroom,,Total,,\r
,**,,"1,216",b ,"1,727",a ,"1,740",a ,\r
\r
Notes\r
"""


def test_single_geo_row_preserved():
    """A sub-CMA breakdown queried at the geo itself returns one row with an
    empty first cell — that row IS the data for the queried geo. Don't drop it."""
    df = tidy(SINGLE_GEO_QUERY_CSV, breakdown="Census Subdivision")
    # 4 categories, one row each — all sub_geography null
    assert df.shape == (4, 5)
    assert df["sub_geography"].is_null().all()
    assert (df["period"] == date(2025, 10, 1)).all()
    # Suppression sentinel parsed correctly
    studio = df.filter(df["category"] == "Studio")
    assert studio["value"][0] is None
    one_bd = df.filter(df["category"] == "1 Bedroom")
    assert one_bd["value"][0] == 1216.0


def test_no_data_raises():
    with pytest.raises(ValueError, match="No data table"):
        tidy(b"Title\r\nNo data available.\r\n")


def test_handles_latin1():
    """HMIP encodes accented characters in latin1 (e.g. Quebec city names)."""
    raw = b"\r\n,col,\r\nMontr\xe9al,1,\r\n"
    df = tidy(raw, breakdown="Provinces")
    assert df["sub_geography"].to_list() == ["Montréal"]


SNAPSHOT_CSV = b""" Seniors' Spaces by Unit Type  \r
2021\r
Standard,Non-Standard,Unknown,Total,\r
"15,919",839,"1,227","17,985",\r
\r
Source,CMHC Seniors Housing Survey\r
"""


def test_snapshot_format():
    df = tidy(SNAPSHOT_CSV)
    assert df.shape == (4, 5)
    assert df.columns == ["period", "sub_geography", "category", "value", "reliability"]
    # Period comes from the '2021' line above the header
    assert (df["period"] == date(2021, 1, 1)).all()
    assert df["sub_geography"].is_null().all()
    assert df["category"].to_list() == ["Standard", "Non-Standard", "Unknown", "Total"]
    # Numbers parse correctly, including quoted ones with embedded commas
    assert df["value"].to_list() == [15919.0, 839.0, 1227.0, 17985.0]
    assert df["reliability"].is_null().all()


def test_snapshot_quoted_dollar_categories():
    """Categories can be quoted strings containing commas (e.g. rent ranges)."""
    raw = b""" Title \r\n2021\r\n"Less Than $1,500","$1,500 - $1,999","Total",\r\n10.9,7.3,82.0,\r\n"""
    df = tidy(raw)
    assert df["category"].to_list() == ["Less Than $1,500", "$1,500 - $1,999", "Total"]
    assert df["value"].to_list() == [10.9, 7.3, 82.0]


def test_snapshot_with_no_parseable_period():
    """If no period line is recognizable, period is null but parsing still succeeds."""
    raw = b""" Title \r\nA,B,C,\r\n1,2,3,\r\n"""
    df = tidy(raw)
    assert df["period"].is_null().all()
    assert df["value"].to_list() == [1.0, 2.0, 3.0]


@pytest.mark.parametrize("s,expected", [
    # Short-month / year (Scss monthly)
    ("Jan 1990", date(1990, 1, 1)),
    ("Feb 1990", date(1990, 2, 1)),
    ("Dec 2025", date(2025, 12, 1)),
    # Year / full-month (Rms annual, some Scss)
    ("1990 March", date(1990, 3, 1)),
    ("1991 October", date(1991, 10, 1)),
    ("2026 January", date(2026, 1, 1)),
    # Quarterly — start-of-period
    ("1990/Q1", date(1990, 1, 1)),
    ("1990/Q2", date(1990, 4, 1)),
    ("1990/Q3", date(1990, 7, 1)),
    ("1990/Q4", date(1990, 10, 1)),
    # Bare year (Census, Srms, Seniors, Core Housing Need — annual)
    ("2006", date(2006, 1, 1)),
    ("1990", date(1990, 1, 1)),
    ("2025", date(2025, 1, 1)),
    # Null-ish input
    (None, None),
    ("", None),
    ("   ", None),
    # Unrecognized formats → None (silent, not raise)
    ("just a string", None),
    ("1800", None),         # year out of plausible range
    ("99999", None),        # too many digits
    ("1990/Q5", None),
])
def test_parse_period(s, expected):
    assert _parse_period(s) == expected
