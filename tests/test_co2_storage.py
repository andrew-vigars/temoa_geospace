from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from geocanoe.acquisition.co2_storage import (
    acquire_canco2_storage,
    find_latest_raw_storage_gpkg,
    resolve_source_gpkg,
)
from geocanoe.geospatial.co2_storage import build_storage_products


def test_acquisition_selects_and_pins_timestamped_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repos" / "temoa-upstream" / "temoa_geospace"
    source_dir = (
        tmp_path
        / "repos"
        / "canco2-storage"
        / "data"
        / "processed"
        / "unified_storage"
    )
    raw_dir = project_root / "data_files" / "raw" / "canco2_storage"
    project_root.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    older = source_dir / "20260101_01_CanadaGeologicalStorageUnified_AV_v1.gpkg"
    newer = source_dir / "20260201_01_CanadaGeologicalStorageUnified_AV_v2.gpkg"
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    newer.with_suffix(".md").write_text("metadata", encoding="utf-8")
    monkeypatch.delenv("CANCO2_STORAGE_GPKG", raising=False)
    monkeypatch.delenv("CANCO2_STORAGE_REPO", raising=False)

    assert resolve_source_gpkg(project_root=project_root) == newer
    acquired = acquire_canco2_storage(
        project_root=project_root,
        destination_dir=raw_dir,
    )
    assert acquired.read_bytes() == b"newer"
    assert acquired.with_suffix(".md").is_file()
    assert find_latest_raw_storage_gpkg(raw_dir) == acquired
    assert (raw_dir / "selected_release.txt").read_text(
        encoding="utf-8"
    ).strip() == newer.name


def test_storage_products_preserve_topology_and_union_capacity_coverage() -> None:
    regions = gpd.GeoDataFrame(
        {
            "region": ["R0", "R1"],
            "site_id": [0, 1],
            "geometry": [box(0, 0, 10, 10), box(10, 0, 20, 10)],
        },
        crs="EPSG:3347",
    )
    shared_geometry = box(5, 0, 15, 10)
    storage_features = gpd.GeoDataFrame(
        {
            "storage_feature_id": ["F1", "F2", "F3"],
            "storage_unit_id": ["U1", "U2", "U3"],
            "source_dataset": ["NATCARB", "ATLANTIC_COS", "BC_STORAGE_ATLAS"],
            "source_layer": ["one", "two", "three"],
            "storage_type": ["saline_aquifer", None, "saline_aquifer"],
            "representation": ["grid", "prospectivity", "resource"],
            "assessment_type": ["quantitative", "qualitative", "quantitative"],
            "data_class": ["capacity", "prospectivity", "capacity"],
            "capacity_data": [1, 0, 1],
            "injectivity_status": ["unknown", "unknown", "unknown"],
            "geometry": [shared_geometry, box(16, 0, 20, 10), shared_geometry],
        },
        crs="EPSG:3347",
    )
    storage_units = pd.DataFrame({"storage_unit_id": ["U1", "U2", "U3"]})
    storage_assessments = pd.DataFrame(
        {
            "storage_feature_id": ["F1", "F2", None],
            "storage_unit_id": ["U1", "U2", "U3"],
            "assessment_scope": ["feature", "feature", "unit"],
            "storage_p10_tonnes": [0, 0, 0],
            "storage_p50_tonnes": [100, 0, 0],
            "storage_p90_tonnes": [0, 0, 0],
            "theoretical_storage_tonnes": [0, 0, 200],
            "effective_storage_tonnes": [0, 0, 0],
        }
    )

    evidence, crosswalk = build_storage_products(
        regions,
        storage_features,
        storage_units,
        storage_assessments,
    )

    assert len(evidence) == 2
    assert len(crosswalk) == 5
    assert set(crosswalk["storage_feature_id"]) == {"F1", "F2", "F3"}
    assert evidence["storage_accessible"].all()
    assert evidence["has_any_capacity_evidence"].all()
    assert evidence.set_index("region").loc["R0", "has_natcarb"]
    assert evidence.set_index("region").loc["R1", "has_atlantic_cos"]
    assert evidence.set_index("region").loc["R1", "has_bc_storage_atlas"]
    assert {
        "natcarb_coverage_fraction",
        "bc_coverage_fraction",
        "atlantic_coverage_fraction",
    }.issubset(evidence.columns)
    assert evidence["capacity_evidence_coverage_fraction"].round(8).tolist() == [
        0.5,
        0.5,
    ]
