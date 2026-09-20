from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from geocanoe.geospatial.pipeline_impedance import (
    ScalarPenaltyLayer,
    calculate_pipeline_effective_distance,
)


def build_edges() -> gpd.GeoDataFrame:
    """Build two simple two-kilometre candidate edges for impedance tests."""

    return gpd.GeoDataFrame(
        {
            "edge_region": ["R0-R1", "R2-R3"],
            "distance_km": [2.0, 2.0],
            "geometry": [
                LineString([(0, 0), (2_000, 0)]),
                LineString([(0, 1_000), (2_000, 1_000)]),
            ],
        },
        crs="EPSG:3347",
    )


def test_effective_distance_combines_constant_and_feature_factors() -> None:
    edges = build_edges()
    water = gpd.GeoDataFrame(
        {
            "geometry": [
                box(0, -100, 750, 100),
                box(500, -100, 1_000, 100),
            ]
        },
        crs=edges.crs,
    )
    residential = gpd.GeoDataFrame(
        {
            "factor": [0.5, 0.2],
            "geometry": [
                box(1_000, -100, 2_000, 100),
                box(0, 900, 2_000, 1_100),
            ],
        },
        crs=edges.crs,
    )

    result = calculate_pipeline_effective_distance(
        edges,
        [
            ScalarPenaltyLayer(
                name="water",
                features=water,
                additional_distance_factor=9.0,
            ),
            ScalarPenaltyLayer(
                name="residential",
                features=residential,
                factor_column="factor",
            ),
        ],
    ).set_index("edge_region")

    assert result.loc["R0-R1", "water_intersection_km"] == pytest.approx(1.0)
    assert result.loc["R0-R1", "water_penalty_km"] == pytest.approx(9.0)
    assert result.loc["R0-R1", "residential_penalty_km"] == pytest.approx(0.5)
    assert result.loc["R0-R1", "effective_distance_km"] == pytest.approx(11.5)
    assert result.loc["R2-R3", "water_penalty_km"] == pytest.approx(0.0)
    assert result.loc["R2-R3", "residential_intersection_km"] == pytest.approx(2.0)
    assert result.loc["R2-R3", "effective_distance_km"] == pytest.approx(2.4)


def test_empty_layer_produces_zero_penalty_columns() -> None:
    edges = build_edges()
    empty_layer = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([], crs=edges.crs),
        crs=edges.crs,
    )

    result = calculate_pipeline_effective_distance(
        edges,
        [
            ScalarPenaltyLayer(
                name="empty",
                features=empty_layer,
                additional_distance_factor=4.0,
            )
        ],
    )

    assert result["empty_intersection_km"].eq(0.0).all()
    assert result["empty_penalty_km"].eq(0.0).all()
    assert result["effective_distance_km"].tolist() == [2.0, 2.0]


def test_factor_column_must_exist() -> None:
    edges = build_edges()
    polygons = gpd.GeoDataFrame(
        {"geometry": [box(0, -100, 1_000, 100)]},
        crs=edges.crs,
    )

    with pytest.raises(ValueError, match="missing factor column"):
        calculate_pipeline_effective_distance(
            edges,
            [
                ScalarPenaltyLayer(
                    name="residential",
                    features=polygons,
                    factor_column="factor",
                )
            ],
        )


def test_edges_must_use_projected_crs() -> None:
    edges = build_edges().to_crs("EPSG:4326")

    with pytest.raises(ValueError, match="projected CRS"):
        calculate_pipeline_effective_distance(edges, [])
