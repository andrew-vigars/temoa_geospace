from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from geocanoe.config import load_geospatial_build_config, load_model_config
from geocanoe.schema.build import (
    ResolvedSchemaConfig,
    assign_etl_curve_to_regions,
    attach_pipeline_cost_distances,
    build_canonical_links,
    build_etl_curve,
    build_generalized_pipeline_etl_segments,
    build_generalized_pipeline_opex_rows,
    build_transport_costvariable,
    build_transport_efficiency,
    build_tech_specs,
    build_schema_fingerprint,
    clear_output_tables,
    rebuild_capacity_limits,
    rebuild_demand,
    rebuild_static_supporting_tables,
    rebuild_storage_activity_limit,
    rebuild_storage_efficiency,
    select_basemap_stem_for_resolution,
    select_enabled_transport_tech_specs,
    select_storage_eligible_regions,
    validate_storage_capacity_bound_setting,
    validate_gasoline_demand_region_coverage,
    validate_storage_region_coverage,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_co2_registry_flags_balance_captured_co2_only() -> None:
    registry = pd.read_csv(
        Path(__file__).resolve().parents[1] / "registry" / "commodities.csv"
    ).set_index("name")

    assert registry.loc["co2", "flag"] == "a"
    assert registry.loc["co2_stored", "flag"] == "wa"


def test_schema_fingerprint_tracks_effective_model_settings(
    tmp_path: Path,
) -> None:
    build_config = load_geospatial_build_config(
        PROJECT_ROOT / "config" / "build_profiles" / "sample_build_profile.toml"
    )
    baseline = load_model_config(
        PROJECT_ROOT / "registry" / "model.toml",
        PROJECT_ROOT / "registry" / "scenarios" / "sample_scenario.toml",
    )
    alternate_path = tmp_path / "required-storage.toml"
    alternate_path.write_text(
        """[scenario]
id = "required-storage"

[storage]
requirement = "minimum_cumulative_activity"
minimum_cumulative_activity = 7_500_000_000
""",
        encoding="utf-8",
    )
    alternate = load_model_config(
        PROJECT_ROOT / "registry" / "model.toml",
        alternate_path,
    )
    weighted_path = tmp_path / "weighted-pipeline.toml"
    weighted_path.write_text(
        """[scenario]
id = "weighted-pipeline"

[pipeline_costs]
impedance_scope = "etl_capex_only"
""",
        encoding="utf-8",
    )
    weighted = load_model_config(
        PROJECT_ROOT / "registry" / "model.toml",
        weighted_path,
    )

    baseline_hash = build_schema_fingerprint(
        build_config,
        baseline,
        "sample_basemap_25km_centroid",
    )
    assert len(baseline_hash) == 8
    assert baseline_hash == build_schema_fingerprint(
        build_config,
        baseline,
        "sample_basemap_25km_centroid",
    )
    assert baseline_hash != build_schema_fingerprint(
        build_config,
        alternate,
        "sample_basemap_25km_centroid",
    )
    assert baseline_hash != build_schema_fingerprint(
        build_config,
        weighted,
        "sample_basemap_25km_centroid",
    )


def test_basemap_selection_matches_configured_resolution() -> None:
    build_config = load_geospatial_build_config(
        PROJECT_ROOT / "config" / "build_profiles" / "sample_build_profile.toml"
    )
    model_config = load_model_config(
        PROJECT_ROOT / "registry" / "model.toml",
        PROJECT_ROOT / "registry" / "scenarios" / "sample_scenario.toml",
    )

    basemap_stem = select_basemap_stem_for_resolution(build_config, model_config)

    assert basemap_stem == "provinces_only_basemap_25km_centroid"


def test_basemap_selection_rejects_ungenerated_resolution(
    tmp_path: Path,
) -> None:
    build_config = load_geospatial_build_config(
        PROJECT_ROOT / "config" / "build_profiles" / "sample_build_profile.toml"
    )
    scenario_path = tmp_path / "unavailable-resolution.toml"
    scenario_path.write_text(
        """[scenario]
id = "bad-resolution"

[basemap]
grid_type = "projected"
resolution = 999
""",
        encoding="utf-8",
    )
    model_config = load_model_config(
        PROJECT_ROOT / "registry" / "model.toml",
        scenario_path,
    )

    with pytest.raises(ValueError, match="No processed basemap matches"):
        select_basemap_stem_for_resolution(build_config, model_config)


def test_etl_curve_is_contiguous_and_monotonic() -> None:
    curve = build_etl_curve("ELC_TRANS", resolution=5, spacing="linear")

    assert len(curve) == 4
    assert curve.iloc[0]["cap_lower"] == 0
    assert np.allclose(curve["cap_upper"].iloc[:-1], curve["cap_lower"].iloc[1:])
    assert np.allclose(
        curve["cost_upper"].iloc[:-1],
        curve["cost_lower"].iloc[1:],
    )
    assert (curve["cap_upper"] > curve["cap_lower"]).all()
    assert (curve["cost_upper"] > curve["cost_lower"]).all()


@pytest.mark.parametrize(
    ("technology", "resolution", "spacing", "message"),
    [
        ("UNKNOWN", 5, "linear", "Missing ETL cost parameters"),
        ("ELC_TRANS", 1, "linear", "at least 2"),
        ("ELC_TRANS", 5, "quadratic", "must be 'log' or 'linear'"),
    ],
)
def test_etl_curve_rejects_invalid_requests(
    technology: str,
    resolution: int,
    spacing: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_etl_curve(technology, resolution=resolution, spacing=spacing)


def test_etl_region_assignment_scales_cost_by_distance() -> None:
    assigned = assign_etl_curve_to_regions(
        pd.Series(["R0-R1", "R1-R2"]),
        "ELC_TRANS",
        pd.Series([1.0, 2.0]),
    )
    first = assigned.loc[assigned["region"] == "R0-R1"].reset_index(drop=True)
    second = assigned.loc[assigned["region"] == "R1-R2"].reset_index(drop=True)

    assert len(first) == len(second)
    assert np.allclose(second["cost_upper"], first["cost_upper"] * 2)
    assert np.allclose(second["cap_upper"], first["cap_upper"])


def test_transport_tables_expand_links_by_technology() -> None:
    links = pd.DataFrame(
        {
            "canoe_region": ["R0-R1", "R1-R2"],
            "distance_km": [10.0, 20.0],
        }
    )
    specs = pd.DataFrame(
        {
            "tech": ["TRUCK_A", "TRUCK_B"],
            "input_comm": ["fuel", "fuel"],
            "output_comm": ["fuel", "fuel"],
            "cost_per_km": [2.0, 3.0],
            "intercept_cost_per_km": [1.0, 4.0],
        }
    )

    efficiency = build_transport_efficiency(links, specs, "test", 2025)
    costs = build_transport_costvariable(links, specs, "test", 2025)

    assert len(efficiency) == 4
    assert len(costs) == 4
    assert set(efficiency["region"]) == {"R0-R1", "R1-R2"}
    assert set(efficiency["vintage"]) == {2025}
    assert set(costs["period"]) == {2025}
    assert set(costs["vintage"]) == {2025}
    assert costs.loc[
        (costs["region"] == "R1-R2") & (costs["tech"] == "TRUCK_B"),
        "cost",
    ].item() == 64.0


def test_transport_master_switches_select_modes_independently() -> None:
    raw = pd.read_csv(PROJECT_ROOT / "registry" / "transport_techs.csv")
    all_specs = build_tech_specs(raw)

    roads_only = select_enabled_transport_tech_specs(
        all_specs,
        roads_enabled=True,
        pipelines_enabled=False,
    )
    pipelines_only = select_enabled_transport_tech_specs(
        all_specs,
        roads_enabled=False,
        pipelines_enabled=True,
    )
    neither = select_enabled_transport_tech_specs(
        all_specs,
        roads_enabled=False,
        pipelines_enabled=False,
    )

    assert roads_only.truck_techs == all_specs.truck_techs
    assert not roads_only.pipe_techs
    assert pipelines_only.pipe_techs == all_specs.pipe_techs
    assert not pipelines_only.truck_techs
    assert not neither.pipe_techs
    assert not neither.truck_techs
    assert neither.trans_techs == all_specs.trans_techs


def test_transport_costs_reject_nonpositive_distance() -> None:
    links = pd.DataFrame(
        {"canoe_region": ["R0-R1"], "distance_km": [0.0]}
    )
    specs = pd.DataFrame(
        {
            "tech": ["TRUCK"],
            "cost_per_km": [1.0],
            "intercept_cost_per_km": [0.0],
        }
    )

    with pytest.raises(AssertionError):
        build_transport_costvariable(links, specs, "test", 2025)


def _pipeline_graph_edges() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "edge_region": ["R0-R1", "R1-R0"],
            "region_from": ["R0", "R1"],
            "region_to": ["R1", "R0"],
            "distance_km": [10.0, 10.0],
        }
    )


