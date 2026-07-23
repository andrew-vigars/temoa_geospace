"""
map_roads.py

Stage 4 of the Geospatial-CANOE workflow.

This script takes the profile-specific filtered road-network GeoPackage and profile-specific Stage 2 graph products, then overlays the configured road layer onto each matching graph. Weak and/or strong connectivity products are generated according to the shared TOML build profile.

Inputs:
    data_files/processed/nrn/{study_area}_filtered_road_networks.gpkg
    data_files/processed/basemaps/{study_area}_boundary_wgs84.gpkg
    data_files/processed/graph/
        study_area_boundary_*_graph_nodes.gpkg
        study_area_boundary_*_graph_edges.csv

Outputs:
    data_files/processed/road_connectivity/
        *_weak_region_road_presence.csv
        *_weak_road_edge_connections.csv
        *_weak_road_edges.gpkg
        *_strong_region_road_presence.csv
        *_strong_road_edge_connections.csv
        *_strong_road_edges.gpkg
        *_road_region_overlay.gpkg
        {study_area}_road_connectivity_summary.csv
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import ListedColormap
from shapely.geometry import LineString

from project_config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_FILES = PROJECT_ROOT / "data_files"

RAW_BASEMAPS = DATA_FILES / "raw" / "basemaps"
PROCESSED_NRN = DATA_FILES / "processed" / "nrn"
PROCESSED_GRAPH = DATA_FILES / "processed" / "graph"

PROCESSED_ROAD_CONNECTIVITY = (
    DATA_FILES
    / "processed"
    / "road_connectivity"
)


# =============================================================================
# Plotting constants
# =============================================================================

road_presence_colors = ListedColormap(
    [
        "#7FCDBB",
        "#2C7FB8",
    ]
)


# =============================================================================
# Load helpers
# =============================================================================

def find_graph_node_files(
    config: GeospatialBuildConfig,
) -> list[Path]:
    """Find graph-node files belonging to the selected build profile."""

    pattern = (
        f"{config.study_area.label}_basemap_*_"
        f"{config.basemaps.keep_method}_graph_nodes.gpkg"
    )

    graph_node_files = sorted(
        PROCESSED_GRAPH.glob(pattern)
    )

    if not graph_node_files:
        raise FileNotFoundError(
            "No graph node files were found for build profile "
            f"{config.study_area.label!r} in {PROCESSED_GRAPH}. "
            f"Expected pattern: {pattern}"
        )

    print(
        f"Found {len(graph_node_files):,} graph node files for "
        f"profile {config.study_area.label!r}:"
    )

    for graph_node_file in graph_node_files:
        print(f"  - {graph_node_file.name}")

    return graph_node_files


def infer_graph_edge_path(graph_node_path: Path) -> Path:
    """Infer the graph edge CSV path paired with a graph node GeoPackage.

    Replaces the ``_graph_nodes.gpkg`` suffix with ``_graph_edges.csv`` and
    returns the corresponding path in the processed graph directory.
    """
    return (
        PROCESSED_GRAPH
        / graph_node_path.name.replace(
            "_graph_nodes.gpkg",
            "_graph_edges.csv",
        )
    )


def load_static_datasets(
    config: GeospatialBuildConfig,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load the configured road layer and processed study-area boundary."""

    road_network_path = (
        PROCESSED_NRN
        / f"{config.study_area.label}_filtered_road_networks.gpkg"
    )

    boundary_path = (
        DATA_FILES
        / "processed"
        / "basemaps"
        / f"{config.study_area.label}_boundary_wgs84.gpkg"
    )

    if not road_network_path.exists():
        raise FileNotFoundError(
            f"Configured road network not found: {road_network_path}"
        )

    if not boundary_path.exists():
        raise FileNotFoundError(
            f"Configured study-area boundary not found: {boundary_path}"
        )

    print(f"Using road network: {road_network_path.name}")
    print(f"Using road layer: {config.road_connectivity.road_layer}")
    print(f"Using study-area boundary: {boundary_path.name}")

    roads = gpd.read_file(
        road_network_path,
        layer=config.road_connectivity.road_layer,
    )

    study_area_boundary = gpd.read_file(
        boundary_path,
        layer="study_area_boundary",
    )

    if roads.crs is None:
        raise ValueError("Road network CRS is undefined.")

    if study_area_boundary.crs is None:
        raise ValueError("Study-area boundary CRS is undefined.")

    required_road_columns = [
        "ROADCLASS",
        "province",
        "study_area",
        "province_codes",
        "geometry",
    ]

    missing_road_columns = [
        column
        for column in required_road_columns
        if column not in roads.columns
    ]

    if missing_road_columns:
        raise ValueError(
            "Road network is missing required columns: "
            f"{missing_road_columns}"
        )

    study_areas = set(roads["study_area"].astype(str).unique())

    if study_areas != {config.study_area.label}:
        raise ValueError(
            f"Road file contains study_area values {sorted(study_areas)}, "
            f"expected only {config.study_area.label!r}."
        )

    roads_overlay = (
        roads[
            [
                "ROADCLASS",
                "province",
                "study_area",
                "province_codes",
                "geometry",
            ]
        ]
        .copy()
        .reset_index(drop=True)
    )

    roads_overlay.insert(
        0,
        "road_id",
        range(len(roads_overlay)),
    )

    print(f"Loaded road segments: {len(roads):,}")
    print(f"Prepared road overlay geometries: {len(roads_overlay):,}")
    print(f"Road CRS: {roads_overlay.crs}")

    return roads_overlay, study_area_boundary



