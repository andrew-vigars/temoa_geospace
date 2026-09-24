"""Tests for TEMOA-style optimized-network HTML preparation."""

import pandas as pd
import networkx as nx

from geocanoe.analysis.network import NetworkInputs
from geocanoe.analysis.network_html import (
    COMMODITY_COLORS,
    build_commodity_graph,
    build_commodity_focus_graphs,
    build_hub_graph,
    build_opportunity_graphs,
    filter_graph,
    technology_category,
)


def _inputs() -> NetworkInputs:
    flow = pd.DataFrame([
        {
            "scenario": "S",
            "period": 2025,
            "region": "A",
            "tech": "H2_PLANT",
            "input_comm": "elc",
            "output_comm": "h2",
            "flow": 10.0,
        },
        {
            "scenario": "S",
            "period": 2025,
            "region": "A-B",
            "tech": "H2_PIPE",
            "input_comm": "h2",
            "output_comm": "h2",
            "flow": 8.0,
        },
    ])
    edges = pd.DataFrame([
        {
            "edge_region": "A-B",
            "region_from": "A",
            "region_to": "B",
            "distance_km": 25.0,
        }
    ])
    return NetworkInputs(flow, pd.DataFrame(), edges)


def test_commodity_graph_decodes_local_and_transport_flows():
    graph = build_commodity_graph(
        _inputs(),
        commodity_flags={"elc": "wa", "h2": "wa"},
        scenario="S",
        period=2025,
    )

    assert {"A::elc", "A::h2", "B::h2"}.issubset(graph)
    assert graph.nodes["A::h2"]["color"] == COMMODITY_COLORS["h2"]
    assert graph["A::elc"]["A::h2"][0]["label"] == "H2_PLANT"
    assert graph["A::h2"]["B::h2"][0]["label"] == "H2_PIPE"
    assert graph["A::h2"]["B::h2"][0]["distance_km"] == 25.0


def test_html_graph_filters_keep_incident_context():
    graph = build_commodity_graph(_inputs(), scenario="S", period=2025)

    selected = filter_graph(graph, region="B", all_components=True)

    assert set(selected) == {"A::h2", "B::h2"}
    assert list(selected.edges()) == [("A::h2", "B::h2")]


def test_technology_categories_preserve_transport_semantics():
    assert technology_category("H2_PIPE") == "pipeline"
    assert technology_category("CO2_TRUCK") == "truck"
    assert technology_category("ELC_TRANS") == "transmission"
    assert technology_category("GSL_PLANT") == "production"
    assert technology_category("CO2_INJECT") == "storage"


def test_default_html_choices_start_with_focused_subsystem():
    graph = build_commodity_graph(_inputs(), scenario="S", period=2025)

    choices = build_commodity_focus_graphs(graph)

    assert choices[0].graph["name"].startswith("h2 pathway")
    assert choices[-1].graph["name"].startswith("All commodities")


def test_focused_pathway_keeps_upstream_inputs_and_downstream_products():
    flow = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "A", "tech": "CAP", "input_comm": "ethos", "output_comm": "co2", "flow": 4.0},
        {"scenario": "S", "period": 2025, "region": "A", "tech": "METOH_PLANT", "input_comm": "co2", "output_comm": "ch3oh", "flow": 4.0},
        {"scenario": "S", "period": 2025, "region": "A", "tech": "GSL_PLANT", "input_comm": "ch3oh", "output_comm": "gsl", "flow": 3.0},
    ])
    graph = build_commodity_graph(
        NetworkInputs(flow, pd.DataFrame(), pd.DataFrame(columns=["edge_region", "region_from", "region_to"])),
        scenario="S",
        period=2025,
    )

    gasoline = next(
        choice for choice in build_commodity_focus_graphs(graph)
        if choice.graph["name"].startswith("gsl pathway")
    )

    assert {"A::ethos", "A::co2", "A::ch3oh", "A::gsl"}.issubset(gasoline)


def test_sql_efficiency_defines_all_inputs_for_deployed_multi_input_process():
    flow_out = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "A", "tech": "METOH_PLANT", "input_comm": "co2", "output_comm": "ch3oh", "flow": 4.0},
    ])
    flow_in = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "A", "tech": "METOH_PLANT", "input_comm": "co2", "output_comm": "ch3oh", "flow": 5.6},
    ])
    efficiency = pd.DataFrame([
        {"region": "A", "tech": "METOH_PLANT", "input_comm": commodity, "output_comm": "ch3oh"}
        for commodity in ("co2", "h2", "elc")
    ])
    capacity = pd.DataFrame([
        {"scenario": "S", "period": 2025, "region": "A", "tech": "METOH_PLANT", "capacity": 2.0},
    ])
    inputs = NetworkInputs(
        flow_out,
        pd.DataFrame(),
        pd.DataFrame(columns=["edge_region", "region_from", "region_to"]),
        flow_in=flow_in,
        net_capacity=capacity,
        efficiency=efficiency,
    )

    graph = build_commodity_graph(inputs, scenario="S", period=2025)

    assert {"A::co2", "A::h2", "A::elc", "A::ch3oh"}.issubset(graph)
    assert graph["A::co2"]["A::ch3oh"][0]["input_flow"] == 5.6


def test_hub_view_collapses_colocated_processes_into_regional_node(tmp_path):
    contracted = nx.MultiDiGraph()
    contracted.add_node("A", roles="production|demand")
    contracted.add_node("B", roles="production")
    contracted.add_edge(
        "A",
        "B",
        tech="H2_PIPE",
        output_comm="h2",
        flow=5.0,
        flow_min=5.0,
        distance_km=20.0,
        segment_count=1,
        pseudo_regions="A-B",
    )
    nx.write_graphml(contracted, tmp_path / "network_contracted.graphml")
    pd.DataFrame([
        {"region": "A", "technology": tech, "role": role, "status": "active", "output_activity": 5.0}
        for tech, role in (("H2_PLANT", "production"), ("METOH_PLANT", "production"), ("GSL_DEMAND", "demand"))
    ]).to_csv(tmp_path / "network_asset_inventory.csv", index=False)
    pd.DataFrame([
        {"region": "A", "component_id": 0, "community_id": 0, "betweenness_centrality": 0.5},
        {"region": "B", "component_id": 0, "community_id": 0, "betweenness_centrality": 0.0},
    ]).to_csv(tmp_path / "network_node_centrality.csv", index=False)
    pd.DataFrame([
        {"edge_region": "A-B", "tech": "H2_PIPE", "optimized_capacity": 10.0, "output_activity": 5.0},
    ]).to_csv(tmp_path / "network_transport_inventory.csv", index=False)

    graph = build_hub_graph(tmp_path)

    assert set(graph) == {"A", "B"}
    assert graph.nodes["A"]["is_hub"]
    assert "METOH_PLANT" in graph.nodes["A"]["technologies"]
    assert graph["A"]["B"][0]["utilization"] == 0.5


def test_opportunity_view_adds_unused_assets_as_disconnected_nodes(tmp_path):
    pd.DataFrame([
        {
            "region": "Z",
            "technology": "CO2_CAP",
            "status": "available_unused",
            "available_capacity": 7.0,
            "disconnected_from_deployed_transport": True,
        }
    ]).to_csv(tmp_path / "network_asset_inventory.csv", index=False)
    hub = nx.MultiDiGraph()
    hub.add_edge("A", "B", tech="CO2_PIPE")

    choices = build_opportunity_graphs(tmp_path, hub)

    assert len(choices) == 1
    assert "Z" in choices[0]
    assert choices[0].degree("Z") == 0
    assert choices[0].nodes["Z"]["is_opportunity"]
