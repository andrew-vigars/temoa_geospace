from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from geocanoe.geospatial.aboriginal_lands import normalize_aboriginal_lands


def test_normalization_filters_and_dissolves_overlapping_features() -> None:
    source = gpd.GeoDataFrame(
        {
            "NID": ["a", "b"],
            "ALCODE": ["1", "2"],
            "NAME1": ["First", "Second"],
            "JUR1": ["ON", "ON"],
            "ALTYPE": ["Reserve", "Reserve"],
            "WEBREF": ["https://example/a", "https://example/b"],
            "geometry": [box(0, 0, 2_000, 1_000), box(1_000, 0, 3_000, 1_000)],
        },
        crs="EPSG:3347",
    )
    boundary = gpd.GeoDataFrame(
        {"geometry": [box(500, -100, 2_500, 1_100)]},
        crs="EPSG:3347",
    )

    features, dissolved = normalize_aboriginal_lands(
        source,
        boundary,
        output_crs="EPSG:3347",
        source_id="source",
        build_id="test",
        study_area="test_area",
        penalty_profile="legacy_reference_v1",
        penalty_enabled=True,
        configured_scalar=9.0,
    )

    assert len(features) == 2
    assert features["area_km2"].sum() == pytest.approx(4.0)
    assert dissolved["area_km2"].sum() == pytest.approx(3.0)
    assert dissolved["applied_scalar"].eq(9.0).all()


def test_disabled_penalty_preserves_geometry_but_applies_zero_scalar() -> None:
    source = gpd.GeoDataFrame(
        {
            "NID": ["a"],
            "ALCODE": ["1"],
            "NAME1": ["First"],
            "JUR1": ["ON"],
            "ALTYPE": ["Reserve"],
            "WEBREF": ["https://example/a"],
            "geometry": [box(0, 0, 1_000, 1_000)],
        },
        crs="EPSG:3347",
    )

    features, dissolved = normalize_aboriginal_lands(
        source,
        source[["geometry"]],
        output_crs="EPSG:3347",
        source_id="source",
        build_id="test",
        study_area="test_area",
        penalty_profile="no_penalties",
        penalty_enabled=False,
        configured_scalar=4.0,
    )

    assert not features.empty
    assert features["configured_scalar"].iloc[0] == 4.0
    assert features["applied_scalar"].iloc[0] == 0.0
    assert bool(dissolved["penalty_enabled"].iloc[0]) is False