# =============================================================================
# Validation
# =============================================================================

def validate_graph_inputs(
    regions: gpd.GeoDataFrame,
    graph_edges: pd.DataFrame,
    roads: gpd.GeoDataFrame,
) -> None:
    """Validate graph-node, graph-edge, and road-overlay inputs.

    Checks that all required columns are present, graph nodes and road overlays
    have defined and matching coordinate reference systems, graph node region IDs
    are unique, and every graph edge references known ``region_from`` and
    ``region_to`` IDs.
    """

    required_region_columns = [
        "region",
        "site_id",
        "lon",
        "lat",
        "study_area",
        "grid_type",
        "resolution",
        "resolution_unit",
        "keep_method",
        "geometry",
    ]

    required_graph_edge_columns = [
        "edge_region",
        "region_from",
        "region_to",
        "direction",
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
        "distance_km",
    ]

    required_road_columns = [
        "road_id",
        "ROADCLASS",
        "geometry",
    ]

    missing_region_columns = [
        column for column in required_region_columns
        if column not in regions.columns
    ]

    missing_graph_edge_columns = [
        column for column in required_graph_edge_columns
        if column not in graph_edges.columns
    ]

    missing_road_columns = [
        column for column in required_road_columns
        if column not in roads.columns
    ]

    if missing_region_columns:
        raise ValueError(
            f"Graph nodes are missing required columns: {missing_region_columns}"
        )

    if missing_graph_edge_columns:
        raise ValueError(
            f"Graph edges are missing required columns: {missing_graph_edge_columns}"
        )

    if missing_road_columns:
        raise ValueError(
            f"Road overlay is missing required columns: {missing_road_columns}"
        )

    if regions.crs is None:
        raise ValueError("Graph node CRS is undefined.")

    if roads.crs is None:
        raise ValueError("Road overlay CRS is undefined.")

    if regions.crs != roads.crs:
        raise ValueError(f"CRS mismatch: regions={regions.crs}, roads={roads.crs}")

    if not regions["region"].is_unique:
        raise ValueError("Graph node region IDs are not unique.")

    region_ids = set(regions["region"])

    unknown_region_from = sorted(set(graph_edges["region_from"]) - region_ids)
    unknown_region_to = sorted(set(graph_edges["region_to"]) - region_ids)

    if unknown_region_from:
        raise ValueError(
            f"Graph edges contain unknown region_from IDs: {unknown_region_from[:10]}"
        )

    if unknown_region_to:
        raise ValueError(
            f"Graph edges contain unknown region_to IDs: {unknown_region_to[:10]}"
        )


# =============================================================================
# Road-region overlay
# =============================================================================