def _pipeline_impedance() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "edge_region": ["R0-R1", "R1-R0"],
            "region_from": ["R0", "R1"],
            "region_to": ["R1", "R0"],
            "physical_distance_km": [10.0, 10.0],
            "effective_distance_km": [15.0, 15.0],
            "cost_distance_multiplier": [1.5, 1.5],
        }
    )


@pytest.mark.parametrize(
    ("scope", "expected_capex", "expected_fixed_opex", "expected_variable_opex"),
    [
        ("none", 10.0, 10.0, 10.0),
        ("etl_capex_only", 15.0, 10.0, 10.0),
        ("all_km_dependent", 15.0, 15.0, 15.0),
    ],
)
def test_pipeline_impedance_scope_selects_cost_distances(
    scope: str,
    expected_capex: float,
    expected_fixed_opex: float,
    expected_variable_opex: float,
) -> None:
    impedance = None if scope == "none" else _pipeline_impedance()

    links = attach_pipeline_cost_distances(
        _pipeline_graph_edges(),
        impedance,
        scope,
    )

    assert links["distance_km"].eq(10.0).all()
    assert links["pipeline_capex_distance_km"].eq(expected_capex).all()
    assert links["pipeline_fixed_opex_distance_km"].eq(expected_fixed_opex).all()
    assert links["pipeline_variable_opex_distance_km"].eq(
        expected_variable_opex
    ).all()


