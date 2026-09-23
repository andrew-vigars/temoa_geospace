from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from geocanoe.acquisition.residential import POPULATION_DENSITY_FIELD
from geocanoe.geospatial.population_impedance import (
    build_population_exposure,
    classify_population_density,
)
from geocanoe.geospatial.pipeline_impedance import (
    ScalarPenaltyLayer,
    calculate_pipeline_effective_distance,
)
from geocanoe.registry.pipeline_impedance import PipelinePenaltyRegistry


def _penalty():
    return PipelinePenaltyRegistry().get_layer(
        "legacy_reference_v1", "population_exposure"
    )


def test_density_thresholds_reproduce_legacy_model() -> None:
    values = pd.Series([0, 156.9, 157, 703, 703.1, 1199, 1199.1, 2000])

    classified = classify_population_density(values, _penalty().bands)

    assert classified["configured_factor"].tolist() == [
        0.0,
        0.0,
        0.11,
        0.11,
        0.43,
        0.43,
        0.82,
        0.82,
    ]


def test_da_density_is_clipped_to_population_centres_and_audited() -> None:
    das = gpd.GeoDataFrame(
        {
            "DAUID": ["1", "2"],
            "DGUID": ["d1", "d2"],
            "PRUID": ["35", "35"],
        },
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10)],
        crs="EPSG:3347",
    )
    population = pd.DataFrame(
        {
            "DGUID": ["d1", "d2"],
            POPULATION_DENSITY_FIELD: [500.0, 1500.0],
        }
    )
    centres = gpd.GeoDataFrame(
        {
            "PCUID": ["p1"],
            "PCPUID": ["pp1"],
            "PCNAME": ["Test Centre"],
            "PCCLASS": [4],
            "PRUID": ["35"],
        },
        geometry=[box(5, 0, 15, 10)],
        crs="EPSG:3347",
    )

    exposure, impedance = build_population_exposure(
        das,
        population,
        centres,
        jurisdictions=("ON",),
        output_crs="EPSG:3347",
        penalty=_penalty(),
        build_id="test",
        study_area="ontario",
        penalty_profile="legacy_reference_v1",
    )

    assert len(exposure) == 2
    assert exposure.geometry.area.sum() == 100
    assert set(exposure["configured_factor"]) == {0.11, 0.82}
    assert impedance["applied_factor"].tolist() == exposure["applied_factor"].tolist()
    assert set(exposure["province"]) == {"ON"}

    edge = gpd.GeoDataFrame(
        {"edge_region": ["A-B"], "distance_km": [0.01]},
        geometry=[LineString([(5, 5), (15, 5)])],
        crs="EPSG:3347",
    )
    routed = calculate_pipeline_effective_distance(
        edge,
        [
            ScalarPenaltyLayer(
                name="population_exposure",
                features=impedance,
                factor_column="applied_factor",
            )
        ],
    )
    assert routed.loc[0, "population_exposure_intersection_km"] == pytest.approx(0.01)
    assert routed.loc[0, "population_exposure_penalty_km"] == pytest.approx(0.00465)
    assert routed.loc[0, "effective_distance_km"] == pytest.approx(0.01465)
