"""Render optimized CANOE networks with TEMOA's interactive Gravis/D3 backend."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import html
import json
from pathlib import Path
from typing import Any
import warnings

import networkx as nx
import numpy as np
import pandas as pd

from geocanoe.analysis.network import (
    MIN_DEPLOYED_FLOW,
    NetworkInputs,
    is_transport_technology,
    load_network_inputs,
    resolve_snapshot,
)
from geocanoe.paths import find_project_root


# Okabe-Ito-derived colors preserve the commodity identity already used by
# geocanoe.analysis.maps while remaining distinguishable for common forms of
# red-green color-vision deficiency.
COMMODITY_COLORS = {
    "elc": "#0072B2",
    "h2": "#009E73",
    "co2": "#666666",
    "co2_stored": "#CC79A7",
    "ch3oh": "#E69F00",
    "gsl": "#D55E00",
    "d_gsl": "#D55E00",
    "ethos": "#000000",
}
TEMOA_LAYER_COLORS = {
    "source": "#009E73",
    "physical": "#56B4E9",
    "demand": "#E69F00",
    "capacity": "#CC79A7",
    "exchange": "#0072B2",
    "waste": "#D55E00",
}
TECHNOLOGY_COLORS = {
    "production": "#000000",
    "pipeline": "#0072B2",
    "truck": "#E69F00",
    "transmission": "#56B4E9",
    "capture": "#D55E00",
    "storage": "#CC79A7",
    "demand": "#009E73",
    "other": "#666666",
}
ROLE_COLORS = {
    "production": TEMOA_LAYER_COLORS["source"],
    "pass_through": TEMOA_LAYER_COLORS["physical"],
    "demand": TEMOA_LAYER_COLORS["demand"],
    "storage": TEMOA_LAYER_COLORS["capacity"],
    "emitter": TEMOA_LAYER_COLORS["waste"],
}
HUB_COLOR = "#332288"
INTEGRATED_HUB_TECHS = {"H2_PLANT", "METOH_PLANT", "GSL_PLANT"}
OPPORTUNITY_COLORS = {
    "CO2_CAP": "#CC79A7",
    "ELC_GEN": "#56B4E9",
}


def find_analysis_manifests(analysis_root: str | Path) -> list[Path]:
    """Find traceable network analyses available for HTML rendering."""

    root = Path(analysis_root)
    if not root.exists():
        raise FileNotFoundError(
            f"Analysis output directory not found: {root}. Run geocanoe-network first."
        )
    manifests = sorted(root.rglob("network_manifest.json"))
    if not manifests:
        raise FileNotFoundError(
            f"No network manifests found under {root}. Run geocanoe-network first."
        )
    return manifests


def select_analysis_manifest(analysis_root: str | Path) -> Path:
    """Select an analysis manifest while displaying its source-run folder."""

    root = Path(analysis_root)
    manifests = find_analysis_manifests(root)
    if len(manifests) == 1:
        print(f"Selected network analysis: {manifests[0].parent.relative_to(root)}")
        return manifests[0]

    print("\nAvailable network analyses:")
    for index, manifest in enumerate(manifests):
        print(f"  [{index}] {manifest.parent.relative_to(root)}")
    selected_index = input("\nSelect network analysis index: ").strip()
    try:
        return manifests[int(selected_index)]
    except (ValueError, IndexError) as exc:
        raise SystemExit("Invalid network-analysis selection.") from exc


def resolve_manifest_path(value: str | Path) -> Path:
    """Accept either a network analysis directory or its manifest path."""

    path = Path(value)
    if path.is_dir():
        path = path / "network_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Network analysis manifest not found: {path}")
    return path


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a network provenance manifest."""

    manifest_path = resolve_manifest_path(path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def commodity_layer(commodity: str, flag: str | None) -> str:
    """Translate CANOE commodity metadata into TEMOA's visual layers."""

    flag_text = flag or ""
    if commodity == "co2_stored" or "w" in flag_text:
        return "waste"
    if "s" in flag_text:
        return "source"
    if "d" in flag_text:
        return "demand"
    return "physical"


def technology_category(technology: str) -> str:
    """Return a stable visual category for an optimized technology."""

    if technology.endswith("_PIPE"):
        return "pipeline"
    if technology.endswith("_TRUCK"):
        return "truck"
    if technology == "ELC_TRANS":
        return "transmission"
    if technology in {"CO2_CAP", "CO2_CAPTURE"}:
        return "capture"
    if technology == "CO2_INJECT":
        return "storage"
    if technology.endswith("_DEMAND") or technology in {"GSL_BACKUP", "GSL_EXISTING"}:
        return "demand"
    if technology.endswith("_GEN") or technology.endswith("_PLANT"):
        return "production"
    return "other"


def _scale_values(
    values: Mapping[Any, float],
    minimum: float,
    maximum: float,
) -> dict[Any, float]:
    """Square-root scale non-negative values into a bounded visual range."""

    if not values:
        return {}
    keys = list(values)
    numeric = np.asarray([values[key] for key in keys], dtype=float)
    numeric[~np.isfinite(numeric)] = 0.0
    transformed = np.sqrt(np.maximum(numeric, 0.0))
    low = float(transformed.min())
    high = float(transformed.max())
    if high <= low:
        scaled = np.full_like(transformed, (minimum + maximum) / 2.0)
    else:
        scaled = minimum + (maximum - minimum) * (transformed - low) / (high - low)
    return dict(zip(keys, scaled, strict=True))


def _format_value(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(numeric) >= 1_000_000:
        return f"{numeric / 1_000_000:,.2f}M"
    if abs(numeric) >= 1_000:
        return f"{numeric / 1_000:,.2f}k"
    return f"{numeric:,.3g}"


def _hover_table(rows: Sequence[tuple[str, object]]) -> str:
    return "<br>".join(
        f"<b>{html.escape(label)}:</b> {html.escape(_format_value(value))}"
        for label, value in rows
    )


def _load_commodity_flags(db_path: str | Path) -> dict[str, str]:
    import sqlite3

    with sqlite3.connect(Path(db_path)) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Commodity'"
        ).fetchone()
        if table_exists is None:
            return {}
        rows = connection.execute("SELECT name, flag FROM Commodity").fetchall()
    return {str(name): str(flag) for name, flag in rows}


def build_commodity_graph(
    inputs: NetworkInputs,
    *,
    contracted_transport: nx.MultiDiGraph | None = None,
    commodity_flags: Mapping[str, str] | None = None,
    scenario: str | None = None,
    period: int | None = None,
    min_flow: float = MIN_DEPLOYED_FLOW,
    edge_color_by: str = "technology",
) -> nx.MultiDiGraph:
    """Build a TEMOA-style region/commodity graph from positive optimized flows."""

    flow = inputs.flow_out.copy()
    flow["flow"] = pd.to_numeric(flow["flow"], errors="coerce")
    flow = flow.loc[flow["flow"] > min_flow].copy()
    if scenario is not None and "scenario" in flow.columns:
        flow = flow.loc[flow["scenario"] == scenario].copy()
    if period is not None and "period" in flow.columns:
        flow = flow.loc[flow["period"] == period].copy()
    required = {"region", "tech", "input_comm", "output_comm", "flow"}
    missing = required - set(flow.columns)
    if missing:
        raise ValueError(f"OutputFlowOut is missing columns: {sorted(missing)}")

    edge_lookup = inputs.edges[
        [
            column
            for column in ("edge_region", "region_from", "region_to", "distance_km")
            if column in inputs.edges.columns
        ]
    ].copy()
    flow["region"] = flow["region"].astype(str)
    flow = flow.merge(
        edge_lookup,
        left_on="region",
        right_on="edge_region",
        how="left",
        validate="many_to_one",
    )
    transport = flow["tech"].map(is_transport_technology)
    flow["source_region"] = flow["region"]
    flow["target_region"] = flow["region"]
    flow.loc[transport, "source_region"] = flow.loc[transport, "region_from"]
    flow.loc[transport, "target_region"] = flow.loc[transport, "region_to"]
    if flow.loc[transport, ["source_region", "target_region"]].isna().any().any():
        unknown = sorted(flow.loc[transport & flow["source_region"].isna(), "region"].unique())
        raise ValueError(f"Transport pseudo-regions absent from graph-edge CSV: {unknown[:10]}")

    group_columns = [
        "source_region",
        "target_region",
        "input_comm",
        "output_comm",
        "tech",
    ]
    aggregations: dict[str, tuple[str, str]] = {"flow": ("flow", "sum")}
    if "distance_km" in flow.columns:
        aggregations["distance_km"] = ("distance_km", "first")
    observed_routes = flow.groupby(
        group_columns, as_index=False, dropna=False
    ).agg(**aggregations)

    # The model registry defines technology relationships; the solved database
    # determines where those registered processes were actually deployed.  Do
    # not infer a technology's topology solely from whichever output rows happen
    # to survive a display filter.
    routes = observed_routes
    if not inputs.efficiency.empty:
        active_pairs = flow.loc[
            ~flow["tech"].map(is_transport_technology), ["region", "tech"]
        ].drop_duplicates()
        capacity = inputs.net_capacity.copy()
        if not capacity.empty and {"region", "tech", "capacity"}.issubset(capacity.columns):
            if scenario is not None and "scenario" in capacity.columns:
                capacity = capacity.loc[capacity["scenario"] == scenario]
            if period is not None and "period" in capacity.columns:
                capacity = capacity.loc[
                    pd.to_numeric(capacity["period"], errors="coerce") == period
                ]
            capacity["capacity"] = pd.to_numeric(
                capacity["capacity"], errors="coerce"
            ).fillna(0)
            active_capacity = capacity.loc[
                (capacity["capacity"] > min_flow)
                & ~capacity["tech"].map(is_transport_technology),
                ["region", "tech"],
            ].drop_duplicates()
            active_pairs = pd.concat(
                [active_pairs, active_capacity], ignore_index=True
            ).drop_duplicates()

        efficiency = inputs.efficiency.copy()
        required_efficiency = {"region", "tech", "input_comm", "output_comm"}
        if required_efficiency.issubset(efficiency.columns) and not active_pairs.empty:
            efficiency["region"] = efficiency["region"].astype(str)
            active_pairs["region"] = active_pairs["region"].astype(str)
            local_relations = efficiency.merge(
                active_pairs, on=["region", "tech"], how="inner", validate="many_to_one"
            )[["region", "input_comm", "output_comm", "tech"]].drop_duplicates()
            local_observed = observed_routes.loc[
                ~observed_routes["tech"].map(is_transport_technology),
                ["source_region", "input_comm", "output_comm", "tech", "flow"],
            ].rename(columns={"source_region": "region", "flow": "output_flow"})
            local_relations = local_relations.merge(
                local_observed,
                on=["region", "input_comm", "output_comm", "tech"],
                how="left",
                validate="one_to_one",
            )
            local_relations["output_flow"] = local_relations["output_flow"].fillna(0.0)
            local_input = inputs.flow_in.copy()
            if not local_input.empty:
                local_input["flow"] = pd.to_numeric(
                    local_input["flow"], errors="coerce"
                ).fillna(0.0)
                if scenario is not None and "scenario" in local_input.columns:
                    local_input = local_input.loc[local_input["scenario"] == scenario]
                if period is not None and "period" in local_input.columns:
                    local_input = local_input.loc[
                        pd.to_numeric(local_input["period"], errors="coerce") == period
                    ]
                local_input = (
                    local_input.groupby(
                        ["region", "input_comm", "output_comm", "tech"],
                        as_index=False,
                    )["flow"].sum()
                    .rename(columns={"flow": "input_flow"})
                )
                local_input["region"] = local_input["region"].astype(str)
                local_relations = local_relations.merge(
                    local_input,
                    on=["region", "input_comm", "output_comm", "tech"],
                    how="left",
                    validate="one_to_one",
                )
            else:
                local_relations["input_flow"] = local_relations["output_flow"]
            local_relations["input_flow"] = local_relations["input_flow"].fillna(
                local_relations["output_flow"]
            )
            local_relations["flow"] = local_relations["input_flow"]
            local_relations["source_region"] = local_relations["region"]
            local_relations["target_region"] = local_relations["region"]
            local_relations["distance_km"] = 0.0
            local_relations = local_relations.drop(columns="region")
            transport_routes = observed_routes.loc[
                observed_routes["tech"].map(is_transport_technology)
            ]
            routes = pd.concat(
                [local_relations, transport_routes], ignore_index=True, sort=False
            )

    flags = commodity_flags or {}
    registry_relations = (
        set(
            inputs.technology_specs[
                ["tech", "input_comm", "output_comm"]
            ].astype(str).itertuples(index=False, name=None)
        )
        if not inputs.technology_specs.empty
        else set()
    )
    graph = nx.MultiDiGraph()
    edge_values: dict[int, float] = {}
    edge_records: list[tuple[str, str, dict[str, Any]]] = []
    asset_nodes: set[str] = set()

    def ensure_node(node: str, region: str, commodity: str, *, is_asset: bool) -> None:
        layer = commodity_layer(commodity, flags.get(commodity))
        if node not in graph:
            graph.add_node(
                node,
                label="",
                region=region,
                commodity=commodity,
                temoa_layer=layer,
                color=COMMODITY_COLORS.get(
                    commodity, TEMOA_LAYER_COLORS.get(layer, "#666666")
                ),
                is_asset=is_asset,
                hover=_hover_table(
                    (("Region", region), ("Commodity", commodity), ("TEMOA layer", layer))
                ),
            )
        elif is_asset:
            graph.nodes[node]["is_asset"] = True
        if is_asset:
            asset_nodes.add(node)

    for row in routes.to_dict(orient="records"):
        source_region = str(row["source_region"])
        target_region = str(row["target_region"])
        input_comm = str(row["input_comm"])
        output_comm = str(row["output_comm"])
        technology = str(row["tech"])
        is_transport = is_transport_technology(technology)
        if contracted_transport is not None and is_transport:
            continue
        input_flow = (
            float(row["input_flow"])
            if pd.notna(row.get("input_flow"))
            else float(row["flow"])
        )
        output_flow = (
            float(row["output_flow"])
            if pd.notna(row.get("output_flow"))
            else float(row["flow"])
        )
        source = f"{source_region}::{input_comm}"
        target = f"{target_region}::{output_comm}"
        ensure_node(source, source_region, input_comm, is_asset=not is_transport)
        ensure_node(target, target_region, output_comm, is_asset=not is_transport)
        category = technology_category(technology)
        edge_color = (
            COMMODITY_COLORS.get(output_comm, "#666666")
            if edge_color_by == "commodity"
            else TECHNOLOGY_COLORS[category]
        )
        attributes = {
            "label": technology,
            "technology": technology,
            "technology_category": category,
            "definition_source": (
                "registry"
                if (technology, input_comm, output_comm) in registry_relations
                else "sqlite_efficiency"
            ),
            "commodity": output_comm,
            "flow": float(row["flow"]),
            "input_flow": input_flow,
            "output_flow": output_flow,
            "distance_km": (
                float(row["distance_km"])
                if pd.notna(row.get("distance_km"))
                else 0.0
            ),
            "color": edge_color,
            "hover": _hover_table(
                (
                    ("Technology", technology),
                    ("Category", category),
                    (
                        "Definition",
                        "registry"
                        if (technology, input_comm, output_comm) in registry_relations
                        else "SQLite Efficiency",
                    ),
                    ("Input", input_comm),
                    ("Output", output_comm),
                    ("Input flow", input_flow),
                    ("Output flow", output_flow),
                    ("Distance (km)", row.get("distance_km", 0.0)),
                )
            ),
        }
        edge_records.append((source, target, attributes))
        edge_values[len(edge_records) - 1] = float(row["flow"])

    if contracted_transport is not None:
        for source_region, target_region, data in contracted_transport.edges(data=True):
            technology = str(data.get("tech", "unknown"))
            commodity = str(data.get("output_comm", "unknown"))
            source = f"{source_region}::{commodity}"
            target = f"{target_region}::{commodity}"
            ensure_node(source, str(source_region), commodity, is_asset=False)
            ensure_node(target, str(target_region), commodity, is_asset=False)
            category = technology_category(technology)
            route_flow = float(data.get("flow_min", data.get("flow", 0.0)))
            attributes = {
                "label": technology,
                "technology": technology,
                "technology_category": category,
                "commodity": commodity,
                "flow": route_flow,
                "distance_km": float(data.get("distance_km", 0.0)),
                "segment_count": int(data.get("segment_count", 1)),
                "color": (
                    COMMODITY_COLORS.get(commodity, "#666666")
                    if edge_color_by == "commodity"
                    else TECHNOLOGY_COLORS[category]
                ),
                "hover": _hover_table(
                    (
                        ("Technology", technology),
                        ("Category", category),
                        ("Commodity", commodity),
                        ("From", source_region),
                        ("To", target_region),
                        ("Bottleneck flow", route_flow),
                        ("Distance (km)", data.get("distance_km", 0.0)),
                        ("Contracted segments", data.get("segment_count", 1)),
                    )
                ),
            }
            edge_records.append((source, target, attributes))
            edge_values[len(edge_records) - 1] = route_flow

    edge_sizes = _scale_values(edge_values, 0.7, 7.0)
    incident_flow: dict[str, float] = {node: 0.0 for node in graph}
    for index, (source, target, attributes) in enumerate(edge_records):
        attributes["size"] = edge_sizes[index]
        graph.add_edge(source, target, **attributes)
        incident_flow[source] += float(attributes["flow"])
        incident_flow[target] += float(attributes["flow"])
    node_sizes = _scale_values(incident_flow, 8.0, 38.0)
    for node, data in graph.nodes(data=True):
        is_asset = node in asset_nodes
        data["label"] = (
            f"{data['region']} · {data['commodity']}" if is_asset else ""
        )
        data["size"] = min(44.0, node_sizes[node] + (7.0 if is_asset else -2.0))
    return graph


def build_corridor_graph(
    analysis_dir: str | Path,
    *,
    size_by: str = "betweenness_centrality",
    edge_color_by: str = "technology",
) -> nx.MultiDiGraph:
    """Prepare the contracted regional network for interactive HTML display."""

    directory = Path(analysis_dir)
    source = directory / "network_contracted.graphml"
    if not source.exists():
        raise FileNotFoundError(f"Contracted GraphML file not found: {source}")
    graph = nx.MultiDiGraph(nx.read_graphml(source))

    metrics_path = directory / "network_node_centrality.csv"
    metrics: dict[str, float] = {}
    if metrics_path.exists():
        table = pd.read_csv(metrics_path)
        if {"region", size_by}.issubset(table.columns):
            metrics = dict(
                zip(table["region"].astype(str), table[size_by].astype(float), strict=True)
            )
    node_sizes = _scale_values(
        {str(node): metrics.get(str(node), 0.0) for node in graph}, 8.0, 42.0
    )
    for node, data in graph.nodes(data=True):
        roles = str(data.get("roles", "pass_through")).split("|")
        precedence = ("demand", "storage", "emitter", "production", "pass_through")
        role = next((candidate for candidate in precedence if candidate in roles), roles[0])
        data.update(
            label=str(node),
            color=ROLE_COLORS.get(role, "#666666"),
            size=node_sizes[str(node)],
            role=role,
            hover=_hover_table(
                (
                    ("Region", node),
                    ("Roles", data.get("roles", role)),
                    (size_by.replace("_", " ").title(), metrics.get(str(node), 0.0)),
                )
            ),
        )

    edge_values = {
        (source_node, target_node, key): float(data.get("flow_min", data.get("flow", 0.0)))
        for source_node, target_node, key, data in graph.edges(keys=True, data=True)
    }
    edge_sizes = _scale_values(edge_values, 0.7, 7.0)
    for source_node, target_node, key, data in graph.edges(keys=True, data=True):
        technology = str(data.get("tech", "unknown"))
        commodity = str(data.get("output_comm", "unknown"))
        category = technology_category(technology)
        data.update(
            label=technology,
            color=(
                COMMODITY_COLORS.get(commodity, "#666666")
                if edge_color_by == "commodity"
                else TECHNOLOGY_COLORS[category]
            ),
            size=edge_sizes[(source_node, target_node, key)],
            technology_category=category,
            hover=_hover_table(
                (
                    ("Technology", technology),
                    ("Commodity", commodity),
                    ("From", source_node),
                    ("To", target_node),
                    ("Bottleneck flow", data.get("flow_min", data.get("flow", 0.0))),
                    ("Distance (km)", data.get("distance_km", 0.0)),
                    ("Segments", data.get("segment_count", 1)),
                )
            ),
        )
    return graph


def build_hub_graph(
    analysis_dir: str | Path,
    *,
    edge_color_by: str = "technology",
) -> nx.MultiDiGraph:
    """Build a regional infrastructure view with colocated processes as hubs.

    Local conversion relationships have zero geographic distance and therefore
    become attributes of one regional node. Only contracted inter-regional
    transport remains as an edge, making this view appropriate for spatial
    centrality and cluster interpretation.
    """

    directory = Path(analysis_dir)
    source = directory / "network_contracted.graphml"
    if not source.exists():
        raise FileNotFoundError(f"Contracted GraphML file not found: {source}")
    graph = nx.MultiDiGraph(nx.read_graphml(source))

    asset_path = directory / "network_asset_inventory.csv"
    assets = pd.read_csv(asset_path) if asset_path.exists() else pd.DataFrame()
    deployed_assets = (
        assets.loc[assets["status"] != "available_unused"].copy()
        if not assets.empty
        else assets
    )
    asset_groups = {
        str(region): rows
        for region, rows in deployed_assets.groupby("region")
    }

    metric_path = directory / "network_node_centrality.csv"
    metrics = pd.read_csv(metric_path) if metric_path.exists() else pd.DataFrame()
    metric_rows = (
        metrics.set_index(metrics["region"].astype(str)).to_dict(orient="index")
        if not metrics.empty
        else {}
    )

    activity_values: dict[str, float] = {}
    for node in graph:
        rows = asset_groups.get(str(node))
        activity_values[str(node)] = (
            float(rows["output_activity"].sum()) if rows is not None else 0.0
        )
    activity_sizes = _scale_values(activity_values, 10.0, 42.0)

    for node, data in graph.nodes(data=True):
        region = str(node)
        rows = asset_groups.get(region)
        technologies = (
            sorted(set(rows["technology"].astype(str)))
            if rows is not None
            else []
        )
        roles = (
            sorted(set(rows["role"].astype(str)))
            if rows is not None
            else str(data.get("roles", "pass_through")).split("|")
        )
        conversion_techs = set(technologies) & INTEGRATED_HUB_TECHS
        is_hub = len(conversion_techs) >= 2 or (
            "GSL_DEMAND" in technologies and bool(conversion_techs)
        )
        metric = metric_rows.get(region, {})
        data.update(
            region=region,
            label=(
                f"{region} · hub ({len(technologies)})"
                if is_hub
                else region if technologies else ""
            ),
            color=(
                HUB_COLOR
                if is_hub
                else ROLE_COLORS.get(roles[0], ROLE_COLORS["pass_through"])
            ),
            size=min(48.0, activity_sizes[region] + (6.0 if is_hub else 0.0)),
            is_hub=is_hub,
            technologies="|".join(technologies),
            asset_count=len(technologies),
            hover=_hover_table(
                (
                    ("Region", region),
                    ("Node type", "integrated hub" if is_hub else "regional node"),
                    ("Technologies", ", ".join(technologies) or "transport junction"),
                    ("Roles", ", ".join(roles)),
                    ("Optimized activity", activity_values[region]),
                    ("Component", metric.get("component_id", "")),
                    ("Community", metric.get("community_id", "")),
                    ("Betweenness", metric.get("betweenness_centrality", 0.0)),
                )
            ),
        )

    transport_path = directory / "network_transport_inventory.csv"
    transport = (
        pd.read_csv(transport_path)
        if transport_path.exists()
        else pd.DataFrame()
    )
    transport_lookup: dict[tuple[str, str], tuple[float, float]] = {}
    if not transport.empty:
        grouped = transport.groupby(["edge_region", "tech"], as_index=False).agg(
            optimized_capacity=("optimized_capacity", "max"),
            output_activity=("output_activity", "max"),
        )
        transport_lookup = {
            (str(row.edge_region), str(row.tech)): (
                float(row.optimized_capacity),
                float(row.output_activity),
            )
            for row in grouped.itertuples(index=False)
        }

    edge_values = {
        (source_node, target_node, key): float(
            data.get("flow_min", data.get("flow", 0.0))
        )
        for source_node, target_node, key, data in graph.edges(keys=True, data=True)
    }
    edge_sizes = _scale_values(edge_values, 0.7, 7.0)
    for source_node, target_node, key, data in graph.edges(keys=True, data=True):
        technology = str(data.get("tech", "unknown"))
        commodity = str(data.get("output_comm", "unknown"))
        category = technology_category(technology)
        pseudo_regions = str(data.get("pseudo_regions", "")).split("|")
        capacities = [
            transport_lookup.get((edge_region, technology), (0.0, 0.0))[0]
            for edge_region in pseudo_regions
        ]
        positive_capacities = [value for value in capacities if value > 0]
        corridor_capacity = min(positive_capacities) if positive_capacities else 0.0
        corridor_flow = float(data.get("flow_min", data.get("flow", 0.0)))
        utilization = corridor_flow / corridor_capacity if corridor_capacity else 0.0
        data.update(
            label=technology,
            color=(
                COMMODITY_COLORS.get(commodity, "#666666")
                if edge_color_by == "commodity"
                else TECHNOLOGY_COLORS[category]
            ),
            size=edge_sizes[(source_node, target_node, key)],
            optimized_capacity=corridor_capacity,
            utilization=utilization,
            hover=_hover_table(
                (
                    ("Technology", technology),
                    ("Commodity", commodity),
                    ("From hub", source_node),
                    ("To hub", target_node),
                    ("Realized flow", corridor_flow),
                    ("Bottleneck capacity", corridor_capacity),
                    ("Utilization", utilization),
                    ("Distance (km)", data.get("distance_km", 0.0)),
                    ("Contracted segments", data.get("segment_count", 1)),
                )
            ),
        )
    graph.graph["name"] = (
        f"Regional infrastructure hubs ({graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} corridors)"
    )
    return graph


def build_opportunity_graphs(
    analysis_dir: str | Path,
    hub_graph: nx.MultiDiGraph,
    *,
    limit: int = 250,
) -> list[nx.MultiDiGraph]:
    """Overlay unused CO2/electricity assets on the deployed hub network."""

    asset_path = Path(analysis_dir) / "network_asset_inventory.csv"
    if not asset_path.exists():
        raise FileNotFoundError(f"Asset inventory not found: {asset_path}")
    assets = pd.read_csv(asset_path)
    unused = assets.loc[assets["status"] == "available_unused"].copy()

    components = list(nx.weakly_connected_components(hub_graph))
    if components:
        largest = max(components, key=len)
        deployed = hub_graph.subgraph(largest).copy()
    else:
        deployed = hub_graph.copy()

    choices: list[nx.MultiDiGraph] = []
    labels = {
        "CO2_CAP": "Untapped CO2 capture potential",
        "ELC_GEN": "Unused electricity potential",
    }
    for technology in ("CO2_CAP", "ELC_GEN"):
        candidates = unused.loc[unused["technology"] == technology].sort_values(
            "available_capacity", ascending=False
        )
        total = len(candidates)
        if technology != "CO2_CAP" and limit > 0:
            candidates = candidates.head(limit)
        if candidates.empty:
            continue
        graph = deployed.copy()
        for row in candidates.itertuples(index=False):
            region = str(row.region)
            disconnected = bool(row.disconnected_from_deployed_transport)
            attributes = {
                "region": region,
                "label": f"{region} · unused {technology}",
                "color": OPPORTUNITY_COLORS[technology],
                "size": 18.0,
                "is_opportunity": True,
                "opportunity_technology": technology,
                "available_capacity": float(row.available_capacity),
                "hover": _hover_table(
                    (
                        ("Region", region),
                        ("Opportunity", labels[technology]),
                        ("Available capacity", row.available_capacity),
                        ("Transport disconnected", disconnected),
                    )
                ),
            }
            if region in graph:
                graph.nodes[region].update(attributes)
            else:
                graph.add_node(region, **attributes)
        qualifier = (
            f"all {len(candidates):,}"
            if len(candidates) == total
            else f"top {len(candidates):,} of {total:,}"
        )
        graph.graph["name"] = (
            f"{labels[technology]} — {qualifier} "
            f"({graph.number_of_nodes()} nodes)"
        )
        choices.append(graph)
    return choices


def filter_graph(
    graph: nx.MultiDiGraph,
    *,
    region: str | None = None,
    commodity: str | None = None,
    all_components: bool = False,
) -> nx.MultiDiGraph:
    """Filter a display graph and optionally retain only its largest component."""

    selected_edges = []
    for source, target, key, data in graph.edges(keys=True, data=True):
        source_data = graph.nodes[source]
        target_data = graph.nodes[target]
        region_match = region is None or region in {
            str(source_data.get("region", source)),
            str(target_data.get("region", target)),
        }
        commodity_match = commodity is None or commodity in {
            str(source_data.get("commodity", "")),
            str(target_data.get("commodity", "")),
            str(data.get("commodity", data.get("output_comm", ""))),
        }
        if region_match and commodity_match:
            selected_edges.append((source, target, key))

    if region is not None or commodity is not None:
        nodes = {
            node
            for source, target, _ in selected_edges
            for node in (source, target)
        }
        display = graph.subgraph(nodes).copy()
        unwanted = [edge for edge in display.edges(keys=True) if edge not in selected_edges]
        display.remove_edges_from(unwanted)
        display.remove_nodes_from(list(nx.isolates(display)))
    else:
        display = graph.copy()

    if display.number_of_nodes() == 0:
        raise ValueError("The selected region/commodity filters produced an empty graph.")
    if not all_components:
        largest = max(nx.weakly_connected_components(display), key=len)
        display = display.subgraph(largest).copy()
    return display


def build_commodity_focus_graphs(
    graph: nx.MultiDiGraph,
    *,
    all_components: bool = False,
) -> list[nx.MultiDiGraph]:
    """Build complete upstream/downstream commodity pathway views."""

    present = {
        str(data.get("commodity", ""))
        for _, data in graph.nodes(data=True)
    }
    preferred_order = ("gsl", "h2", "co2", "ch3oh", "elc", "co2_stored", "d_gsl")
    commodities = [value for value in preferred_order if value in present]
    commodities.extend(sorted(present - set(commodities) - {"", "ethos"}))
    focused: list[nx.MultiDiGraph] = []
    for commodity in commodities:
        targets = {
            node
            for node, data in graph.nodes(data=True)
            if str(data.get("commodity", "")) == commodity
        }
        if not targets:
            continue
        nodes = set(targets)
        for node in targets:
            nodes.update(nx.ancestors(graph, node))
            nodes.update(nx.descendants(graph, node))
        subset = graph.subgraph(nodes).copy()
        if not all_components and subset.number_of_nodes():
            largest = max(nx.weakly_connected_components(subset), key=len)
            subset = subset.subgraph(largest).copy()
        scope = "all components" if all_components else "largest connected"
        subset.graph["name"] = (
            f"{commodity} pathway — {scope} "
            f"({subset.number_of_nodes()} nodes, {subset.number_of_edges()} edges)"
        )
        focused.append(subset)

    overview = filter_graph(graph, all_components=all_components)
    scope = "all components" if all_components else "largest connected"
    overview.graph["name"] = (
        f"All commodities — {scope} ({overview.number_of_nodes()} nodes, "
        f"{overview.number_of_edges()} edges)"
    )
    focused.append(overview)
    return focused


def render_gravis_html(
    graph: nx.MultiDiGraph | Sequence[nx.MultiDiGraph],
    output_path: str | Path,
    *,
    show_edge_labels: bool = False,
) -> Path:
    """Render a self-contained interactive HTML graph using TEMOA's backend."""

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
            import gravis as gv
    except ImportError as exc:
        raise RuntimeError(
            "Gravis is required for HTML networks. Run: python -m pip install -e ."
        ) from exc

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = gv.d3(
        graph,
        show_menu=True,
        show_details=True,
        show_node_label=True,
        node_label_data_source="label",
        show_edge_label=show_edge_labels,
        edge_label_data_source="label",
        edge_curvature=0.12,
        graph_height=900,
        details_height=160,
        zoom_factor=0.85,
        node_drag_fix=True,
        node_hover_neighborhood=True,
        node_label_size_factor=0.55,
        edge_label_size_factor=0.45,
        layout_algorithm_active=True,
        use_collision_force=True,
        collision_force_radius=24.0,
        collision_force_strength=0.9,
        many_body_force_strength=-150.0,
        links_force_distance=85.0,
        large_graph_threshold=500,
    )
    # Avoid Gravis 0.1.0's Windows export_html encoding bug by writing its
    # generated document explicitly as UTF-8.
    destination.write_text(figure.to_html(), encoding="utf-8")
    return destination


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render optimized CANOE networks as interactive TEMOA-style HTML."
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        help="Network analysis directory or network_manifest.json path",
    )
    parser.add_argument(
        "--view",
        choices=("commodity", "corridor", "hub", "opportunity"),
        default="commodity",
    )
    parser.add_argument("--out", type=Path, help="HTML output path")
    parser.add_argument("--region", help="Keep edges touching this model region")
    parser.add_argument("--commodity", help="Keep edges touching this commodity")
    parser.add_argument(
        "--edge-color-by",
        choices=("technology", "commodity"),
        default="technology",
    )
    parser.add_argument(
        "--size-by",
        choices=("betweenness_centrality", "pagerank", "degree_centrality"),
        default="betweenness_centrality",
        help="Corridor-view node sizing metric",
    )
    parser.add_argument("--show-edge-labels", action="store_true")
    parser.add_argument("--all-components", action="store_true")
    parser.add_argument(
        "--opportunity-limit",
        type=int,
        default=250,
        help="Maximum unused electricity sites in the opportunity view (0 keeps all)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    project_root = find_project_root()
    manifest_path = (
        resolve_manifest_path(args.analysis)
        if args.analysis is not None
        else select_analysis_manifest(project_root / "analysis_outputs")
    )
    manifest = load_manifest(manifest_path)
    analysis_dir = manifest_path.parent

    render_data: nx.MultiDiGraph | list[nx.MultiDiGraph]
    if args.view == "commodity":
        db_path = Path(manifest["source_database"])
        edge_path = Path(manifest["source_graph_edges"])
        inputs = load_network_inputs(db_path, edge_path)
        contracted_path = analysis_dir / "network_contracted.graphml"
        contracted_transport = (
            nx.MultiDiGraph(nx.read_graphml(contracted_path))
            if contracted_path.exists()
            else None
        )
        scenario, period = resolve_snapshot(
            inputs.flow_out,
            scenario=manifest.get("scenario"),
            period=manifest.get("period"),
            min_flow=float(manifest.get("minimum_deployed_flow", MIN_DEPLOYED_FLOW)),
        )
        graph = build_commodity_graph(
            inputs,
            contracted_transport=contracted_transport,
            commodity_flags=_load_commodity_flags(db_path),
            scenario=scenario,
            period=period,
            min_flow=float(manifest.get("minimum_deployed_flow", MIN_DEPLOYED_FLOW)),
            edge_color_by=args.edge_color_by,
        )
    elif args.view == "corridor":
        graph = build_corridor_graph(
            analysis_dir,
            size_by=args.size_by,
            edge_color_by=args.edge_color_by,
        )
    elif args.view == "hub":
        graph = build_hub_graph(
            analysis_dir,
            edge_color_by=args.edge_color_by,
        )
    else:
        hub_graph = build_hub_graph(
            analysis_dir,
            edge_color_by=args.edge_color_by,
        )
        render_data = build_opportunity_graphs(
            analysis_dir,
            hub_graph,
            limit=args.opportunity_limit,
        )
        if not render_data:
            raise ValueError("No unused asset opportunities were found.")
        graph = render_data[0]

    if args.view == "commodity" and args.region is None and args.commodity is None:
        render_data = build_commodity_focus_graphs(
            graph,
            all_components=args.all_components,
        )
        graph = render_data[0]
    elif args.view == "opportunity":
        pass
    else:
        graph = filter_graph(
            graph,
            region=args.region,
            commodity=args.commodity,
            all_components=args.all_components,
        )
        render_data = graph
    default_name = f"network_{args.view}_interactive.html"
    output_path = args.out or analysis_dir / default_name

    print("\nInteractive network visualization:")
    print(f"  Analysis:       {analysis_dir}")
    print(f"  View:           {args.view}")
    print(f"  Displayed:      {graph.number_of_nodes():,} nodes, "
          f"{graph.number_of_edges():,} edges")
    print(f"  Edge colour:    {args.edge_color_by}")
    print(f"  Region filter:  {args.region or 'none'}")
    print(f"  Commodity:      {args.commodity or 'none'}")
    if isinstance(render_data, list):
        print(f"  Graph choices:  {len(render_data):,} selectable views")
    print(f"  Output:         {output_path}")

    written = render_gravis_html(
        render_data,
        output_path,
        show_edge_labels=args.show_edge_labels,
    )
    print(f"Saved interactive network: {written}")


if __name__ == "__main__":
    main()
