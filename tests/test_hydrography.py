from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import box

from geocanoe.config import load_geospatial_build_config
from geocanoe.geospatial import hydrography


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "build_profiles" / "sample_build_profile.toml"
REGISTRY_PATH = PROJECT_ROOT / "registry" / "geospatial_sources.yaml"


def _waterbody_rows() -> gpd.GeoDataFrame:
    common = {
        "validity_date": "2024-01-01",
        "dataset_name": "synthetic NHN",
        "planimetric_accuracy": 10.0,
        "provider": 1,
        "permanency": 1,
        "isolated": 0,
        "code_spec": 1,
        "geonamedb": None,
        "lakeid_1": None,
        "lakeid_2": None,
        "lakename_1": None,
        "lakename_2": None,
        "rivid_1": None,
        "rivid_2": None,
        "rivname_1": None,
        "rivname_2": None,
    }
    return gpd.GeoDataFrame(
        [
            {**common, "nid": "large-lake", "water_definition": 4},
            {**common, "nid": "small-lake", "water_definition": 4},
            {**common, "nid": "reservoir", "water_definition": 5},
        ],
        geometry=[
            box(-79.8, 43.3, -79.4, 43.7),
            box(-79.2, 43.3, -79.19, 43.31),
            box(-79.0, 43.3, -78.6, 43.7),
        ],
        crs="EPSG:4617",
    )


def test_hydrography_build_filters_clips_documents_and_previews(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "rhn_nhn_hhyd.gpkg"
    metadata_path = tmp_path / "nhn_wms_capabilities.xml"
    acquisition_manifest_path = tmp_path / "nhn_acquisition_manifest.json"
    _waterbody_rows().to_file(
        source_path,
        layer="nhn_hhyd_Waterbody_2",
        driver="GPKG",
        engine="pyogrio",
    )
    metadata_path.write_text("<metadata/>", encoding="utf-8")
    acquisition_manifest_path.write_text("{}\n", encoding="utf-8")

    registry_text = REGISTRY_PATH.read_text(encoding="utf-8").replace(
        "root: data_files/raw/nhn",
        f"root: {tmp_path.as_posix()}",
    ).replace(
        "primary_key_field: id",
        "primary_key_field: fid",
    )
    registry_path = tmp_path / "geospatial_sources.yaml"
    registry_path.write_text(registry_text, encoding="utf-8")

    basemap_dir = tmp_path / "basemaps"
    basemap_dir.mkdir()
    boundary = gpd.GeoDataFrame(
        {"study_area": ["provinces_only"]},
        geometry=[box(-80.0, 43.0, -79.1, 44.0)],
        crs="EPSG:4617",
    ).to_crs("EPSG:3347")
    boundary.to_file(
        basemap_dir / "provinces_only_boundary_epsg3347.gpkg",
        driver="GPKG",
        engine="pyogrio",
    )
    monkeypatch.setattr(hydrography, "PROCESSED_BASEMAPS", basemap_dir)

    output_dir = tmp_path / "processed" / "nhn"
    config = load_geospatial_build_config(PROFILE_PATH)
    outputs = hydrography.run_hydrography_build(
        config,
        registry_path=registry_path,
        output_dir=output_dir,
    )

    result = gpd.read_file(
        outputs["gpkg"],
        layer="waterbody_polygons",
        engine="pyogrio",
    )
    assert result["source_feature_id"].tolist() == ["large-lake"]
    assert result["feature_class"].tolist() == ["lake"]
    assert result.crs.to_epsg() == 3347
    assert pyogrio.list_layers(outputs["gpkg"])[0, 0] == "waterbody_polygons"
    assert outputs["polygons_preview"].is_file()
    assert outputs["summary"].is_file()
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["source_id"] == "nrcan_nhn_hhyd_national_en"
    assert manifest["polygons"]["minimum_source_area_km2"] == {"lake": 50.0}
