from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.registry import (
    GeospatialSourceRegistry,
    load_geospatial_source_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "registry" / "geospatial_sources.yaml"


def test_committed_registry_resolves_nhn_source() -> None:
    registry = GeospatialSourceRegistry(REGISTRY_PATH, repo_root=PROJECT_ROOT)

    source = registry.get("nrcan_nhn_hhyd_national_en")

    assert source["bronze"]["source_crs"] == "EPSG:4617"
    assert registry.resolve_bronze_path(source["id"]).name == "rhn_nhn_hhyd.gpkg"
    assert registry.resolve_acquisition_manifest_path(source["id"]).name == (
        "nhn_acquisition_manifest.json"
    )
    assert registry.domain(source["id"], "water_definition")["values"]["lake"] == 4
    assert registry.layer(source["id"], "waterbody_polygons")["source_layer"] == (
        "nhn_hhyd_Waterbody_2"
    )


def test_registry_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    source = REGISTRY_PATH.read_text(encoding="utf-8")
    entry = source.split("  - id:", maxsplit=1)[1]
    invalid = source + "  - id:" + entry
    path = tmp_path / "duplicates.yaml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_geospatial_source_registry(path)