def build_road_region_overlay(
    roads_overlay: gpd.GeoDataFrame,
    regions: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Overlay road segments onto graph regions using spatial intersection."""

    graph_nodes_overlay = regions[
        [
            "region",
            "geometry",
        ]
    ].copy()

    road_region_overlay = gpd.sjoin(
        roads_overlay,
        graph_nodes_overlay,
        how="inner",
        predicate="intersects",
    )

    road_region_overlay = road_region_overlay.drop(
        columns=["index_right"],
        errors="ignore",
    )

    return road_region_overlay


def build_region_road_presence(
    regions: gpd.GeoDataFrame,
    road_region_overlay: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Build a region-level table indicating whether each graph node has roads.

    Counts unique road segments and total road-region intersections for each
    graph region, fills missing counts with zero for regions without roads, and
    adds a boolean ``has_road`` flag used by weak road-connectivity logic.
    """

    road_counts_by_region = (
        road_region_overlay
        .groupby("region")
        .agg(
            road_segment_count=("road_id", "nunique"),
            road_region_intersection_count=("road_id", "size"),
        )
        .reset_index()
    )

    region_road_presence = (
        regions[
            [
                "region",
                "study_area",
                "grid_type",
                "resolution",
                "resolution_unit",
                "keep_method",
            ]
        ]
        .merge(
            road_counts_by_region,
            on="region",
            how="left",
        )
    )

    count_cols = [
        "road_segment_count",
        "road_region_intersection_count",
    ]

    region_road_presence[count_cols] = (
        region_road_presence[count_cols]
        .fillna(0)
        .astype(int)
    )

    region_road_presence["has_road"] = (
        region_road_presence["road_segment_count"] > 0
    )

    return region_road_presence


# =============================================================================
# Connectivity builders
# =============================================================================

def build_weak_road_connections(
    graph_edges: pd.DataFrame,
    region_road_presence: pd.DataFrame,
) -> pd.DataFrame:
    """Flag graph edges as weakly road-enabled using endpoint road presence.

    A graph edge is considered weakly road-enabled when both adjacent endpoint
    regions contain at least one road segment. The returned table preserves the
    graph-edge records and adds endpoint road-presence flags, a combined
    ``has_road_connection`` flag, connection-method metadata, and ``region_pair``.
    """

    road_presence_lookup = (
        region_road_presence
        .set_index("region")["has_road"]
        .to_dict()
    )

    weak_connections = graph_edges.copy()

    weak_connections["has_road_from"] = (
        weak_connections["region_from"]
        .map(road_presence_lookup)
        .fillna(False)
    )

    weak_connections["has_road_to"] = (
        weak_connections["region_to"]
        .map(road_presence_lookup)
        .fillna(False)
    )

    weak_connections["has_road_connection"] = (
        weak_connections["has_road_from"]
        & weak_connections["has_road_to"]
    )

    weak_connections["connection_method"] = "weak_node_presence_adjacency"
    weak_connections["region_pair"] = weak_connections["edge_region"]

    return weak_connections


def build_strong_road_connections(
    graph_edges: pd.DataFrame,
    road_region_overlay: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Flag graph edges as strongly road-enabled using shared road segments.

    A graph edge is considered strongly road-enabled when its adjacent endpoint
    regions intersect at least one common ``road_id`` in the road-region overlay.
    The returned table preserves the graph-edge records and adds the shared-road
    count, a combined ``has_road_connection`` flag, connection-method metadata,
    and ``region_pair``.
    """

    road_regions = (
        road_region_overlay[
            [
                "road_id",
                "region",
            ]
        ]
        .drop_duplicates()
    )

    region_to_roads = (
        road_regions
        .groupby("region")["road_id"]
        .apply(set)
        .to_dict()
    )

    strong_connections = graph_edges.copy()

    shared_road_counts = []

    for row in strong_connections.itertuples(index=False):
        from_roads = region_to_roads.get(row.region_from, set())
        to_roads = region_to_roads.get(row.region_to, set())

        shared_road_counts.append(
            len(from_roads.intersection(to_roads))
        )

    strong_connections["shared_road_segment_count"] = shared_road_counts

    strong_connections["has_road_connection"] = (
        strong_connections["shared_road_segment_count"] > 0
    )

    strong_connections["connection_method"] = "strong_shared_road_segment"
    strong_connections["region_pair"] = strong_connections["edge_region"]

    return strong_connections


def build_road_edge_geometries(
    road_edge_connections: pd.DataFrame,
    regions_crs,
) -> gpd.GeoDataFrame:
    """Build road-enabled edge geometries in the graph's native CRS.

    Stage 2 graph edges store both native graph coordinates
    (``x_from``, ``y_from``, ``x_to``, ``y_to``) and WGS84 display
    coordinates (``lon_from``, ``lat_from``, ``lon_to``, ``lat_to``).

    Geometry must be constructed from the native coordinates because the
    output GeoDataFrame is assigned the graph-node CRS. Using longitude and
    latitude values with a projected CRS places the lines near the projected
    origin and makes them effectively invisible on kilometre-grid plots.
    """

    required_native_columns = [
        "x_from",
        "y_from",
        "x_to",
        "y_to",
    ]

    missing_columns = [
        column
        for column in required_native_columns
        if column not in road_edge_connections.columns
    ]

    if missing_columns:
        raise ValueError(
            "Road-edge connections are missing native coordinate columns: "
            f"{missing_columns}"
        )

    road_edges = (
        road_edge_connections
        .loc[road_edge_connections["has_road_connection"]]
        .copy()
    )

    road_edges["geometry"] = road_edges.apply(
        lambda row: LineString(
            [
                (
                    float(row["x_from"]),
                    float(row["y_from"]),
                ),
                (
                    float(row["x_to"]),
                    float(row["y_to"]),
                ),
            ]
        ),
        axis=1,
    )

    return gpd.GeoDataFrame(
        road_edges,
        geometry="geometry",
        crs=regions_crs,
    )


# =============================================================================
# Export
# =============================================================================

def export_road_connectivity_outputs(
    output_dir: Path,
    output_stem: str,
    connection_method: str,
    region_road_presence: pd.DataFrame,
    road_edge_connections: pd.DataFrame,
    road_edge_gdf: gpd.GeoDataFrame,
    road_region_overlay: gpd.GeoDataFrame | None = None,
) -> dict:
    """Export road-connectivity tables and geometries for one graph/method pair.

    Writes region-level road presence, graph-edge road-connection flags, and
    road-enabled graph-edge geometries to the processed road-connectivity folder.
    Optionally exports the road-region overlay, then returns output filenames for
    inclusion in the road-connectivity summary table.
    """

    region_road_presence_path = (
        output_dir
        / f"{output_stem}_{connection_method}_region_road_presence.csv"
    )

    road_edge_connections_path = (
        output_dir
        / f"{output_stem}_{connection_method}_road_edge_connections.csv"
    )

    road_edge_gpkg_path = (
        output_dir
        / f"{output_stem}_{connection_method}_road_edges.gpkg"
    )

    region_road_presence.to_csv(
        region_road_presence_path,
        index=False,
    )

    road_edge_connections.to_csv(
        road_edge_connections_path,
        index=False,
    )

    if road_edge_gpkg_path.exists():
        road_edge_gpkg_path.unlink()

    road_edge_gdf.to_file(
        road_edge_gpkg_path,
        driver="GPKG",
    )

    outputs = {
        f"{connection_method}_region_road_presence_file": (
            region_road_presence_path.name
        ),
        f"{connection_method}_road_edge_connections_file": (
            road_edge_connections_path.name
        ),
        f"{connection_method}_road_edges_file": (
            road_edge_gpkg_path.name
        ),
    }

    if road_region_overlay is not None:
        road_overlay_path = (
            output_dir
            / f"{output_stem}_road_region_overlay.gpkg"
        )

        if road_overlay_path.exists():
            road_overlay_path.unlink()

        road_region_overlay.to_file(
            road_overlay_path,
            driver="GPKG",
        )

        outputs["road_region_overlay_file"] = road_overlay_path.name

    print(f"Exported: {region_road_presence_path.name}")
    print(f"Exported: {road_edge_connections_path.name}")
    print(f"Exported: {road_edge_gpkg_path.name}")

    return outputs


# =============================================================================
# Optional plotting
# =============================================================================

def plot_region_road_presence(
    regions: gpd.GeoDataFrame,
    region_road_presence: pd.DataFrame,
    roads_overlay: gpd.GeoDataFrame,
    study_area_boundary: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Export a diagnostic map of graph regions containing road segments."""

    regions_with_road_presence = regions.merge(
        region_road_presence,
        on=[
            "region",
            "study_area",
            "grid_type",
            "resolution",
            "resolution_unit",
            "keep_method",
        ],
        how="left",
    )

    regions_with_road_presence["has_road"] = (
        regions_with_road_presence["has_road"]
        .fillna(False)
        .astype(bool)
    )

    resolution = float(regions_with_road_presence["resolution"].iloc[0])
    resolution_unit = str(
        regions_with_road_presence["resolution_unit"].iloc[0]
    )
    grid_type = str(regions_with_road_presence["grid_type"].iloc[0])
    keep_method = str(regions_with_road_presence["keep_method"].iloc[0])
    display_unit = "°" if resolution_unit == "degree" else f" {resolution_unit}"

    fig, ax = plt.subplots(
        figsize=(12, 12),
        dpi=300,
    )

    regions_with_road_presence.plot(
        ax=ax,
        column="has_road",
        cmap=road_presence_colors,
        legend=True,
        linewidth=0.12,
        edgecolor="#4D4D4D",
        zorder=1,
    )

    roads_overlay.plot(
        ax=ax,
        color="black",
        linewidth=0.10,
        alpha=0.25,
        zorder=2,
    )

    study_area_boundary.boundary.plot(
        ax=ax,
        color="black",
        linewidth=0.5,
        zorder=3,
    )

    ax.set_title(
        f"Road Presence by Graph Region "
        f"({resolution:g}{display_unit} {grid_type} {keep_method})",
        fontsize=16,
    )

    ax.set_axis_off()
    plt.tight_layout()

    plt.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)


def plot_road_edge_connectivity(
    regions_with_road_presence: gpd.GeoDataFrame,
    roads_overlay: gpd.GeoDataFrame,
    road_edge_gdf: gpd.GeoDataFrame,
    study_area_boundary: gpd.GeoDataFrame,
    connection_label: str,
    output_path: Path,
) -> None:
    """Export a diagnostic PNG showing road-enabled graph edges.

    Plots graph regions by ``has_road`` status, overlays the filtered road
    network and Canada basemap boundary for context, and draws enabled graph-edge
    geometries for the selected weak or strong road-connectivity method.
    """

    resolution = float(regions_with_road_presence["resolution"].iloc[0])
    resolution_unit = str(
        regions_with_road_presence["resolution_unit"].iloc[0]
    )
    grid_type = str(regions_with_road_presence["grid_type"].iloc[0])
    keep_method = str(regions_with_road_presence["keep_method"].iloc[0])
    display_unit = "°" if resolution_unit == "degree" else f" {resolution_unit}"

    fig, ax = plt.subplots(
        figsize=(12, 12),
        dpi=300,
    )

    regions_with_road_presence.plot(
        ax=ax,
        column="has_road",
        cmap=road_presence_colors,
        legend=True,
        linewidth=0.12,
        edgecolor="#4D4D4D",
        zorder=1,
    )

    roads_overlay.plot(
        ax=ax,
        color="black",
        linewidth=0.10,
        alpha=0.25,
        zorder=2,
    )

    if not road_edge_gdf.empty:
        road_edge_gdf.plot(
            ax=ax,
            color="#F03B20",
            linewidth=0.8,
            alpha=0.8,
            zorder=3,
        )

    study_area_boundary.boundary.plot(
        ax=ax,
        color="black",
        linewidth=0.5,
        zorder=4,
    )

    ax.set_title(
        f"{connection_label} Road-Enabled Graph Edge Connectivity "
        f"({resolution:g}{display_unit} {grid_type} {keep_method})",
        fontsize=16,
    )

    ax.set_axis_off()
    plt.tight_layout()

    plt.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)


