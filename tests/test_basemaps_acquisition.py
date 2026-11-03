from __future__ import annotations

from pathlib import Path

from geocanoe.acquisition import basemaps


def test_basemap_resource_uses_archival_mirror_with_upstream_provenance() -> None:
    assert basemaps.BASEMAP_RESOURCE["url"] == (
        "https://data.source.coop/dataforcanada/d4c-datapkg-statistical/"
        "archive/statcan/boundaries/2021/lpr_000b21a_e.zip"
    )
    assert basemaps.BASEMAP_RESOURCE["upstream_url"] == (
        "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
        "sip-pis/boundary-limites/files-fichiers/lpr_000b21a_e.zip"
    )


def test_acquire_basemap_downloads_mirror_and_extracts_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    expected = tmp_path / basemaps.BASEMAP_RESOURCE["expected_shapefile"]

    def fake_download(
        url: str,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        calls.append(("download", url, destination, overwrite))
        return destination

    def fake_extract(
        zip_path: Path,
        output_dir: Path,
        overwrite: bool = False,
    ) -> list[Path]:
        calls.append(("extract", zip_path, output_dir, overwrite))
        return [expected]

    monkeypatch.setattr(basemaps, "download_file", fake_download)
    monkeypatch.setattr(basemaps, "extract_basemap_archive", fake_extract)

    result = basemaps.acquire_basemap(tmp_path, overwrite=True)
    archive = tmp_path / basemaps.BASEMAP_RESOURCE["archive_name"]

    assert result == [expected]
    assert calls == [
        ("download", basemaps.BASEMAP_URL, archive, True),
        ("extract", archive, tmp_path, True),
    ]
