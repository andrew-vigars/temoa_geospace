"""Build and analyse deployed CANOE transport networks.

The model represents a directed transport link ``Ri -> Rj`` as a technology
located in the pseudo-region ``Ri-Rj``.  This module decodes those rows from a
solved SQLite database, contracts degree-two pass-through regions, and exposes
the result as NetworkX graphs suitable for centrality and clustering analysis.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import networkx as nx
import pandas as pd

from geocanoe.paths import find_project_root


MIN_DEPLOYED_FLOW = 1e-3
TRANSPORT_SUFFIXES = ("_PIPE", "_TRUCK")
TRANSPORT_TECHS = {"ELC_TRANS"}


@dataclass(frozen=True)
class NetworkInputs:
    """Registry definitions and solved tables needed to build the network."""

    flow_out: pd.DataFrame
    demand: pd.DataFrame
    edges: pd.DataFrame
    flow_in: pd.DataFrame = field(default_factory=pd.DataFrame)
    net_capacity: pd.DataFrame = field(default_factory=pd.DataFrame)
    built_capacity: pd.DataFrame = field(default_factory=pd.DataFrame)
    limit_capacity: pd.DataFrame = field(default_factory=pd.DataFrame)
    efficiency: pd.DataFrame = field(default_factory=pd.DataFrame)
    technology_specs: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True)
class NetworkAnalysis:
    """Decoded graphs and their tabular centrality results."""

    deployed: nx.MultiDiGraph
    contracted: nx.MultiDiGraph
    projected: nx.DiGraph
    node_metrics: pd.DataFrame
    edge_metrics: pd.DataFrame
    asset_inventory: pd.DataFrame
    transport_inventory: pd.DataFrame


def is_transport_technology(technology: object) -> bool:
    """Return whether a technology is represented on edge pseudo-regions."""

    tech = str(technology)
    return tech in TRANSPORT_TECHS or tech.endswith(TRANSPORT_SUFFIXES)


def _read_optional_table(
    connection: sqlite3.Connection,
    table: str,
) -> pd.DataFrame:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return pd.DataFrame()
    return pd.read_sql_query(f'SELECT * FROM "{table}"', connection)


def load_technology_registry(registry_dir: str | Path | None = None) -> pd.DataFrame:
    """Load the canonical node and transport technology relationships."""

    directory = (
        Path(registry_dir)
        if registry_dir is not None
        else find_project_root() / "registry"
    )
    sources = []
    for filename, source in (
        ("generation_efficiency.csv", "generation_efficiency"),
        ("transport_techs.csv", "transport_techs"),
    ):
        path = directory / filename
        if not path.exists():
            raise FileNotFoundError(f"Technology registry not found: {path}")
        table = pd.read_csv(path)
        required = {"tech", "input_comm", "output_comm"}
        missing = required - set(table.columns)
        if missing:
            raise ValueError(f"{filename} is missing columns: {sorted(missing)}")
        selected = table[["tech", "input_comm", "output_comm"]].copy()
        selected["definition_source"] = source
        sources.append(selected)
    return pd.concat(sources, ignore_index=True).drop_duplicates(
        ["tech", "input_comm", "output_comm"], ignore_index=True
    )


def load_network_inputs(db_path: str | Path, edge_path: str | Path) -> NetworkInputs:
    """Read registry topology, solved SQLite results, and graph-edge metadata."""

    with sqlite3.connect(Path(db_path)) as connection:
        flow_out = _read_optional_table(connection, "OutputFlowOut")
        flow_in = _read_optional_table(connection, "OutputFlowIn")
        demand = _read_optional_table(connection, "Demand")
        net_capacity = _read_optional_table(connection, "OutputNetCapacity")
        built_capacity = _read_optional_table(connection, "OutputBuiltCapacity")
        limit_capacity = _read_optional_table(connection, "LimitCapacity")
        efficiency = _read_optional_table(connection, "Efficiency")

    if flow_out.empty:
        raise ValueError(f"OutputFlowOut is missing or empty in {db_path}")

    edges = pd.read_csv(edge_path)
    required_edges = {"edge_region", "region_from", "region_to"}
    missing = required_edges - set(edges.columns)
    if missing:
        raise ValueError(f"Graph-edge CSV is missing columns: {sorted(missing)}")

    return NetworkInputs(
        flow_out=flow_out,
        demand=demand,
        edges=edges,
        flow_in=flow_in,
        net_capacity=net_capacity,
        built_capacity=built_capacity,
        limit_capacity=limit_capacity,
        efficiency=efficiency,
        technology_specs=load_technology_registry(),
    )


def find_solved_databases(output_root: str | Path) -> list[Path]:
    """Find solved SQLite databases beneath timestamped model-run folders."""

    root = Path(output_root)
    if not root.exists():
        raise FileNotFoundError(f"Model output directory not found: {root}")
    databases = sorted(root.rglob("solved_*.sqlite"))
    if not databases:
        raise FileNotFoundError(f"No solved_*.sqlite databases found under {root}")
    return databases


def select_solved_database(output_root: str | Path) -> Path:
    """Prompt for one solved database while showing its source run folder."""

    root = Path(output_root)
    databases = find_solved_databases(root)
    print("\nAvailable solved model outputs:")
    for index, database_path in enumerate(databases):
        print(f"  [{index}] {database_path.relative_to(root)}")
    selected_index = input("\nSelect solved output index: ").strip()
    try:
        return databases[int(selected_index)]
    except (ValueError, IndexError) as exc:
        raise SystemExit("Invalid solved output selection.") from exc


def _filter_output(
    frame: pd.DataFrame,
    *,
    scenario: str | None,
    period: int | None,
    min_flow: float,
) -> pd.DataFrame:
    required = {"region", "tech", "flow"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"OutputFlowOut is missing columns: {sorted(missing)}")

    selected = frame.copy()
    selected["flow"] = pd.to_numeric(selected["flow"], errors="coerce")
    selected = selected.loc[selected["flow"] > min_flow].copy()
    if scenario is None and "scenario" in selected:
        scenarios = selected["scenario"].dropna().unique()
        if len(scenarios) > 1:
            raise ValueError(
                "OutputFlowOut contains multiple scenarios; select one with --scenario"
            )
    if scenario is not None:
        if "scenario" not in selected:
            raise ValueError("OutputFlowOut has no scenario column")
        selected = selected.loc[selected["scenario"] == scenario].copy()
    if period is None and "period" in selected:
        periods = selected["period"].dropna().unique()
        if len(periods) > 1:
            raise ValueError(
                "OutputFlowOut contains multiple periods; select one with --period"
            )
    if period is not None:
        if "period" not in selected:
            raise ValueError("OutputFlowOut has no period column")
        selected = selected.loc[selected["period"] == period].copy()
    return selected


def resolve_snapshot(
    flow_out: pd.DataFrame,
    *,
    scenario: str | None,
    period: int | None,
    min_flow: float,
) -> tuple[str | None, int | None]:
    """Resolve omitted filters when the solved output has one clear snapshot."""

    active = flow_out.loc[
        pd.to_numeric(flow_out["flow"], errors="coerce") > min_flow
    ]
    selected_scenario = scenario
    if selected_scenario is None and "scenario" in active.columns:
        scenarios = active["scenario"].dropna().astype(str).unique()
        if len(scenarios) > 1:
            raise ValueError(
                "OutputFlowOut contains multiple scenarios; select one with --scenario"
            )
        if len(scenarios) == 1:
            selected_scenario = str(scenarios[0])

    selected_period = period
    if selected_period is None and "period" in active.columns:
        periods = pd.to_numeric(active["period"], errors="coerce").dropna().unique()
        if len(periods) > 1:
            raise ValueError(
                "OutputFlowOut contains multiple periods; select one with --period"
            )
        if len(periods) == 1:
            selected_period = int(periods[0])
    return selected_scenario, selected_period


def identify_asset_nodes(flow_out: pd.DataFrame, demand: pd.DataFrame) -> dict[str, set[str]]:
    """Return active node regions and their production/storage/demand roles."""

    roles: dict[str, set[str]] = defaultdict(set)
    node_rows = flow_out.loc[~flow_out["tech"].map(is_transport_technology)]
    for row in node_rows[["region", "tech"]].drop_duplicates().itertuples(index=False):
        tech = str(row.tech)
        if tech == "CO2_INJECT":
            role = "storage"
        elif tech in {"CO2_CAP", "CO2_CAPTURE"}:
            role = "emitter"
        elif tech.endswith("_DEMAND"):
            role = "demand"
        else:
            role = "production"
        roles[str(row.region)].add(role)

    if not demand.empty and {"region", "demand"}.issubset(demand.columns):
        positive = pd.to_numeric(demand["demand"], errors="coerce").fillna(0) > 0
        for region in demand.loc[positive, "region"].astype(str).unique():
            roles[region].add("demand")
    return dict(roles)


def build_deployed_graph(
    inputs: NetworkInputs,
    *,
    scenario: str | None = None,
    period: int | None = None,
    min_flow: float = MIN_DEPLOYED_FLOW,
) -> nx.MultiDiGraph:
    """Decode positive pseudo-edge output rows into a directed multigraph."""

    flow_out = _filter_output(
        inputs.flow_out,
        scenario=scenario,
        period=period,
        min_flow=min_flow,
    )
    demand = inputs.demand
    if period is not None and "period" in demand.columns:
        demand = demand.loc[demand["period"] == period].copy()
    if scenario is not None and "scenario" in demand.columns:
        demand = demand.loc[demand["scenario"] == scenario].copy()
    roles = identify_asset_nodes(flow_out, demand)
    capacity = _filter_result_table(
        inputs.net_capacity, scenario=scenario, period=period
    )
    if {"region", "tech", "capacity"}.issubset(capacity.columns):
        capacity["capacity"] = pd.to_numeric(
            capacity["capacity"], errors="coerce"
        ).fillna(0)
        deployed_local = capacity.loc[
            (capacity["capacity"] > min_flow)
            & ~capacity["tech"].map(is_transport_technology),
            ["region", "tech"],
        ].drop_duplicates()
        for row in deployed_local.itertuples(index=False):
            roles.setdefault(str(row.region), set()).add(
                _technology_role(str(row.tech))
            )
    transport = flow_out.loc[flow_out["tech"].map(is_transport_technology)].copy()

    edge_columns = ["edge_region", "region_from", "region_to"]
    for optional in ("distance_km", "lon_from", "lat_from", "lon_to", "lat_to"):
        if optional in inputs.edges.columns:
            edge_columns.append(optional)
    edge_lookup = inputs.edges[edge_columns].copy()
    for column in ("edge_region", "region_from", "region_to"):
        edge_lookup[column] = edge_lookup[column].astype(str)

    transport["region"] = transport["region"].astype(str)
    decoded = transport.merge(
        edge_lookup,
        left_on="region",
        right_on="edge_region",
        how="left",
        validate="many_to_one",
    )
    if decoded["region_from"].isna().any():
        unknown = sorted(decoded.loc[decoded["region_from"].isna(), "region"].unique())
        raise ValueError(f"Deployed pseudo-regions absent from graph-edge CSV: {unknown[:10]}")

    group_cols = ["region_from", "region_to", "edge_region", "tech"]
    for column in ("output_comm", "scenario", "period"):
        if column in decoded.columns:
            group_cols.append(column)
    aggregations: dict[str, tuple[str, str]] = {"flow": ("flow", "sum")}
    if "distance_km" in decoded.columns:
        aggregations["distance_km"] = ("distance_km", "first")
    decoded = decoded.groupby(group_cols, as_index=False, dropna=False).agg(**aggregations)

    graph = nx.MultiDiGraph()
    for region, node_roles in roles.items():
        graph.add_node(region, roles="|".join(sorted(node_roles)), is_asset=True)
    for row in decoded.to_dict(orient="records"):
        source = str(row.pop("region_from"))
        target = str(row.pop("region_to"))
        row["pseudo_regions"] = (str(row["edge_region"]),)
        row["path_regions"] = (source, target)
        row["segment_count"] = 1
        row["flow_min"] = float(row["flow"])
        graph.add_edge(source, target, **row)
    for node in graph:
        graph.nodes[node].setdefault("roles", "pass_through")
        graph.nodes[node].setdefault("is_asset", node in roles)
    return graph


def _layer_subgraphs(graph: nx.MultiDiGraph) -> Iterable[tuple[tuple[str, str], nx.DiGraph]]:
    layers: dict[tuple[str, str], nx.DiGraph] = {}
    for source, target, data in graph.edges(data=True):
        layer_key = (str(data.get("tech", "")), str(data.get("output_comm", "")))
        layer = layers.setdefault(layer_key, nx.DiGraph())
        if layer.has_edge(source, target):
            current = layer[source][target]
            current["flow"] += float(data["flow"])
            current["flow_min"] += float(data["flow_min"])
            current["pseudo_regions"] += tuple(data["pseudo_regions"])
        else:
            layer.add_edge(source, target, **data)
    return layers.items()


def _preserved_nodes(layer: nx.DiGraph, asset_nodes: set[str]) -> set[str]:
    preserved = {
        node
        for node in layer
        if node in asset_nodes or layer.in_degree(node) != 1 or layer.out_degree(node) != 1
    }
    # A closed directed ring has no natural endpoint. Keep one deterministic
    # anchor so it can be represented without an infinite walk.
    for component in nx.weakly_connected_components(layer):
        if not (component & preserved):
            preserved.add(min(component))
    return preserved


def contract_pass_through_nodes(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Collapse same-technology chains between assets or graph junctions.

    Distance is additive along a chain. ``flow`` and ``flow_min`` are the
    minimum segment flow, representing the usable/bottleneck throughput of the
    contracted route; all contributing pseudo-regions remain in the attributes.
    """

    contracted = nx.MultiDiGraph()
    asset_nodes = {node for node, data in graph.nodes(data=True) if data.get("is_asset")}

    for (tech, commodity), layer in _layer_subgraphs(graph):
        preserved = _preserved_nodes(layer, asset_nodes)
        visited: set[tuple[str, str]] = set()
        for source in sorted(preserved):
            if source not in layer:
                continue
            for first_target in sorted(layer.successors(source)):
                if (source, first_target) in visited:
                    continue
                path = [source, first_target]
                segments = [layer[source][first_target]]
                visited.add((source, first_target))
                current = first_target
                while current not in preserved:
                    successor = next(iter(layer.successors(current)))
                    if (current, successor) in visited:
                        break
                    visited.add((current, successor))
                    segments.append(layer[current][successor])
                    path.append(successor)
                    current = successor

                pseudo_regions = tuple(
                    region for segment in segments for region in segment["pseudo_regions"]
                )
                flows = [float(segment["flow_min"]) for segment in segments]
                distances = [
                    float(segment.get("distance_km", 1.0))
                    for segment in segments
                    if pd.notna(segment.get("distance_km", 1.0))
                ]
                attributes: dict[str, Any] = {
                    "tech": tech,
                    "output_comm": commodity,
                    "flow": min(flows),
                    "flow_min": min(flows),
                    "flow_mean": sum(flows) / len(flows),
                    "distance_km": sum(distances),
                    "segment_count": len(segments),
                    "pseudo_regions": pseudo_regions,
                    "path_regions": tuple(path),
                }
                contracted.add_edge(source, current, **attributes)

    # Preserve optimized local assets even when no deployed transport corridor
    # reaches them.  These zero-degree nodes are meaningful disconnected
    # components, not pass-through regions to be discarded.
    contracted.add_nodes_from(
        (node, graph.nodes[node]) for node in asset_nodes if node not in contracted
    )
    for node in contracted:
        contracted.nodes[node].update(graph.nodes[node])
    return contracted