# =============================================================================
# Main graph workflow
# =============================================================================

def build_road_connectivity_for_graph(
    graph_node_path: Path,
    roads_overlay: gpd.GeoDataFrame,
    study_area_boundary: gpd.GeoDataFrame,
    config: GeospatialBuildConfig,
) -> dict:
    """Build weak and strong road-connectivity outputs for one graph.

    Loads the graph nodes and paired graph-edge table, aligns static road and
    basemap layers to the graph CRS, validates graph and road inputs, overlays
    roads onto graph regions, derives region-level road presence, builds weak
    and strong road-enabled graph edges, exports tabular and geospatial outputs,
    optionally writes diagnostic plots, and returns a summary row for the graph.
    """

    graph_edge_path = infer_graph_edge_path(graph_node_path)

    if not graph_edge_path.exists():
        raise FileNotFoundError(
            f"Missing graph edge file: {graph_edge_path.name}"
        )

    print("\n" + "=" * 80)
    print(f"Processing graph: {graph_node_path.name}")
    print("=" * 80)

    regions = gpd.read_file(graph_node_path)
    graph_edges = pd.read_csv(graph_edge_path)

    if roads_overlay.crs != regions.crs:
        roads_for_overlay = roads_overlay.to_crs(regions.crs)
    else:
        roads_for_overlay = roads_overlay.copy()

    if study_area_boundary.crs != regions.crs:
        boundary_for_plot = study_area_boundary.to_crs(regions.crs)
    else:
        boundary_for_plot = study_area_boundary.copy()

    validate_graph_inputs(
        regions=regions,
        graph_edges=graph_edges,
        roads=roads_for_overlay,
    )

    study_area = str(regions["study_area"].iloc[0])
    grid_type = str(regions["grid_type"].iloc[0])
    resolution = float(regions["resolution"].iloc[0])
    resolution_unit = str(regions["resolution_unit"].iloc[0])
    keep_method = str(regions["keep_method"].iloc[0])

    if study_area != config.study_area.label:
        raise ValueError(
            f"{graph_node_path.name} belongs to study area "
            f"{study_area!r}, expected {config.study_area.label!r}."
        )

    graph_stem = graph_node_path.name.replace(
        "_graph_nodes.gpkg",
        "",
    )

    output_stem = f"{graph_stem}_road_connectivity"

    road_region_overlay = build_road_region_overlay(
        roads_overlay=roads_for_overlay,
        regions=regions,
    )

    region_road_presence = build_region_road_presence(
        regions=regions,
        road_region_overlay=road_region_overlay,
    )

    regions_with_road_presence = regions.merge(
        region_road_presence,
        on=[
            "region",
            "study_area",
            "grid_type",
            "resolution",
            "resolution_unit",
            "keep_method",
        ],
        how="left",
    )

    regions_with_road_presence["has_road"] = (
        regions_with_road_presence["has_road"]
        .fillna(False)
        .astype(bool)
    )

    method_results: dict[str, dict[str, object]] = {}
    exported_files: dict[str, str] = {}

    for method in config.road_connectivity.methods:
        if method == "weak":
            connections = build_weak_road_connections(
                graph_edges=graph_edges,
                region_road_presence=region_road_presence,
            )
        elif method == "strong":
            connections = build_strong_road_connections(
                graph_edges=graph_edges,
                road_region_overlay=road_region_overlay,
            )
        else:
            raise ValueError(
                f"Unsupported road-connectivity method: {method!r}"
            )

        edge_gdf = build_road_edge_geometries(
            road_edge_connections=connections,
            regions_crs=regions.crs,
        )

        method_outputs = export_road_connectivity_outputs(
            output_dir=PROCESSED_ROAD_CONNECTIVITY,
            output_stem=output_stem,
            connection_method=method,
            region_road_presence=region_road_presence,
            road_edge_connections=connections,
            road_edge_gdf=edge_gdf,
            road_region_overlay=(
                road_region_overlay
                if method == config.road_connectivity.methods[0]
                else None
            ),
        )

        enabled_edges = int(
            connections["has_road_connection"].sum()
        )
        edge_share = (
            enabled_edges / len(connections)
            if len(connections) > 0
            else 0.0
        )

        method_results[method] = {
            "connections": connections,
            "edge_gdf": edge_gdf,
            "enabled_edges": enabled_edges,
            "edge_share": edge_share,
        }
        exported_files.update(method_outputs)

    if config.road_connectivity.plot_outputs:
        plot_dir = PROCESSED_ROAD_CONNECTIVITY / "plots"
        plot_dir.mkdir(parents=True, exist_ok=True)

        road_presence_plot_path = (
            plot_dir
            / f"{graph_stem}_road_presence.png"
        )


        plot_region_road_presence(
            regions=regions,
            region_road_presence=region_road_presence,
            roads_overlay=roads_for_overlay,
            study_area_boundary=boundary_for_plot,
            output_path=road_presence_plot_path,
        )

        connectivity_plot_files: dict[str, str] = {}

        for method, result in method_results.items():
            connectivity_plot_path = (
                plot_dir
                / f"{graph_stem}_{method}_connectivity.png"
            )

            plot_road_edge_connectivity(
                regions_with_road_presence=regions_with_road_presence,
                roads_overlay=roads_for_overlay,
                road_edge_gdf=result["edge_gdf"],
                study_area_boundary=boundary_for_plot,
                connection_label=method.title(),
                output_path=connectivity_plot_path,
            )

            connectivity_plot_files[
                f"{method}_connectivity_plot_file"
            ] = connectivity_plot_path.name

        print(f"Exported plot: {road_presence_plot_path.name}")
        for plot_file in connectivity_plot_files.values():
            print(f"Exported plot: {plot_file}")

    regions_with_roads = int(
        region_road_presence["has_road"].sum()
    )

    print(
        f"Regions with roads: "
        f"{regions_with_roads:,} / {len(region_road_presence):,}"
    )

    for method, result in method_results.items():
        print(
            f"{method.title()} road-enabled edges: "
            f"{result['enabled_edges']:,} / "
            f"{len(result['connections']):,}"
        )

    summary = {
        "study_area": study_area,
        "graph_file": graph_node_path.name,
        "edge_file": graph_edge_path.name,
        "grid_type": grid_type,
        "resolution": resolution,
        "resolution_unit": resolution_unit,
        "keep_method": keep_method,
        "road_layer": config.road_connectivity.road_layer,
        "regions": len(regions),
        "graph_edges": len(graph_edges),
        "road_overlay_rows": len(road_region_overlay),
        "unique_roads_matched": road_region_overlay["road_id"].nunique(),
        "regions_with_roads": regions_with_roads,
    }

    for method, result in method_results.items():
        summary[f"{method}_road_edges"] = result["enabled_edges"]
        summary[f"{method}_road_edge_share"] = result["edge_share"]

    summary.update(exported_files)

    if config.road_connectivity.plot_outputs:
        summary["road_presence_plot_file"] = (
            road_presence_plot_path.name
        )
        summary.update(connectivity_plot_files)

    return summary


