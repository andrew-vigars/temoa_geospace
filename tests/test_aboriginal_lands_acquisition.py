from __future__ import annotations

import zipfile
from pathlib import Path
from types import ModuleType

import geopandas as gpd
from shapely.geometry import Polygon

from geocanoe.acquisition import aboriginal_lands
from geocanoe.execution.bronze import (
    BRONZE_STAGE_ORDER,
    BronzeStageOptions,
    execute_bronze_stage,
)


TEST_STEM = "AL_TA_CA_2_188_eng"


def write_test_shapefile(directory: Path) -> Path:
    fields = {field: ["test"] for field in aboriginal_lands.REQUIRED_FIELDS}
    fields["ACCURACY"] = [1]
    frame = gpd.GeoDataFrame(
        fields,
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])],
        crs=aboriginal_lands.EXPECTED_CRS,
    )
    path = directory / f"{TEST_STEM}.shp"
    frame.to_file(path, engine="pyogrio")
    return path


def write_test_xml(path: Path) -> None:
    path.write_text(
        "<?xml version='1.0'?>"
        "<WMS_Capabilities xmlns='http://www.opengis.net/wms' version='1.3.0'>"
        "<Service><Title>CLSS Administrative Boundaries</Title></Service>"
        "<Capability><Layer><Layer><Name>"
        "Aboriginal_Lands_of_Canada_Legislative_Boundaries"
        "</Name></Layer></Layer></Capability></WMS_Capabilities>",
        encoding="utf-8",
    )


def test_archive_extract_and_shapefile_validation(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_shapefile = write_test_shapefile(source_dir)
    archive = tmp_path / aboriginal_lands.ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w") as zipped:
        for component in source_dir.glob(f"{source_shapefile.stem}.*"):
            zipped.write(component, component.name)

    output_dir = tmp_path / "raw" / "aboriginal_lands"
    extracted = aboriginal_lands.extract_archive(archive, output_dir)

    assert extracted == output_dir / source_shapefile.name
    assert aboriginal_lands.validate_archive(archive) == archive
    assert aboriginal_lands.validate_shapefile(extracted) == extracted


def test_capabilities_xml_validation(tmp_path: Path) -> None:
    xml_path = tmp_path / aboriginal_lands.XML_NAME
    write_test_xml(xml_path)
    assert aboriginal_lands.validate_capabilities_xml(xml_path) == xml_path


def test_acquisition_reuses_existing_shapefile_before_archive_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "aboriginal_lands"
    raw_dir.mkdir()
    shapefile = write_test_shapefile(raw_dir)
    xml_path = raw_dir / aboriginal_lands.XML_NAME
    write_test_xml(xml_path)
    calls: list[str] = []

    def fake_download(url: str, destination: Path, **kwargs: object) -> Path:
        calls.append(url)
        return destination

    monkeypatch.setattr(aboriginal_lands, "download_file", fake_download)
    result = aboriginal_lands.acquire_aboriginal_lands(raw_dir)

    assert result["shapefile"] == shapefile
    assert result["archive"] is None
    assert result["manifest"].is_file()
    assert calls == [aboriginal_lands.WMS_CAPABILITIES_URL]


def test_bronze_dispatches_aboriginal_lands_with_options(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    module = ModuleType("test_aboriginal_lands_stage")
    setattr(
        module,
        "acquire_aboriginal_lands",
        lambda **kwargs: calls.append(kwargs) or "complete",
    )
    options = BronzeStageOptions(
        overwrite=True,
        raw_aboriginal_lands_dir=tmp_path,
        keep_aboriginal_lands_archive=True,
    )

    result = execute_bronze_stage("aboriginal_lands", module, options)

    assert "aboriginal_lands" in BRONZE_STAGE_ORDER
    assert result == "complete"
    assert calls == [
        {
            "overwrite": True,
            "keep_archive": True,
            "raw_aboriginal_lands": tmp_path,
        }
    ]
