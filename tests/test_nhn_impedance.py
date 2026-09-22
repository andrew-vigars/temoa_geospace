from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from geocanoe.geospatial.nhn_impedance import normalize_nhn_waterbodies


def build_waterbodies() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "source_id": ["nhn", "nhn"],
            "source_feature_id": ["lake-a", "lake-b"],
            "feature_class": ["lake", "lake"],
            "permanency_class": ["permanent", "permanent"],
            "study_area": ["test_area", "test_area"],
            "geometry": [
                box(0, 0, 2_000, 1_000),
                box(1_000, 0, 3_000, 1_000),
            ],
        },
        crs="EPSG:3347",
    )


def test_nhn_waterbodies_are_normalized_and_overlap_safe() -> None:
    features, dissolved = normalize_nhn_waterbodies(
        build_waterbodies(),
        output_crs="EPSG:3347",
        expected_source_id="nhn",
        build_id="test",
        study_area="test_area",
        penalty_profile="legacy_reference_v1",
        penalty_enabled=True,
        configured_scalar=9.0,
    )

    assert len(features) == 2
    assert features["area_km2"].sum() == pytest.approx(4.0)
    assert len(dissolved) == 1
    assert dissolved["area_km2"].sum() == pytest.approx(3.0)
    assert dissolved["source_feature_count"].iloc[0] == 2
    assert dissolved["applied_scalar"].iloc[0] == 9.0


def test_nhn_waterbody_source_identity_is_enforced() -> None:
    with pytest.raises(ValueError, match="source identity"):
        normalize_nhn_waterbodies(
            build_waterbodies(),
            output_crs="EPSG:3347",
            expected_source_id="different-source",
            build_id="test",
            study_area="test_area",
            penalty_profile="legacy_reference_v1",
            penalty_enabled=True,
            configured_scalar=9.0,
        )
