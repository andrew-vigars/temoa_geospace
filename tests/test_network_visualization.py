"""Tests for static NetworkX visualization preparation."""

import networkx as nx

from geocanoe.analysis.network_viz import scale_node_sizes, select_display_graph


def test_node_size_scaling_is_monotonic():
    sizes = scale_node_sizes({"low": 0.0, "middle": 0.25, "high": 1.0})

    assert sizes["low"] < sizes["middle"] < sizes["high"]
    assert sizes["low"] >= 45
    assert sizes["high"] <= 700


def test_largest_component_is_selected_by_default():
    graph = nx.DiGraph()
    graph.add_edges_from([("A", "B"), ("B", "C"), ("X", "Y")])

    selected = select_display_graph(graph, all_components=False)

    assert set(selected) == {"A", "B", "C"}
    assert set(select_display_graph(graph, all_components=True)) == set(graph)
