from pathlib import Path

from geocanoe.acquisition import emissions


def test_emissions_sources_recover_removed_eccc_static_files() -> None:
    csv = emissions.EMISSIONS_RESOURCE["csv"]
    geojson = emissions.EMISSIONS_RESOURCE["geojson"]

    assert csv["upstream_url"].startswith("https://indicators-map.canada.ca/")
    assert csv["url"].startswith("https://web.archive.org/web/20260503123643id_/")
    assert geojson["upstream_url"].endswith("AirEmissions_GHG_2024.zip")
    assert geojson["url"].startswith(emissions.EMISSIONS_ARCGIS_LAYER_URL)
    assert "where=Year%3D2024" in geojson["url"]
    assert "outSR=4326" in geojson["url"]


def test_get_raw_emissions_data_downloads_replacement_resources(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path, bool]] = []

    def fake_download(
        url: str,
        destination: Path,
        overwrite: bool = False,
        max_retries: int = emissions.MAX_RETRIES,
    ) -> Path:
        del max_retries
        calls.append((url, destination, overwrite))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("test", encoding="utf-8")
        return destination

    monkeypatch.setattr(emissions, "RAW_EMISSIONS", tmp_path)
    monkeypatch.setattr(emissions, "download_file", fake_download)

    csv_path, geojson_path = emissions.get_raw_emissions_data(overwrite=True)

    assert csv_path == tmp_path / emissions.EMISSIONS_RESOURCE["csv"]["filename"]
    assert geojson_path == tmp_path / emissions.EMISSIONS_RESOURCE["geojson"]["expected_file"]
    assert calls == [
        (emissions.EMISSIONS_CSV_ARCHIVE_URL, csv_path, True),
        (emissions.EMISSIONS_ARCGIS_GEOJSON_URL, geojson_path, True),
    ]
