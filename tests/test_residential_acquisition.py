from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from types import ModuleType

import geopandas as gpd
from shapely.geometry import Polygon

from geocanoe.acquisition import residential
from geocanoe.execution.bronze import (
    BRONZE_STAGE_ORDER,
    BronzeStageOptions,
    execute_bronze_stage,
)


def write_boundary(directory: Path) -> Path:
    count = residential.EXPECTED_BOUNDARY_FEATURES
    frame = gpd.GeoDataFrame(
        {
            "DAUID": [f"{index:08d}" for index in range(count)],
            "DGUID": [f"2021S0512{index:08d}" for index in range(count)],
            "LANDAREA": [1.0] * count,
            "PRUID": ["35"] * count,
        },
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])] * count,
        crs=residential.EXPECTED_BOUNDARY_CRS,
    )
    path = directory / residential.BOUNDARY_SHAPEFILE_NAME
    frame.to_file(path, engine="pyogrio")
    return path


def write_population_table(path: Path) -> None:
    fields = sorted(residential.REQUIRED_TABLE_FIELDS) + [
        residential.POPULATION_DENSITY_FIELD
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow({field: "test" for field in fields})


def test_download_reports_streamed_byte_progress(
    tmp_path: Path, monkeypatch
) -> None:
    class Response:
        status_code = 200
        headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == residential.CHUNK_SIZE
            yield b"abc"
            yield b"def"

    progress_options: dict[str, object] = {}
    progress_updates: list[int] = []

    class Progress:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def update(self, size: int) -> None:
            progress_updates.append(size)

    def fake_tqdm(**kwargs: object) -> Progress:
        progress_options.update(kwargs)
        return Progress()

    monkeypatch.setattr(
        residential.requests, "get", lambda *args, **kwargs: Response()
    )
    monkeypatch.setattr(residential, "tqdm", fake_tqdm)
    destination = tmp_path / residential.BOUNDARY_ARCHIVE_NAME

    result = residential.download_file("https://example.test/data.zip", destination)

    assert result.read_bytes() == b"abcdef"
    assert progress_options["total"] == 6
    assert progress_options["unit"] == "B"
    assert progress_updates == [3, 3]


def test_archive_validation_and_extraction(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    boundary = write_boundary(source)
    boundary_archive = tmp_path / residential.BOUNDARY_ARCHIVE_NAME
    with zipfile.ZipFile(boundary_archive, "w") as archive:
        for component in source.glob(f"{boundary.stem}.*"):
            archive.write(component, f"nested/{component.name}")

    table = source / residential.POPULATION_TABLE_NAME
    write_population_table(table)
    table_archive = tmp_path / residential.POPULATION_ARCHIVE_NAME
    with zipfile.ZipFile(table_archive, "w") as archive:
        archive.write(table, f"nested/{table.name}")

    output = tmp_path / "raw" / "residential"
    residential.validate_boundary_archive(boundary_archive)
    residential.validate_population_archive(table_archive)
    residential._extract_flattened(boundary_archive, output)
    residential._extract_flattened(table_archive, output)

    assert residential.validate_boundary_shapefile(output / boundary.name)
    assert residential.validate_population_table(output / table.name)


def test_acquisition_reuses_valid_outputs_without_download(
    tmp_path: Path, monkeypatch
) -> None:
    boundary = write_boundary(tmp_path)
    table = tmp_path / residential.POPULATION_TABLE_NAME
    write_population_table(table)

    def unexpected_download(*args: object, **kwargs: object) -> Path:
        raise AssertionError("valid existing Bronze outputs should be reused")

    monkeypatch.setattr(residential, "download_file", unexpected_download)
    result = residential.acquire_residential(tmp_path)

    assert result == {"boundaries": boundary, "population_table": table}


def test_bronze_dispatches_residential_with_output_override(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    module = ModuleType("test_residential_stage")
    setattr(
        module,
        "acquire_residential",
        lambda **kwargs: calls.append(kwargs) or "complete",
    )
    options = BronzeStageOptions(
        overwrite=True,
        raw_residential_dir=tmp_path,
    )

    result = execute_bronze_stage("residential", module, options)

    assert "residential" in BRONZE_STAGE_ORDER
    assert result == "complete"
    assert calls == [{"overwrite": True, "raw_residential": tmp_path}]