def test_pipeline_impedance_requires_exact_edge_coverage() -> None:
    impedance = _pipeline_impedance().iloc[:1].copy()

    with pytest.raises(ValueError, match="does not exactly cover"):
        attach_pipeline_cost_distances(
            _pipeline_graph_edges(),
            impedance,
            "etl_capex_only",
        )


def test_pipeline_etl_and_opex_use_separate_cost_distances() -> None:
    links = attach_pipeline_cost_distances(
        _pipeline_graph_edges(),
        _pipeline_impedance(),
        "all_km_dependent",
    )
    links["canoe_region"] = links["edge_region"]
    tech_specs = pd.DataFrame({"tech": ["H2_PIPE"]})
    etl_template = pd.DataFrame(
        {
            "tech_or_group": ["H2_PIPE"],
            "segment": [0],
            "cap_lower": [0.0],
            "cap_upper": [100.0],
            "cost_lower_per_km": [0.0],
            "cost_upper_per_km": [2.0],
            "data_id": ["GEO001"],
        }
    )
    opex_coefficients = pd.DataFrame(
        {
            "technology": ["H2_PIPE", "H2_PIPE"],
            "cost_type": ["fixed_opex", "variable_opex"],
            "coefficient_per_km": [3.0, 4.0],
            "intercept_cost": [0.0, 0.0],
            "data_id": ["GEO001", "GEO001"],
        }
    )

    etl = build_generalized_pipeline_etl_segments(
        links,
        tech_specs,
        etl_template,
    )
    fixed, variable = build_generalized_pipeline_opex_rows(
        links,
        tech_specs,
        opex_coefficients,
        2025,
        impedance_scope="all_km_dependent",
    )
    transmission = build_transport_costvariable(
        links,
        pd.DataFrame(
            {
                "tech": ["ELC_TRANS"],
                "cost_per_km": [2.0],
                "intercept_cost_per_km": [0.0],
            }
        ),
        "physical transmission distance",
        2025,
    )

    assert etl["cost_upper"].eq(30.0).all()
    assert fixed["cost"].eq(45.0).all()
    assert variable["cost"].eq(60.0).all()
    assert transmission["cost"].eq(20.0).all()


