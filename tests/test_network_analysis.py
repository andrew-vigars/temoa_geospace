"""Tests for solved-output pseudo-edge network decoding."""

import networkx as nx
import pandas as pd

from geocanoe.analysis.network import (
    NetworkInputs,
    analyse_network,
    build_asset_inventory,
    build_deployed_graph,
    contract_pass_through_nodes,
    resolve_snapshot,
)


def _inputs() -> NetworkInputs:
    flow = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "A", "tech": "H2_PLANT", "output_comm": "h2", "flow": 10},
        {"scenario": "S", "period": 2025, "region": "A-B", "tech": "H2_PIPE", "output_comm": "h2", "flow": 9},
        {"scenario": "S", "period": 2025, "region": "B-C", "tech": "H2_PIPE", "output_comm": "h2", "flow": 8},
        {"scenario": "S", "period": 2025, "region": "C", "tech": "CO2_INJECT", "output_comm": "co2", "flow": 8},
    ])
    edges = pd.DataFrame([
        {"edge_region": "A-B", "region_from": "A", "region_to": "B", "distance_km": 12},
        {"edge_region": "B-C", "region_from": "B", "region_to": "C", "distance_km": 13},
    ])
    demand = pd.DataFrame(columns=["region", "demand"])
    return NetworkInputs(flow, demand, edges)


def test_decodes_pseudo_regions_as_directed_edges():
    graph = build_deployed_graph(_inputs(), scenario="S", period=2025)

    assert isinstance(graph, nx.MultiDiGraph)
    assert set(graph.edges()) == {("A", "B"), ("B", "C")}
    assert graph.nodes["A"]["roles"] == "production"
    assert graph.nodes["C"]["roles"] == "storage"


def test_resolves_single_scenario_and_period_for_provenance():
    scenario, period = resolve_snapshot(
        _inputs().flow_out,
        scenario=None,
        period=None,
        min_flow=1e-3,
    )

    assert scenario == "S"
    assert period == 2025


def test_contracts_only_degree_two_pass_through_nodes():
    graph = build_deployed_graph(_inputs())
    contracted = contract_pass_through_nodes(graph)

    assert list(contracted.edges()) == [("A", "C")]
    edge = contracted["A"]["C"][0]
    assert edge["path_regions"] == ("A", "B", "C")
    assert edge["pseudo_regions"] == ("A-B", "B-C")
    assert edge["segment_count"] == 2
    assert edge["distance_km"] == 25
    assert edge["flow"] == 8


def test_branch_node_is_preserved_for_bottleneck_analysis():
    inputs = _inputs()
    extra_flow = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "B-D", "tech": "H2_PIPE", "output_comm": "h2", "flow": 4},
        {"scenario": "S", "period": 2025, "region": "D", "tech": "H2_PLANT", "output_comm": "h2", "flow": 4},
    ])
    extra_edge = pd.DataFrame([
        {"edge_region": "B-D", "region_from": "B", "region_to": "D", "distance_km": 7},
    ])
    branched = NetworkInputs(
        pd.concat([inputs.flow_out, extra_flow], ignore_index=True),
        inputs.demand,
        pd.concat([inputs.edges, extra_edge], ignore_index=True),
    )

    analysis = analyse_network(branched)

    assert "B" in analysis.contracted
    assert set(analysis.contracted.edges()) == {("A", "B"), ("B", "C"), ("B", "D")}
    assert set(analysis.node_metrics["region"]) == {"A", "B", "C", "D"}
    assert {"component_id", "community_id"}.issubset(analysis.node_metrics)
    assert "edge_betweenness_centrality" in analysis.edge_metrics
    assert "betweenness_centrality" in analysis.projected.nodes["B"]
    assert "community_id" in analysis.projected.nodes["B"]
    assert "edge_betweenness_centrality" in analysis.projected["A"]["B"]


def test_asset_inventory_separates_used_and_disconnected_potential():
    inputs = _inputs()
    limits = pd.DataFrame([
        {"region": "A", "period": 2025, "tech_or_group": "CO2_CAP", "capacity": 12.0},
        {"region": "Z", "period": 2025, "tech_or_group": "CO2_CAP", "capacity": 7.0},
    ])
    capacity = pd.DataFrame([
        {"scenario": "S", "region": "A", "period": 2025, "tech": "H2_PLANT", "capacity": 10.0},
    ])
    expanded = NetworkInputs(
        inputs.flow_out,
        inputs.demand,
        inputs.edges,
        net_capacity=capacity,
        limit_capacity=limits,
    )
    deployed = build_deployed_graph(expanded, scenario="S", period=2025)

    inventory = build_asset_inventory(
        expanded,
        deployed,
        scenario="S",
        period=2025,
        min_flow=1e-3,
    )

    unused = inventory.loc[
        (inventory["region"] == "Z") & (inventory["technology"] == "CO2_CAP")
    ].iloc[0]
    assert unused["status"] == "available_unused"
    assert bool(unused["disconnected_from_deployed_transport"])


def test_contraction_preserves_optimized_assets_without_transport():
    inputs = _inputs()
    isolated = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "Z", "tech": "ELC_GEN", "output_comm": "elc", "flow": 3.0},
    ])
    expanded = NetworkInputs(
        pd.concat([inputs.flow_out, isolated], ignore_index=True),
        inputs.demand,
        inputs.edges,
    )

    analysis = analyse_network(expanded, scenario="S", period=2025)

    assert "Z" in analysis.contracted
    assert analysis.contracted.degree("Z") == 0
    assert "Z" in analysis.projected
    assert analysis.projected.nodes["Z"]["component_id"] != analysis.projected.nodes["A"]["component_id"]
