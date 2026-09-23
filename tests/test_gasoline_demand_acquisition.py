from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from types import ModuleType

import geopandas as gpd
from shapely.geometry import Polygon

from geocanoe.acquisition import gasoline_demand
from geocanoe.execution.bronze import (
    BRONZE_STAGE_ORDER,
    BronzeStageOptions,
    execute_bronze_stage,
)


def write_fuel_sales(path: Path) -> None:
    fields = sorted(gasoline_demand.REQUIRED_FUEL_SALES_FIELDS) + [
        "Type of fuel sales"
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                **{field: "test" for field in fields},
                "REF_DATE": "2024",
                "GEO": "Ontario",
                "UOM": "Litres",
                "SCALAR_FACTOR": "thousands",
                "VALUE": "123",
                "Type of fuel sales": gasoline_demand.NET_GASOLINE_LABEL,
            }
        )


def write_population_centres(directory: Path) -> Path:
    count = gasoline_demand.EXPECTED_POPULATION_CENTRE_RECORDS
    values: dict[str, list[object]] = {
        field: [f"value-{index}" for index in range(count)]
        for field in gasoline_demand.REQUIRED_POPULATION_CENTRE_FIELDS
    }
    unique_count = gasoline_demand.EXPECTED_POPULATION_CENTRES
    values["PCUID"] = [
        f"centre-{index if index < unique_count else index - unique_count}"
        for index in range(count)
    ]
    values["PCCLASS"] = ["1"] * count
    values["PRUID"] = ["35"] * count
    frame = gpd.GeoDataFrame(
        values,
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])] * count,
        crs=gasoline_demand.EXPECTED_POPULATION_CENTRE_CRS,
    )
    path = directory / gasoline_demand.POPULATION_CENTRE_SHAPEFILE_NAME
    frame.to_file(path, engine="pyogrio")
    path.with_suffix(".xml").write_text("<metadata />", encoding="utf-8")
    return path


def test_archive_validation_and_extraction(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    table = source / gasoline_demand.FUEL_SALES_TABLE_NAME
    metadata = source / gasoline_demand.FUEL_SALES_METADATA_NAME
    write_fuel_sales(table)
    metadata.write_text("metadata", encoding="utf-8")
    fuel_archive = tmp_path / gasoline_demand.FUEL_SALES_ARCHIVE_NAME
    with zipfile.ZipFile(fuel_archive, "w") as archive:
        archive.write(table, f"nested/{table.name}")
        archive.write(metadata, f"nested/{metadata.name}")

    centres = write_population_centres(source)
    centre_archive = tmp_path / gasoline_demand.POPULATION_CENTRE_ARCHIVE_NAME
    with zipfile.ZipFile(centre_archive, "w") as archive:
        for component in source.glob(f"{centres.stem}.*"):
            archive.write(component, f"nested/{component.name}")

    fuel_output = tmp_path / "raw" / "gasoline_demand" / "fuel_sales"
    centre_output = tmp_path / "raw" / "gasoline_demand" / "population_centres"
    gasoline_demand.validate_fuel_sales_archive(fuel_archive)
    gasoline_demand.validate_population_centres_archive(centre_archive)
    gasoline_demand._extract_flattened(fuel_archive, fuel_output)
    gasoline_demand._extract_flattened(centre_archive, centre_output)

    assert gasoline_demand.validate_fuel_sales_table(fuel_output / table.name)
    assert gasoline_demand.validate_csv_metadata(fuel_output / metadata.name)
    assert gasoline_demand.validate_population_centres(
        centre_output / centres.name
    )


def test_acquisition_reuses_valid_outputs_and_writes_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    fuel_dir = tmp_path / gasoline_demand.FUEL_SALES_DIRNAME
    centres_dir = tmp_path / gasoline_demand.POPULATION_CENTRES_DIRNAME
    fuel_dir.mkdir()
    centres_dir.mkdir()
    table = fuel_dir / gasoline_demand.FUEL_SALES_TABLE_NAME
    metadata = fuel_dir / gasoline_demand.FUEL_SALES_METADATA_NAME
    write_fuel_sales(table)
    metadata.write_text("metadata", encoding="utf-8")
    centres = write_population_centres(centres_dir)

    def unexpected_download(*args: object, **kwargs: object) -> Path:
        raise AssertionError("valid existing Bronze outputs should be reused")

    monkeypatch.setattr(gasoline_demand, "download_file", unexpected_download)
    result = gasoline_demand.acquire_gasoline_demand(tmp_path)
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))

    assert result["fuel_sales"] == table
    assert result["population_centres"] == centres
    assert manifest["stage"] == "gasoline_demand"
    assert manifest["reused_population_source"]["stage"] == "residential"
    assert manifest["methodology_reference"]["publisher"] == (
        "Natural Resources Canada"
    )


def test_bronze_dispatches_gasoline_demand_with_output_override(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    module = ModuleType("test_gasoline_demand_stage")
    setattr(
        module,
        "acquire_gasoline_demand",
        lambda **kwargs: calls.append(kwargs) or "complete",
    )
    options = BronzeStageOptions(
        overwrite=True,
        raw_gasoline_demand_dir=tmp_path,
    )

    result = execute_bronze_stage("gasoline_demand", module, options)

    assert "gasoline_demand" in BRONZE_STAGE_ORDER
    assert result == "complete"
    assert calls == [
        {"overwrite": True, "raw_gasoline_demand": tmp_path}
    ]
