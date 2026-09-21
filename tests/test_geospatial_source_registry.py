from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.registry import (
    GeospatialSourceRegistry,
    load_geospatial_source_registry,
)
from geocanoe.execution.bronze import BRONZE_STAGE_ORDER


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "registry" / "geospatial_sources.yaml"


def test_committed_registry_covers_current_bronze_stages() -> None:
    registry = GeospatialSourceRegistry(REGISTRY_PATH, repo_root=PROJECT_ROOT)

    assert registry.list_ids() == (
        "statcan_2021_province_territory_boundaries",
        "nrcan_aboriginal_lands_national_en",
        "statcan_nrn_national_bilingual",
        "nrcan_nhn_hhyd_national_en",
        "eccc_large_facility_ghg_2024",
        "canco2_unified_storage_selected_release",
    )
    assert tuple(
        registry.get(source_id)["acquisition"]["stage"]
        for source_id in registry.list_ids()
    ) == BRONZE_STAGE_ORDER


def test_committed_registry_resolves_nhn_source() -> None:
    registry = GeospatialSourceRegistry(REGISTRY_PATH, repo_root=PROJECT_ROOT)

    source = registry.get("nrcan_nhn_hhyd_national_en")

    assert registry.artifact(source["id"], "hydrographic_features")[
        "source_crs"
    ] == "EPSG:4617"
    assert registry.resolve_bronze_path(source["id"]).name == "rhn_nhn_hhyd.gpkg"
    assert registry.resolve_acquisition_manifest_path(source["id"]).name == (
        "nhn_acquisition_manifest.json"
    )
    assert registry.domain(source["id"], "water_definition")["values"]["lake"] == 4
    assert registry.layer(source["id"], "waterbody_polygons")["source_layer"] == (
        "nhn_hhyd_Waterbody_2"
    )


def test_registry_resolves_fixed_and_globbed_artifacts(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    fixed = raw_dir / "fixed.gpkg"
    fixed.touch()
    for name in ("release_2.shp", "release_1.shp"):
        (raw_dir / name).touch()
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """schema_version: 2
sources:
  - id: example
    kind: vector_dataset
    domain: test
    publisher: Test Publisher
    title: Test Source
    licence: Test Licence
    acquisition:
      stage: example
      module: example.acquisition
      refresh_policy: manual
      release_model: fixed
    bronze:
      root: raw
      primary_artifact: fixed
      artifacts:
        fixed:
          path: fixed.gpkg
          format: gpkg
        releases:
          glob: release_*.shp
          format: shapefile
          multiple: true
""",
        encoding="utf-8",
    )
    registry = GeospatialSourceRegistry(registry_path, repo_root=tmp_path)

    assert registry.resolve_bronze_path("example") == fixed
    assert [
        path.name
        for path in registry.resolve_artifact_paths("example", "releases")
    ] == ["release_1.shp", "release_2.shp"]


def test_registry_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    source = REGISTRY_PATH.read_text(encoding="utf-8")
    entry = source.split("  - id:", maxsplit=1)[1]
    invalid = source + "  - id:" + entry
    path = tmp_path / "duplicates.yaml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_geospatial_source_registry(path)


def test_registry_rejects_ambiguous_artifact_locator(tmp_path: Path) -> None:
    source = REGISTRY_PATH.read_text(encoding="utf-8")
    invalid = source.replace(
        "path: lpr_000b21a_e.shp\n          format: shapefile",
        "path: lpr_000b21a_e.shp\n          glob: '*.shp'\n          format: shapefile",
        1,
    )
    path = tmp_path / "ambiguous.yaml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        load_geospatial_source_registry(path)