def parse_args() -> argparse.Namespace:
    """Parse the Stage 4 build-profile path."""

    parser = argparse.ArgumentParser(
        description=(
            "Map configured processed roads onto configured region graphs."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help=(
            "Path to a geospatial preprocessing TOML build profile."
        ),
    )
    return parser.parse_args()


def run_road_connectivity_build(
    config: GeospatialBuildConfig,
) -> pd.DataFrame:
    """Run Stage 4 using an already loaded build profile."""

    PROCESSED_ROAD_CONNECTIVITY.mkdir(
        parents=True,
        exist_ok=True,
    )

    graph_node_files = find_graph_node_files(config)

    roads_overlay, study_area_boundary = load_static_datasets(
        config
    )

    summary_rows: list[dict] = []

    for graph_node_path in graph_node_files:
        summary = build_road_connectivity_for_graph(
            graph_node_path=graph_node_path,
            roads_overlay=roads_overlay,
            study_area_boundary=study_area_boundary,
            config=config,
        )
        summary_rows.append(summary)

    road_connectivity_summary = pd.DataFrame(summary_rows)

    road_connectivity_summary = (
        road_connectivity_summary
        .sort_values(
            by=[
                "grid_type",
                "resolution",
                "keep_method",
            ]
        )
        .reset_index(drop=True)
    )

    summary_path = (
        PROCESSED_ROAD_CONNECTIVITY
        / f"{config.study_area.label}_road_connectivity_summary.csv"
    )

    road_connectivity_summary.to_csv(
        summary_path,
        index=False,
    )

    print("\nRoad connectivity processing complete.")
    print(f"Exported summary: {summary_path.name}")

    return road_connectivity_summary


def main() -> None:
    """Load a TOML profile and run Stage 4."""

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_road_connectivity_build(config)


if __name__ == "__main__":
    main()