def project_graph(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Aggregate parallel transport modes for topology-level centrality."""

    projected = nx.DiGraph()
    projected.add_nodes_from(graph.nodes(data=True))
    for source, target, data in graph.edges(data=True):
        capacity = float(data.get("flow_min", data.get("flow", 0.0)))
        distance = float(data.get("distance_km", 1.0))
        if projected.has_edge(source, target):
            edge = projected[source][target]
            edge["capacity"] += capacity
            edge["distance_km"] = min(edge["distance_km"], distance)
            edge["technologies"].add(str(data.get("tech", "")))
            edge["route_count"] += 1
        else:
            projected.add_edge(
                source,
                target,
                capacity=capacity,
                distance_km=distance,
                technologies={str(data.get("tech", ""))},
                route_count=1,
            )
    for node in projected:
        projected.nodes[node].update(graph.nodes[node])
    return projected


def _weighted_pagerank(
    graph: nx.DiGraph,
    *,
    weight: str,
    alpha: float = 0.85,
    tolerance: float = 1e-9,
    max_iterations: int = 200,
) -> dict[str, float]:
    """Compute PageRank without requiring NetworkX's optional SciPy backend."""

    count = graph.number_of_nodes()
    if count == 0:
        return {}
    rank = {node: 1.0 / count for node in graph}
    outgoing = {
        node: sum(float(data.get(weight, 1.0)) for _, _, data in graph.out_edges(node, data=True))
        for node in graph
    }
    base = (1.0 - alpha) / count
    for _ in range(max_iterations):
        dangling = alpha * sum(rank[node] for node, total in outgoing.items() if total == 0) / count
        updated = {node: base + dangling for node in graph}
        for source, target, data in graph.edges(data=True):
            total = outgoing[source]
            if total:
                updated[target] += alpha * rank[source] * float(data.get(weight, 1.0)) / total
        error = sum(abs(updated[node] - rank[node]) for node in graph)
        rank = updated
        if error <= count * tolerance:
            return rank
    raise nx.PowerIterationFailedConvergence(max_iterations)


def calculate_centrality(graph: nx.DiGraph) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate node centralities, clusters, and directed edge betweenness."""

    if graph.number_of_nodes() == 0:
        return pd.DataFrame(), pd.DataFrame()

    degree = nx.degree_centrality(graph)
    in_degree = nx.in_degree_centrality(graph)
    out_degree = nx.out_degree_centrality(graph)
    betweenness = nx.betweenness_centrality(graph, weight="distance_km")
    inward_closeness = nx.closeness_centrality(graph, distance="distance_km")
    outward_closeness = nx.closeness_centrality(
        graph.reverse(copy=False), distance="distance_km"
    )
    pagerank = _weighted_pagerank(graph, weight="capacity")
    undirected = graph.to_undirected()
    clustering = nx.clustering(undirected, weight="capacity")

    component_id: dict[str, int] = {}
    components = sorted(
        nx.weakly_connected_components(graph),
        key=lambda values: (-len(values), min(values)),
    )
    for index, component in enumerate(components):
        component_id.update({node: index for node in component})

    if undirected.number_of_edges():
        communities = list(
            nx.community.greedy_modularity_communities(
                undirected, weight="capacity"
            )
        )
    else:
        communities = [{node} for node in undirected]
    communities.sort(key=lambda values: (-len(values), min(values)))
    community_id = {
        node: index
        for index, community in enumerate(communities)
        for node in community
    }

    node_rows = []
    for node, data in graph.nodes(data=True):
        node_rows.append({
            "region": node,
            "roles": data.get("roles", "pass_through"),
            "component_id": component_id[node],
            "community_id": community_id[node],
            "degree_centrality": degree[node],
            "in_degree_centrality": in_degree[node],
            "out_degree_centrality": out_degree[node],
            "betweenness_centrality": betweenness[node],
            "inward_closeness": inward_closeness[node],
            "outward_closeness": outward_closeness[node],
            "pagerank": pagerank[node],
            "clustering_coefficient": clustering[node],
            "in_capacity": graph.in_degree(node, weight="capacity"),
            "out_capacity": graph.out_degree(node, weight="capacity"),
        })

    edge_betweenness = nx.edge_betweenness_centrality(graph, weight="distance_km")
    edge_rows = []
    for source, target, data in graph.edges(data=True):
        edge_rows.append({
            "region_from": source,
            "region_to": target,
            "technologies": "|".join(sorted(data["technologies"])),
            "capacity": data["capacity"],
            "distance_km": data["distance_km"],
            "route_count": data["route_count"],
            "edge_betweenness_centrality": edge_betweenness[(source, target)],
        })

    nodes = pd.DataFrame(node_rows).sort_values(
        "betweenness_centrality", ascending=False, ignore_index=True
    )
    edges = pd.DataFrame(edge_rows).sort_values(
        "edge_betweenness_centrality", ascending=False, ignore_index=True
    )
    return nodes, edges


def _filter_result_table(
    frame: pd.DataFrame,
    *,
    scenario: str | None,
    period: int | None,
) -> pd.DataFrame:
    """Apply snapshot filters only when the corresponding columns exist."""

    selected = frame.copy()
    if scenario is not None and "scenario" in selected.columns:
        selected = selected.loc[selected["scenario"].astype(str) == scenario].copy()
    if period is not None and "period" in selected.columns:
        values = pd.to_numeric(selected["period"], errors="coerce")
        selected = selected.loc[values == period].copy()
    return selected


def _technology_role(technology: str) -> str:
    if technology == "CO2_INJECT":
        return "storage"
    if technology in {"CO2_CAP", "CO2_CAPTURE"}:
        return "emitter"
    if technology.endswith("_DEMAND"):
        return "demand"
    return "production"


def _commodity_lookup(efficiency: pd.DataFrame) -> dict[tuple[str, str], tuple[str, str]]:
    if efficiency.empty:
        return {}
    required = {"region", "tech", "input_comm", "output_comm"}
    if not required.issubset(efficiency.columns):
        return {}
    grouped = efficiency.groupby(["region", "tech"], dropna=False)
    return {
        (str(region), str(tech)): (
            "|".join(sorted(set(rows["input_comm"].dropna().astype(str)))),
            "|".join(sorted(set(rows["output_comm"].dropna().astype(str)))),
        )
        for (region, tech), rows in grouped
    }


def build_asset_inventory(
    inputs: NetworkInputs,
    deployed_graph: nx.MultiDiGraph,
    *,
    scenario: str | None,
    period: int | None,
    min_flow: float,
) -> pd.DataFrame:
    """Classify regional assets as active, built-idle, or available-unused.

    ``LimitCapacity`` supplies candidate CO2/electricity assets, while solved
    capacity and activity tables determine what the optimization actually used.
    Connectivity is evaluated against the uncontracted deployed transport graph
    so a candidate lying on a used corridor is not mistaken for an isolated site.
    """

    available: dict[tuple[str, str], float] = defaultdict(float)
    limits = _filter_result_table(
        inputs.limit_capacity, scenario=scenario, period=period
    )
    if {"region", "tech_or_group", "capacity"}.issubset(limits.columns):
        limits["capacity"] = pd.to_numeric(limits["capacity"], errors="coerce").fillna(0)
        limits = limits.loc[limits["capacity"] > min_flow]
        for row in limits.itertuples(index=False):
            available[(str(row.region), str(row.tech_or_group))] += float(row.capacity)

    optimized: dict[tuple[str, str], float] = defaultdict(float)
    capacities = _filter_result_table(
        inputs.net_capacity, scenario=scenario, period=period
    )
    if {"region", "tech", "capacity"}.issubset(capacities.columns):
        capacities["capacity"] = pd.to_numeric(
            capacities["capacity"], errors="coerce"
        ).fillna(0)
        for row in capacities.loc[
            capacities["capacity"] > min_flow
        ].itertuples(index=False):
            if not is_transport_technology(row.tech):
                optimized[(str(row.region), str(row.tech))] += float(row.capacity)

    activity: dict[tuple[str, str], float] = defaultdict(float)
    flow = _filter_result_table(inputs.flow_out, scenario=scenario, period=period)
    if {"region", "tech", "flow"}.issubset(flow.columns):
        flow["flow"] = pd.to_numeric(flow["flow"], errors="coerce").fillna(0)
        for row in flow.loc[flow["flow"] > min_flow].itertuples(index=False):
            if not is_transport_technology(row.tech):
                activity[(str(row.region), str(row.tech))] += float(row.flow)

    commodities = _commodity_lookup(inputs.efficiency)
    keys = sorted(set(available) | set(optimized) | set(activity))
    if not keys:
        return pd.DataFrame()

    components: dict[str, int] = {}
    ordered_components = sorted(
        nx.weakly_connected_components(deployed_graph),
        key=lambda values: (-len(values), min(values)),
    )
    for component_id, members in enumerate(ordered_components):
        components.update({str(region): component_id for region in members})

    rows = []
    for region, tech in keys:
        available_capacity = available.get((region, tech), 0.0)
        optimized_capacity = optimized.get((region, tech), 0.0)
        output_activity = activity.get((region, tech), 0.0)
        if output_activity > min_flow:
            status = "active"
        elif optimized_capacity > min_flow:
            status = "deployed_inactive"
        else:
            status = "available_unused"
        connected = region in deployed_graph and deployed_graph.degree(region) > 0
        input_commodities, output_commodities = commodities.get(
            (region, tech), ("", "")
        )
        rows.append({
            "region": region,
            "technology": tech,
            "role": _technology_role(tech),
            "input_commodities": input_commodities,
            "output_commodities": output_commodities,
            "available_capacity": available_capacity,
            "optimized_capacity": optimized_capacity,
            "output_activity": output_activity,
            "status": status,
            "on_deployed_transport_network": connected,
            "component_id": components.get(region),
            "disconnected_from_deployed_transport": not connected,
        })
    return pd.DataFrame(rows).sort_values(
        ["status", "technology", "region"], ignore_index=True
    )


def build_transport_inventory(
    inputs: NetworkInputs,
    *,
    scenario: str | None,
    period: int | None,
    min_flow: float,
) -> pd.DataFrame:
    """Compare every SQL-defined candidate transport link with solved use."""

    required = {"region", "tech", "input_comm", "output_comm"}
    if inputs.efficiency.empty or not required.issubset(inputs.efficiency.columns):
        return pd.DataFrame()
    candidates = inputs.efficiency.loc[
        inputs.efficiency["tech"].map(is_transport_technology),
        ["region", "tech", "input_comm", "output_comm"],
    ].drop_duplicates()
    candidates = candidates.rename(columns={"region": "edge_region"})
    candidates["edge_region"] = candidates["edge_region"].astype(str)

    edge_columns = [
        column
        for column in ("edge_region", "region_from", "region_to", "distance_km")
        if column in inputs.edges.columns
    ]
    candidates = candidates.merge(
        inputs.edges[edge_columns], on="edge_region", how="left", validate="many_to_one"
    )

    flow = _filter_result_table(inputs.flow_out, scenario=scenario, period=period)
    if not flow.empty:
        flow["flow"] = pd.to_numeric(flow["flow"], errors="coerce").fillna(0)
        flow_totals = (
            flow.groupby(["region", "tech"], as_index=False)["flow"].sum()
            .rename(columns={"region": "edge_region", "flow": "output_activity"})
        )
        candidates = candidates.merge(
            flow_totals, on=["edge_region", "tech"], how="left"
        )
    else:
        candidates["output_activity"] = 0.0

    capacity = _filter_result_table(
        inputs.net_capacity, scenario=scenario, period=period
    )
    if not capacity.empty:
        capacity["capacity"] = pd.to_numeric(
            capacity["capacity"], errors="coerce"
        ).fillna(0)
        capacity_totals = (
            capacity.groupby(["region", "tech"], as_index=False)["capacity"].sum()
            .rename(columns={"region": "edge_region", "capacity": "optimized_capacity"})
        )
        candidates = candidates.merge(
            capacity_totals, on=["edge_region", "tech"], how="left"
        )
    else:
        candidates["optimized_capacity"] = 0.0

    for column in ("output_activity", "optimized_capacity"):
        candidates[column] = candidates[column].fillna(0.0)
    candidates["status"] = "available_unused"
    candidates.loc[candidates["optimized_capacity"] > min_flow, "status"] = "deployed_inactive"
    candidates.loc[candidates["output_activity"] > min_flow, "status"] = "active"
    return candidates.sort_values(["status", "tech", "edge_region"], ignore_index=True)


def analyse_network(
    inputs: NetworkInputs,
    *,
    scenario: str | None = None,
    period: int | None = None,
    min_flow: float = MIN_DEPLOYED_FLOW,
) -> NetworkAnalysis:
    """Run pseudo-edge decoding, contraction, projection, and centrality."""

    deployed = build_deployed_graph(
        inputs, scenario=scenario, period=period, min_flow=min_flow
    )
    contracted = contract_pass_through_nodes(deployed)
    projected = project_graph(contracted)
    node_metrics, edge_metrics = calculate_centrality(projected)
    asset_inventory = build_asset_inventory(
        inputs,
        deployed,
        scenario=scenario,
        period=period,
        min_flow=min_flow,
    )
    transport_inventory = build_transport_inventory(
        inputs,
        scenario=scenario,
        period=period,
        min_flow=min_flow,
    )
    for record in node_metrics.to_dict(orient="records"):
        region = str(record.pop("region"))
        projected.nodes[region].update(record)
    for record in edge_metrics.to_dict(orient="records"):
        source = str(record.pop("region_from"))
        target = str(record.pop("region_to"))
        projected[source][target]["edge_betweenness_centrality"] = record[
            "edge_betweenness_centrality"
        ]
    return NetworkAnalysis(
        deployed,
        contracted,
        projected,
        node_metrics,
        edge_metrics,
        asset_inventory,
        transport_inventory,
    )


def _graphml_copy(graph: nx.Graph) -> nx.Graph:
    output = graph.copy()
    for _, data in output.nodes(data=True):
        for key, value in list(data.items()):
            if isinstance(value, (set, tuple, list)):
                data[key] = "|".join(map(str, value))
    for _, _, data in output.edges(data=True):
        for key, value in list(data.items()):
            if isinstance(value, (set, tuple, list)):
                data[key] = "|".join(map(str, value))
    return output


def export_analysis(
    analysis: NetworkAnalysis,
    output_dir: str | Path,
    *,
    source_db: str | Path | None = None,
    edge_path: str | Path | None = None,
    scenario: str | None = None,
    period: int | None = None,
    min_flow: float = MIN_DEPLOYED_FLOW,
) -> list[Path]:
    """Write centrality tables and reusable contracted graphs."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = [
        destination / "network_node_centrality.csv",
        destination / "network_edge_betweenness.csv",
        destination / "network_asset_inventory.csv",
        destination / "network_disconnected_assets.csv",
        destination / "network_transport_inventory.csv",
        destination / "network_contracted.graphml",
        destination / "network_projected.graphml",
        destination / "network_manifest.json",
    ]
    analysis.node_metrics.to_csv(paths[0], index=False)
    analysis.edge_metrics.to_csv(paths[1], index=False)
    analysis.asset_inventory.to_csv(paths[2], index=False)
    disconnected = (
        analysis.asset_inventory.loc[
            analysis.asset_inventory["disconnected_from_deployed_transport"]
        ].copy()
        if not analysis.asset_inventory.empty
        else analysis.asset_inventory.copy()
    )
    disconnected.to_csv(paths[3], index=False)
    analysis.transport_inventory.to_csv(paths[4], index=False)
    nx.write_graphml(_graphml_copy(analysis.contracted), paths[5])
    nx.write_graphml(_graphml_copy(analysis.projected), paths[6])
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_database": str(Path(source_db).resolve()) if source_db else None,
        "source_graph_edges": str(Path(edge_path).resolve()) if edge_path else None,
        "scenario": scenario,
        "period": period,
        "minimum_deployed_flow": min_flow,
        "deployed_nodes": analysis.deployed.number_of_nodes(),
        "deployed_segments": analysis.deployed.number_of_edges(),
        "contracted_nodes": analysis.contracted.number_of_nodes(),
        "contracted_routes": analysis.contracted.number_of_edges(),
        "projected_nodes": analysis.projected.number_of_nodes(),
        "projected_edges": analysis.projected.number_of_edges(),
        "asset_inventory_rows": len(analysis.asset_inventory),
        "disconnected_asset_rows": len(disconnected),
        "transport_inventory_rows": len(analysis.transport_inventory),
    }
    paths[7].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decode a solved CANOE SQLite database into a NetworkX graph."
    )
    parser.add_argument("--db", type=Path, help="Solved SQLite database")
    parser.add_argument("--edges", type=Path, help="Matching graph-edge CSV")
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "Output directory (default: "
            "analysis_outputs/<source-run>/network)"
        ),
    )
    parser.add_argument("--scenario", help="Optional scenario filter")
    parser.add_argument("--period", type=int, help="Optional model-period filter")
    parser.add_argument("--min-flow", type=float, default=MIN_DEPLOYED_FLOW)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    project_root = find_project_root()
    if args.db is not None and args.edges is not None:
        # A fully specified analysis remains independent of Folium/GeoPandas.
        db_path = args.db.resolve()
        edge_path = args.edges.resolve()
        run_dir = db_path.parent
    else:
        # Keep map/path imports out of the reusable SQL/NetworkX API. They are
        # needed only for interactive run selection or edge-path inference.
        from geocanoe.analysis import maps

        project_paths = maps.resolve_project_paths()
        if args.db is None:
            db_path = select_solved_database(project_paths.output_root)
            selected = maps.SelectedRun(run_dir=db_path.parent, db_path=db_path)
        else:
            db_path = args.db.resolve()
            selected = maps.SelectedRun(run_dir=db_path.parent, db_path=db_path)
        run_dir = selected.run_dir

        edge_path = args.edges
        if edge_path is None:
            edge_path = maps.infer_geospatial_paths(
                project_paths.data_files, selected
            ).edge_path
    output_dir = (
        args.out
        or project_root / "analysis_outputs" / run_dir.name / "network"
    )

    inputs = load_network_inputs(db_path, edge_path)
    scenario, period = resolve_snapshot(
        inputs.flow_out,
        scenario=args.scenario,
        period=args.period,
        min_flow=args.min_flow,
    )
    print("\nNetwork analysis inputs:")
    print(f"  Solved database: {db_path}")
    print(f"  Graph edges:     {edge_path}")
    print(f"  Scenario:        {scenario or 'not recorded'}")
    print(f"  Period:          {period if period is not None else 'not recorded'}")
    print(f"  Output folder:   {output_dir}")

    analysis = analyse_network(
        inputs,
        scenario=scenario,
        period=period,
        min_flow=args.min_flow,
    )
    written = export_analysis(
        analysis,
        output_dir,
        source_db=db_path,
        edge_path=edge_path,
        scenario=scenario,
        period=period,
        min_flow=args.min_flow,
    )
    print(
        f"Decoded {analysis.deployed.number_of_edges():,} deployed segments into "
        f"{analysis.contracted.number_of_edges():,} contracted routes."
    )
    print(
        f"Projected network: {analysis.projected.number_of_nodes():,} nodes, "
        f"{analysis.projected.number_of_edges():,} edges."
    )
    if not analysis.asset_inventory.empty:
        unused = analysis.asset_inventory["status"].eq("available_unused").sum()
        disconnected = analysis.asset_inventory[
            "disconnected_from_deployed_transport"
        ].sum()
        print(
            f"Asset inventory: {len(analysis.asset_inventory):,} rows; "
            f"{unused:,} available-unused; {disconnected:,} disconnected."
        )
    if not analysis.transport_inventory.empty:
        active_transport = analysis.transport_inventory["status"].eq("active").sum()
        print(
            f"Transport inventory: {len(analysis.transport_inventory):,} candidate "
            f"links; {active_transport:,} active."
        )
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()
