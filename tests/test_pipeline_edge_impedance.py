from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from geocanoe.geospatial.pipeline_edge_impedance import calculate_edge_impedance
from geocanoe.geospatial.pipeline_impedance import ScalarPenaltyLayer


def test_edge_impedance_is_calculated_once_and_mirrored_to_directions() -> None:
    edges = gpd.GeoDataFrame(
        {
            "edge_region": ["R0-R1", "R1-R0", "R1-R2", "R2-R1"],
            "region_from": ["R0", "R1", "R1", "R2"],
            "region_to": ["R1", "R0", "R2", "R1"],
            "distance_km": [1.0, 1.0, 1.0, 1.0],
        },
        geometry=[
            LineString([(0, 0), (1000, 0)]),
            LineString([(1000, 0), (0, 0)]),
            LineString([(1000, 0), (2000, 0)]),
            LineString([(2000, 0), (1000, 0)]),
        ],
        crs="EPSG:3347",
    )
    lands = gpd.GeoDataFrame(
        {"factor": [2.0]},
        geometry=[box(0, -10, 500, 10)],
        crs=edges.crs,
    )
    population = gpd.GeoDataFrame(
        {"factor": [0.5]},
        geometry=[box(1000, -10, 2000, 10)],
        crs=edges.crs,
    )

    result = calculate_edge_impedance(
        edges,
        [
            ScalarPenaltyLayer("lands", lands, factor_column="factor"),
            ScalarPenaltyLayer("population", population, factor_column="factor"),
        ],
    ).set_index("edge_region")

    assert result.loc["R0-R1", "lands_penalty_km"] == pytest.approx(1.0)
    assert result.loc["R1-R0", "lands_penalty_km"] == pytest.approx(1.0)
    assert result.loc["R1-R2", "population_penalty_km"] == pytest.approx(0.5)
    assert result.loc["R2-R1", "population_penalty_km"] == pytest.approx(0.5)
    assert result.loc["R0-R1", "cost_distance_multiplier"] == pytest.approx(2.0)
    assert result.loc["R1-R2", "cost_distance_multiplier"] == pytest.approx(1.5)
    assert result["undirected_edge"].nunique() == 2


def test_edge_impedance_requires_unique_directed_ids() -> None:
    edges = gpd.GeoDataFrame(
        {
            "edge_region": ["R0-R1", "R0-R1"],
            "region_from": ["R0", "R0"],
            "region_to": ["R1", "R1"],
            "distance_km": [1.0, 1.0],
        },
        geometry=[
            LineString([(0, 0), (1000, 0)]),
            LineString([(0, 0), (1000, 0)]),
        ],
        crs="EPSG:3347",
    )

    with pytest.raises(ValueError, match="must be unique"):
        calculate_edge_impedance(edges, [])
