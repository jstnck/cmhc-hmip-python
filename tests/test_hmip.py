from cmhc.catalogue import Table
from cmhc.geographies import Geography


def test_fetch_table_builds_form_params(monkeypatch):
    """Verify fetch_table sends the right form keys without hitting the network."""
    from cmhc import hmip

    captured = {}

    class FakeResponse:
        content = b",col\r\nx,1\r\n"
        def raise_for_status(self):
            pass

    def fake_post(url, data, timeout):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse()

    monkeypatch.setattr(hmip._client, "post", fake_post)

    table = Table(
        survey="Rms", series="Vacancy Rate", dimension="Bedroom Type",
        breakdown="Historical Time Periods", table_id="2.2.1",
        filters={"dwelling_type_desc_en": ["Row", "Apartment"]},
    )
    geo = Geography(name="Vancouver", geography_id="2410", geography_type_id=3)
    hmip.fetch_table(table, geo, year=2024)

    assert captured["url"] == hmip.EXPORT_URL
    assert captured["data"]["TableId"] == "2.2.1"
    assert captured["data"]["GeographyId"] == "2410"
    assert captured["data"]["GeographyTypeId"] == 3
    assert captured["data"]["exportType"] == "csv"
    assert captured["data"]["ForTimePeriod.Year"] == 2024
    assert captured["data"]["Frequency"] == "Annual"  # auto-inferred from year
    assert captured["data"]["AppliedFilters[0].Key"] == "dwelling_type_desc_en"
    # Multi-value filters are passed as lists; httpx serializes them as
    # repeated form keys (which is what HMIP expects).
    assert captured["data"]["AppliedFilters[0].Value"] == ["Row", "Apartment"]