def test_canonical_links_separate_all_edges_from_road_edges() -> None:
    graph_nodes = gpd.GeoDataFrame({"region": ["R0", "R1", "R2"]})
    graph_edges = pd.DataFrame(
        {
            "edge_region": ["R0-R1", "R1-R2"],
            "region_from": ["R0", "R1"],
            "region_to": ["R1", "R2"],
            "distance_km": [10.0, 20.0],
        }
    )
    road_connections = pd.DataFrame(
        {
            "edge_region": ["R0-R1", "R1-R2"],
            "region_from": ["R0", "R1"],
            "region_to": ["R1", "R2"],
            "direction": ["east", "east"],
            "connection_method": ["strong", "strong"],
            "distance_km": [10.0, 20.0],
            "lon_from": [0.0, 1.0],
            "lat_from": [0.0, 0.0],
            "lon_to": [1.0, 2.0],
            "lat_to": [0.0, 0.0],
            "has_road_connection": [True, False],
        }
    )
    placeholder = Path("unused")
    config = ResolvedSchemaConfig(
        build_id="test",
        scenario_id="baseline",
        scenario_description="Test baseline",
        fingerprint="a1b2c3d4",
        basemap_stem="test_basemap",
        road_layer="freight_access",
        connection_method="strong",
        basemap_path=placeholder,
        graph_node_path=placeholder,
        graph_edge_path=placeholder,
        road_edge_connections_path=placeholder,
        road_edges_gpkg_path=placeholder,
        road_region_overlay_path=placeholder,
        co2_storage_path=placeholder,
        gasoline_demand_path=placeholder,
        storage_eligibility="all_mapped",
        storage_use_capacity_bound=False,
        model_config_path=placeholder,
        scenario_config_path=placeholder,
        model_start_year=2025,
        model_end_year=2050,
        emissions_projection_method="constant",
        global_discount_rate=0.03,
        default_loan_rate=0.03,
        storage_requirement="none",
        storage_minimum_cumulative_activity=0.0,
        legacy_gasoline_enabled=False,
        legacy_gasoline_years_of_demand=0.0,
        output_sqlite_path=placeholder,
    )

    canonical = build_canonical_links(
        graph_nodes,
        graph_edges,
        road_connections,
        config,
    )

    assert canonical.valid_node_regions == {"R0", "R1", "R2"}
    assert canonical.valid_pipeline_edge_regions == {"R0-R1", "R1-R2"}
    assert canonical.valid_road_edge_regions == {"R0-R1"}


def test_storage_region_coverage_must_exactly_match_graph_nodes() -> None:
    graph_nodes = pd.DataFrame({"region": ["R0", "R1", "R2"]})
    storage_regions = pd.DataFrame(
        {
            "region": ["R0", "R1", "R3"],
            "storage_accessible": [True, False, True],
        }
    )

    with pytest.raises(
        ValueError,
        match="does not exactly cover selected graph regions",
    ):
        validate_storage_region_coverage(storage_regions, graph_nodes)


def test_gasoline_demand_coverage_must_exactly_match_graph_nodes() -> None:
    graph_nodes = pd.DataFrame({"region": ["R0", "R1", "R2"]})
    gasoline_regions = pd.DataFrame(
        {
            "region": ["R0", "R1", "R3"],
            "demand": [10.0, 20.0, 0.0],
            "demand_units": ["t/year"] * 3,
            "has_gasoline_demand": [True, True, False],
        }
    )

    with pytest.raises(
        ValueError,
        match="does not exactly cover selected graph regions",
    ):
        validate_gasoline_demand_region_coverage(
            gasoline_regions,
            graph_nodes,
        )


def test_gasoline_demand_contract_accepts_zero_demand_regions() -> None:
    graph_nodes = pd.DataFrame({"region": ["R0", "R1", "R2"]})
    gasoline_regions = pd.DataFrame(
        {
            "region": ["R0", "R1", "R2"],
            "demand": [10.0, 20.0, 0.0],
            "demand_units": ["t/year"] * 3,
            "has_gasoline_demand": [True, True, False],
        }
    )

    validate_gasoline_demand_region_coverage(
        gasoline_regions,
        graph_nodes,
    )


