from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from geocanoe.schema.build import (
    ResolvedSchemaConfig,
    assign_etl_curve_to_regions,
    build_canonical_links,
    build_etl_curve,
    build_transport_costvariable,
    build_transport_efficiency,
    clear_output_tables,
)


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

    efficiency = build_transport_efficiency(links, specs, "test")
    costs = build_transport_costvariable(links, specs, "test")

    assert len(efficiency) == 4
    assert len(costs) == 4
    assert set(efficiency["region"]) == {"R0-R1", "R1-R2"}
    assert costs.loc[
        (costs["region"] == "R1-R2") & (costs["tech"] == "TRUCK_B"),
        "cost",
    ].item() == 64.0


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
        build_transport_costvariable(links, specs, "test")


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
        basemap_stem="test_basemap",
        road_layer="freight_access",
        connection_method="strong",
        basemap_path=placeholder,
        graph_node_path=placeholder,
        graph_edge_path=placeholder,
        road_edge_connections_path=placeholder,
        road_edges_gpkg_path=placeholder,
        road_region_overlay_path=placeholder,
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
