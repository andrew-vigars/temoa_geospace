"""Render a solved CANOE NetworkX analysis as a static network figure."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import networkx as nx
import numpy as np

from geocanoe.paths import find_project_root


DEFAULT_SIZE_METRIC = "betweenness_centrality"
DEFAULT_COLOR_ATTRIBUTE = "community_id"
SIZE_METRICS = (
    "betweenness_centrality",
    "pagerank",
    "degree_centrality",
    "in_capacity",
    "out_capacity",
)
COLOR_ATTRIBUTES = ("community_id", "component_id", "roles")


def find_projected_graphs(analysis_root: str | Path) -> list[Path]:
    """Return projected network GraphML files beneath ``analysis_root``."""

    root = Path(analysis_root)
    if not root.exists():
        raise FileNotFoundError(
            f"Analysis output directory not found: {root}. Run geocanoe-network first."
        )
    graphs = sorted(root.rglob("network_projected.graphml"))
    if not graphs:
        raise FileNotFoundError(
            f"No network_projected.graphml files found under {root}. "
            "Run geocanoe-network first."
        )
    return graphs


def select_projected_graph(analysis_root: str | Path) -> Path:
    """Select one projected graph while displaying its source-analysis folder."""

    root = Path(analysis_root)
    graphs = find_projected_graphs(root)
    if len(graphs) == 1:
        print(f"Selected network analysis: {graphs[0].relative_to(root)}")
        return graphs[0]

    print("\nAvailable network analyses:")
    for index, graph_path in enumerate(graphs):
        print(f"  [{index}] {graph_path.relative_to(root)}")
    selected_index = input("\nSelect network analysis index: ").strip()
    try:
        return graphs[int(selected_index)]
    except (ValueError, IndexError) as exc:
        raise SystemExit("Invalid network-analysis selection.") from exc


def load_projected_graph(graph_path: str | Path) -> nx.DiGraph:
    """Load and validate a projected directed GraphML network."""

    source = Path(graph_path)
    if not source.exists():
        raise FileNotFoundError(f"Projected GraphML file not found: {source}")
    loaded = nx.read_graphml(source)
    graph = nx.DiGraph(loaded)
    if graph.number_of_nodes() == 0:
        raise ValueError(f"Projected GraphML contains no nodes: {source}")
    return graph


def select_display_graph(graph: nx.DiGraph, all_components: bool) -> nx.DiGraph:
    """Use the largest weak component by default to avoid disconnected clutter."""

    if all_components or graph.number_of_nodes() == 1:
        return graph.copy()
    largest = max(nx.weakly_connected_components(graph), key=len)
    return graph.subgraph(largest).copy()


def _numeric_attributes(
    graph: nx.DiGraph,
    attribute: str,
    default: float = 0.0,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for node, data in graph.nodes(data=True):
        try:
            values[str(node)] = float(data.get(attribute, default))
        except (TypeError, ValueError):
            values[str(node)] = default
    return values


def scale_node_sizes(values: Mapping[str, float]) -> dict[str, float]:
    """Square-root scale a node metric to readable marker areas."""

    if not values:
        return {}
    numeric = np.asarray(list(values.values()), dtype=float)
    numeric[~np.isfinite(numeric)] = 0.0
    numeric = np.maximum(numeric, 0.0)
    transformed = np.sqrt(numeric)
    low = float(transformed.min())
    high = float(transformed.max())
    if high <= low:
        scaled = np.full_like(transformed, 180.0)
    else:
        scaled = 45.0 + 655.0 * (transformed - low) / (high - low)
    return dict(zip(values, scaled, strict=True))


def scale_edge_widths(graph: nx.DiGraph) -> list[float]:
    """Square-root scale projected route capacity to line widths."""

    capacity = np.asarray(
        [float(data.get("capacity", 0.0)) for _, _, data in graph.edges(data=True)],
        dtype=float,
    )
    if capacity.size == 0:
        return []
    capacity[~np.isfinite(capacity)] = 0.0
    transformed = np.sqrt(np.maximum(capacity, 0.0))
    high = float(transformed.max())
    if high == 0:
        return [0.6] * len(capacity)
    return (0.25 + 2.75 * transformed / high).tolist()


def calculate_layout(
    graph: nx.DiGraph,
    layout: str,
    *,
    seed: int = 42,
) -> dict[Any, np.ndarray]:
    """Calculate a deterministic network layout."""

    undirected = graph.to_undirected()
    if layout == "spring":
        return nx.spring_layout(
            undirected,
            seed=seed,
            weight=None,
            iterations=150,
            k=1.7 / np.sqrt(max(graph.number_of_nodes(), 1)),
        )
    if layout == "kamada-kawai":
        return nx.kamada_kawai_layout(undirected, weight="distance_km")
    if layout == "circular":
        return nx.circular_layout(undirected)
    raise ValueError(f"Unknown layout: {layout}")


def _node_colors(
    graph: nx.DiGraph,
    color_by: str,
) -> tuple[list[Any], str | None, list[Line2D]]:
    if color_by == "roles":
        role_colors = {
            "production": "#0072B2",
            "storage": "#CC79A7",
            "emitter": "#777777",
            "demand": "#D55E00",
            "pass_through": "#B8B8B8",
        }
        colors = []
        present: set[str] = set()
        for _, data in graph.nodes(data=True):
            roles = str(data.get("roles", "pass_through")).split("|")
            role = next((value for value in roles if value != "pass_through"), roles[0])
            present.add(role)
            colors.append(role_colors.get(role, "#56B4E9"))
        legend = [
            Line2D(
                [0], [0], marker="o", linestyle="", markersize=7,
                markerfacecolor=role_colors.get(role, "#56B4E9"),
                markeredgecolor="white", label=role.replace("_", " ").title(),
            )
            for role in sorted(present)
        ]
        return colors, None, legend

    values = _numeric_attributes(graph, color_by)
    return [values[str(node)] for node in graph], color_by.replace("_", " ").title(), []


def render_network(
    graph: nx.DiGraph,
    output_path: str | Path,
    *,
    size_metric: str = DEFAULT_SIZE_METRIC,
    color_by: str = DEFAULT_COLOR_ATTRIBUTE,
    layout: str = "spring",
    top_labels: int = 15,
    title: str | None = None,
) -> Path:
    """Render a centrality- and community-aware network PNG or SVG."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metric = _numeric_attributes(graph, size_metric)
    node_sizes = scale_node_sizes(metric)
    positions = calculate_layout(graph, layout)
    colors, colorbar_label, legend_handles = _node_colors(graph, color_by)

    figure, axes = plt.subplots(figsize=(14, 10), constrained_layout=True)
    figure.patch.set_facecolor("#FAFAF8")
    axes.set_facecolor("#FAFAF8")
    edge_widths = scale_edge_widths(graph)
    nx.draw_networkx_edges(
        graph,
        positions,
        ax=axes,
        width=edge_widths,
        edge_color="#606060",
        alpha=0.25,
        arrows=True,
        arrowsize=7,
        connectionstyle="arc3,rad=0.04",
    )
    nodes = nx.draw_networkx_nodes(
        graph,
        positions,
        ax=axes,
        node_size=[node_sizes[str(node)] for node in graph],
        node_color=colors,
        cmap=plt.get_cmap("turbo"),
        edgecolors="white",
        linewidths=0.45,
        alpha=0.9,
    )

    label_count = max(0, min(top_labels, graph.number_of_nodes()))
    ranked = sorted(metric, key=metric.get, reverse=True)[:label_count]
    labels = {node: node for node in ranked}
    nx.draw_networkx_labels(
        graph,
        positions,
        labels=labels,
        ax=axes,
        font_size=7,
        font_weight="normal",
        bbox={"facecolor": "#FAFAF8", "edgecolor": "none", "alpha": 0.72, "pad": 0.5},
    )

    if colorbar_label is not None:
        colorbar = figure.colorbar(nodes, ax=axes, shrink=0.62, pad=0.01)
        colorbar.set_label(colorbar_label)
    elif legend_handles:
        axes.legend(handles=legend_handles, loc="lower left", frameon=False, ncols=2)

    axes.set_title(
        title or f"CANOE transport network — nodes sized by {size_metric.replace('_', ' ')}",
        loc="left",
        fontsize=14,
        fontweight="normal",
    )
    axes.text(
        0.0,
        -0.025,
        (
            f"{graph.number_of_nodes():,} nodes · {graph.number_of_edges():,} directed edges · "
            f"colour: {color_by.replace('_', ' ')} · edge width: capacity"
        ),
        transform=axes.transAxes,
        fontsize=9,
        color="#555555",
    )
    axes.set_axis_off()
    figure.savefig(destination, dpi=220, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return destination


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize a projected CANOE transport network GraphML file."
    )
    parser.add_argument("--graph", type=Path, help="network_projected.graphml path")
    parser.add_argument("--out", type=Path, help="PNG or SVG output path")
    parser.add_argument("--size-by", choices=SIZE_METRICS, default=DEFAULT_SIZE_METRIC)
    parser.add_argument("--color-by", choices=COLOR_ATTRIBUTES, default=DEFAULT_COLOR_ATTRIBUTE)
    parser.add_argument(
        "--layout",
        choices=("spring", "kamada-kawai", "circular"),
        default="spring",
    )
    parser.add_argument("--top-labels", type=int, default=15)
    parser.add_argument(
        "--all-components",
        action="store_true",
        help="Plot every component instead of only the largest weak component.",
    )
    parser.add_argument("--title", help="Optional figure title")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    analysis_root = find_project_root() / "analysis_outputs"
    graph_path = args.graph or select_projected_graph(analysis_root)
    graph = load_projected_graph(graph_path)
    display_graph = select_display_graph(graph, args.all_components)
    output_path = args.out or Path(graph_path).with_name("network_visualization.png")

    print("\nNetwork visualization inputs:")
    print(f"  GraphML:         {Path(graph_path).resolve()}")
    print(f"  Displayed graph: {display_graph.number_of_nodes():,} nodes, "
          f"{display_graph.number_of_edges():,} edges")
    print(f"  Node size:       {args.size_by}")
    print(f"  Node colour:     {args.color_by}")
    print(f"  Layout:          {args.layout}")
    print(f"  Output figure:   {output_path}")

    written = render_network(
        display_graph,
        output_path,
        size_metric=args.size_by,
        color_by=args.color_by,
        layout=args.layout,
        top_labels=args.top_labels,
        title=args.title,
    )
    print(f"Saved network visualization: {written}")


if __name__ == "__main__":
    main()