def test_storage_efficiency_exactly_covers_accessible_regions() -> None:
    columns = [
        "region",
        "input_comm",
        "tech",
        "vintage",
        "output_comm",
        "efficiency",
        "notes",
        "data_source",
        "dq_cred",
        "dq_geog",
        "dq_struc",
        "dq_tech",
        "dq_time",
        "data_id",
    ]
    stale_storage = pd.DataFrame(
        [["R9", "co2", "CO2_INJECT", 1, "co2_stored", 1.0] + [None] * 8],
        columns=columns,
    )
    ordinary = pd.DataFrame(
        [["R0", "elc", "ELC_GEN", 1, "elc", 1.0] + [None] * 8],
        columns=columns,
    )
    db_encoded = {
        "Efficiency": pd.concat([ordinary, stale_storage], ignore_index=True)
    }
    storage_regions = pd.DataFrame(
        {
            "region": ["R0", "R1", "R2"],
            "storage_accessible": [True, False, True],
        }
    )

    rebuild_storage_efficiency(db_encoded, storage_regions, 2025)

    encoded = db_encoded["Efficiency"].loc[
        db_encoded["Efficiency"]["tech"] == "CO2_INJECT"
    ]
    assert set(encoded["region"]) == {"R0", "R2"}
    assert len(encoded) == 2
    assert encoded["input_comm"].eq("co2").all()
    assert encoded["output_comm"].eq("co2_stored").all()
    assert encoded["efficiency"].eq(1.0).all()
    assert encoded["vintage"].eq(2025).all()
    assert not db_encoded["Efficiency"].loc[
        db_encoded["Efficiency"]["tech"] == "ELC_GEN"
    ].empty


@pytest.mark.parametrize(
    ("eligibility", "expected"),
    [
        ("all_mapped", {"R0", "R1", "R2"}),
        ("quantitative", {"R0"}),
        ("qualitative", {"R1", "R2"}),
    ],
)
def test_storage_eligibility_modes_select_expected_regions(
    eligibility: str,
    expected: set[str],
) -> None:
    storage_regions = pd.DataFrame(
        {
            "region": ["R0", "R1", "R2", "R3"],
            "storage_accessible": [True, True, True, False],
            "has_quantitative_storage_evidence": [True, False, False, False],
            "has_qualitative_storage_evidence": [False, True, True, False],
        }
    )

    selected = select_storage_eligible_regions(storage_regions, eligibility)

    assert set(selected) == expected


def test_capacity_limits_keep_emissions_as_annual_representative_capacity() -> None:
    db_encoded: dict[str, pd.DataFrame] = {}
    site_attributes = pd.DataFrame(
        {
            "region": ["R1", "R2"],
            "co2": [1_000.0, 0.0],
            "max_elc": [5.0, 10.0],
        }
    )

    rebuild_capacity_limits(
        db_encoded,
        site_attributes,
        model_period=2025,
        period_years=25,
        emissions_projection_method="constant",
    )

    co2_limits = db_encoded["LimitCapacity"].loc[
        db_encoded["LimitCapacity"]["tech_or_group"] == "CO2_CAP"
    ]
    assert co2_limits["capacity"].tolist() == [1_000.0, 0.0]
    assert set(co2_limits["units"]) == {"t CO2e/year"}


def test_demand_is_encoded_as_annual_tonnes() -> None:
    db_encoded: dict[str, pd.DataFrame] = {}
    site_attributes = pd.DataFrame(
        {
            "region": ["R1", "R2"],
            "demand": [10.0, 0.0],
        }
    )

    rebuild_demand(
        db_encoded,
        site_attributes,
        model_period=2025,
    )

    assert db_encoded["Demand"][
        ["region", "period", "commodity", "demand", "units"]
    ].to_dict("records") == [
        {
            "region": "R1",
            "period": 2025,
            "commodity": "d_gsl",
            "demand": 10.0,
            "units": "t/year",
        }
    ]
    assert "Silver gasoline-demand workflow" in (
        db_encoded["Demand"].iloc[0]["notes"]
    )


def test_storage_capacity_bound_is_rejected_without_numeric_silver_data() -> None:
    with pytest.raises(ValueError, match="no allocated numerical regional"):
        validate_storage_capacity_bound_setting(True)

    validate_storage_capacity_bound_setting(False)


def test_storage_minimum_cumulative_activity_encodes_annual_equivalent() -> None:
    limit_columns = [
        "region",
        "period",
        "tech_or_group",
        "operator",
        "activity",
        "units",
        "notes",
        "data_source",
        "dq_cred",
        "dq_geog",
        "dq_struc",
        "dq_tech",
        "dq_time",
        "data_id",
    ]
    db_encoded = {
        "LimitActivity": pd.DataFrame(
            [
                ["R0", 1, "ELC_GEN", "le", 10.0] + [None] * 9,
                ["R9", 1, "CO2_INJECT", "le", 5.0] + [None] * 9,
            ],
            columns=limit_columns,
        ),
        "Efficiency": pd.DataFrame(
            {"region": ["R1"], "tech": ["CO2_INJECT"]}
        ),
    }

    rebuild_storage_activity_limit(
        db_encoded,
        requirement="minimum_cumulative_activity",
        minimum_cumulative_activity=25_000.0,
        model_period=2025,
        period_years=25,
    )

    encoded = db_encoded["LimitActivity"].loc[
        db_encoded["LimitActivity"]["tech_or_group"] == "CO2_INJECT"
    ]
    assert encoded[
        ["region", "period", "operator", "activity", "units"]
    ].to_dict("records") == [
        {
            "region": "global",
            "period": 2025,
            "operator": "ge",
            "activity": 1_000.0,
            "units": "t CO2e/year",
        }
    ]
    assert set(db_encoded["LimitActivity"]["tech_or_group"]) == {
        "ELC_GEN",
        "CO2_INJECT",
    }


def test_storage_requirement_none_removes_stale_injection_constraint() -> None:
    db_encoded = {
        "LimitActivity": pd.DataFrame(
            {
                "tech_or_group": ["CO2_INJECT", "ELC_GEN"],
            }
        ),
        "Efficiency": pd.DataFrame(
            {"region": ["R1"], "tech": ["CO2_INJECT"]}
        ),
    }

    rebuild_storage_activity_limit(db_encoded, "none", 0.0, 2025, 25)

    assert db_encoded["LimitActivity"]["tech_or_group"].tolist() == [
        "ELC_GEN"
    ]


def test_static_tables_encode_one_25_year_period_and_finance() -> None:
    db_encoded = {
        "MetaDataReal": pd.DataFrame(
            {
                "element": ["global_discount_rate", "default_loan_rate"],
                "value": [0.05, 0.05],
                "notes": [None, None],
            }
        )
    }
    commodities = pd.DataFrame(
        {"name": ["co2"], "flag": ["a"], "description": ["captured"]}
    )

    rebuild_static_supporting_tables(
        db_encoded,
        commodities,
        model_start_year=2025,
        model_end_year=2050,
        global_discount_rate=0.03,
        default_loan_rate=0.03,
    )

    assert db_encoded["TimePeriod"].to_dict("records") == [
        {"sequence": 1, "period": 2025, "flag": "f"},
        {"sequence": 2, "period": 2050, "flag": "f"},
    ]
    finance = db_encoded["MetaDataReal"].set_index("element")["value"]
    assert finance["global_discount_rate"] == 0.03
    assert finance["default_loan_rate"] == 0.03


def test_clear_output_tables_preserves_schema() -> None:
    tables = {
        "Technology": pd.DataFrame({"tech": ["A"]}),
        "OutputFlowOut": pd.DataFrame({"scenario": ["S"], "flow": [1.0]}),
        "OutputCost": pd.DataFrame({"scenario": ["S"], "cost": [2.0]}),
    }

    clear_output_tables(tables)

    assert tables["Technology"].to_dict("records") == [{"tech": "A"}]
    assert tables["OutputFlowOut"].empty
    assert list(tables["OutputFlowOut"].columns) == ["scenario", "flow"]
    assert tables["OutputCost"].empty
