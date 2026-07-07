"""
map_roads.py

Stage 4 of the Geospatial-CANOE workflow.

This script takes the filtered national road network (NRN) GeoPackage file and the original Canada basemap shapefile,
and overlays the road network onto the graph regions for each resolution and keep method combination.
A weak and strong road connectivity analysis is performed for each graph
whereby weak connectivity is defined as both regions having at least one road segment present,
and strong connectivity is defined as regions having a direct road connection given they exist in both adjacent regions.

Inputs:
    data_files/processed/nrn/CANADA_filtered_road_networks.gpkg
    data_files/raw/basemaps/lpr_000b21a_e.shp
    data_files/processed/graph/
        canada_basemap_*_graph_nodes.gpkg
        canada_basemap_*_graph_edges.csv

Outputs:
    data_files/processed/road_connectivity/
        *_weak_region_road_presence.csv
        *_weak_road_edge_connections.csv
        *_weak_road_edges.gpkg
        *_strong_region_road_presence.csv
        *_strong_road_edge_connections.csv
        *_strong_road_edges.gpkg
        *_road_region_overlay.gpkg
        road_connectivity_summary.csv
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import ListedColormap
from shapely.geometry import LineString


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
# Settings
# =============================================================================

ROAD_NETWORK_PATH = (
    PROCESSED_NRN
    / "CANADA_filtered_road_networks.gpkg"
)

ORIGINAL_BASEMAP_PATH = (
    RAW_BASEMAPS
    / "lpr_000b21a_e.shp"
)

ROAD_LAYER = "freight_access"

PLOT_OUTPUTS = True

road_presence_colors = ListedColormap(
    [
        "#7FCDBB",
        "#2C7FB8",
    ]
)


# =============================================================================
# Load helpers
# =============================================================================

def find_graph_node_files() -> list[Path]:
    """Find and report processed graph node files for road-connectivity mapping.

    Searches the processed graph folder for ``canada_basemap_*_graph_nodes.gpkg``
    files, raises ``FileNotFoundError`` if none are found, prints the discovered
    files, and returns them in sorted order.
    """
    graph_node_files = sorted(
        PROCESSED_GRAPH.glob("canada_basemap_*_graph_nodes.gpkg")
    )

    if not graph_node_files:
        raise FileNotFoundError(
            f"No graph node files found in {PROCESSED_GRAPH}"
        )

    print(f"Found {len(graph_node_files):,} graph node files:")

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


def load_static_datasets() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load and validate static road and basemap inputs for road mapping.

    Reads the filtered national road network layer and original Canada basemap,
    verifies that both datasets have defined coordinate reference systems,
    reprojects the basemap to the road CRS, validates required road columns, and
    returns a road-overlay GeoDataFrame with stable ``road_id`` values plus the
    CRS-aligned Canada basemap.
    """
    if not ROAD_NETWORK_PATH.exists():
        raise FileNotFoundError(f"Road network not found: {ROAD_NETWORK_PATH}")

    if not ORIGINAL_BASEMAP_PATH.exists():
        raise FileNotFoundError(f"Original basemap not found: {ORIGINAL_BASEMAP_PATH}")

    print(f"Using road network: {ROAD_NETWORK_PATH.name}")
    print(f"Using original basemap: {ORIGINAL_BASEMAP_PATH.name}")

    roads = gpd.read_file(
        ROAD_NETWORK_PATH,
        layer=ROAD_LAYER,
    )

    canada_basemap = gpd.read_file(
        ORIGINAL_BASEMAP_PATH,
    )

    if roads.crs is None:
        raise ValueError("Road network CRS is undefined.")

    if canada_basemap.crs is None:
        raise ValueError("Original Canada basemap CRS is undefined.")

    canada_basemap = canada_basemap.to_crs(roads.crs)

    required_road_columns = [
        "ROADCLASS",
        "geometry",
    ]

    missing_road_columns = [
        column for column in required_road_columns
        if column not in roads.columns
    ]

    if missing_road_columns:
        raise ValueError(
            f"Road network is missing required columns: {missing_road_columns}"
        )

    roads_overlay = (
        roads[
            [
                "ROADCLASS",
                "geometry",
            ]
        ]
        .copy()
        .reset_index(drop=False)
        .rename(columns={"index": "road_id"})
    )

    print(f"Loaded road segments: {len(roads):,}")
    print(f"Prepared road overlay geometries: {len(roads_overlay):,}")
    print(f"Road CRS: {roads_overlay.crs}")

    return roads_overlay, canada_basemap


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
        "resolution_deg",
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
                "resolution_deg",
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
    """Build centroid-to-centroid geometries for road-enabled graph edges."""

    road_edges = (
        road_edge_connections
        .loc[road_edge_connections["has_road_connection"]]
        .copy()
    )

    road_edges["geometry"] = road_edges.apply(
        lambda row: LineString(
            [
                (row["lon_from"], row["lat_from"]),
                (row["lon_to"], row["lat_to"]),
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
        PROCESSED_ROAD_CONNECTIVITY
        / f"{output_stem}_{connection_method}_region_road_presence.csv"
    )

    road_edge_connections_path = (
        PROCESSED_ROAD_CONNECTIVITY
        / f"{output_stem}_{connection_method}_road_edge_connections.csv"
    )

    road_edge_gpkg_path = (
        PROCESSED_ROAD_CONNECTIVITY
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
            PROCESSED_ROAD_CONNECTIVITY
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
    canada_basemap: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Export a diagnostic map of graph regions containing road segments."""

    regions_with_road_presence = regions.merge(
        region_road_presence,
        on=[
            "region",
            "resolution_deg",
            "keep_method",
        ],
        how="left",
    )

    regions_with_road_presence["has_road"] = (
        regions_with_road_presence["has_road"]
        .fillna(False)
        .astype(bool)
    )

    resolution = regions_with_road_presence["resolution_deg"].iloc[0]
    keep_method = regions_with_road_presence["keep_method"].iloc[0]

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

    canada_basemap.boundary.plot(
        ax=ax,
        color="black",
        linewidth=0.5,
        zorder=3,
    )

    ax.set_title(
        f"Road Presence by Graph Region ({resolution:g}° {keep_method})",
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
    canada_basemap: gpd.GeoDataFrame,
    connection_label: str,
    output_path: Path,
) -> None:
    """Export a diagnostic PNG showing road-enabled graph edges.

    Plots graph regions by ``has_road`` status, overlays the filtered road
    network and Canada basemap boundary for context, and draws enabled graph-edge
    geometries for the selected weak or strong road-connectivity method.
    """

    resolution = regions_with_road_presence["resolution_deg"].iloc[0]
    keep_method = regions_with_road_presence["keep_method"].iloc[0]

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

    canada_basemap.boundary.plot(
        ax=ax,
        color="black",
        linewidth=0.5,
        zorder=4,
    )

    ax.set_title(
        f"{connection_label} Road-Enabled Graph Edge Connectivity "
        f"({resolution:g}° {keep_method})",
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
    canada_basemap: gpd.GeoDataFrame,
    plot_outputs: bool = False,
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

    if canada_basemap.crs != regions.crs:
        canada_for_plot = canada_basemap.to_crs(regions.crs)
    else:
        canada_for_plot = canada_basemap.copy()

    validate_graph_inputs(
        regions=regions,
        graph_edges=graph_edges,
        roads=roads_for_overlay,
    )

    resolution = float(regions["resolution_deg"].iloc[0])
    keep_method = str(regions["keep_method"].iloc[0])

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
            "resolution_deg",
            "keep_method",
        ],
        how="left",
    )

    regions_with_road_presence["has_road"] = (
        regions_with_road_presence["has_road"]
        .fillna(False)
        .astype(bool)
    )

    weak_connections = build_weak_road_connections(
        graph_edges=graph_edges,
        region_road_presence=region_road_presence,
    )

    strong_connections = build_strong_road_connections(
        graph_edges=graph_edges,
        road_region_overlay=road_region_overlay,
    )

    weak_edge_gdf = build_road_edge_geometries(
        road_edge_connections=weak_connections,
        regions_crs=regions.crs,
    )

    strong_edge_gdf = build_road_edge_geometries(
        road_edge_connections=strong_connections,
        regions_crs=regions.crs,
    )

    weak_output_files = export_road_connectivity_outputs(
        output_stem=output_stem,
        connection_method="weak",
        region_road_presence=region_road_presence,
        road_edge_connections=weak_connections,
        road_edge_gdf=weak_edge_gdf,
        road_region_overlay=road_region_overlay,
    )

    strong_output_files = export_road_connectivity_outputs(
        output_stem=output_stem,
        connection_method="strong",
        region_road_presence=region_road_presence,
        road_edge_connections=strong_connections,
        road_edge_gdf=strong_edge_gdf,
        road_region_overlay=None,
    )

    if plot_outputs:
        plot_dir = PROCESSED_ROAD_CONNECTIVITY / "plots"
        plot_dir.mkdir(parents=True, exist_ok=True)

        road_presence_plot_path = (
            plot_dir
            / f"{graph_stem}_road_presence.png"
        )

        weak_connectivity_plot_path = (
            plot_dir
            / f"{graph_stem}_weak_connectivity.png"
        )

        strong_connectivity_plot_path = (
            plot_dir
            / f"{graph_stem}_strong_connectivity.png"
        )

        plot_region_road_presence(
            regions=regions,
            region_road_presence=region_road_presence,
            roads_overlay=roads_for_overlay,
            canada_basemap=canada_for_plot,
            output_path=road_presence_plot_path,
        )

        plot_road_edge_connectivity(
            regions_with_road_presence=regions_with_road_presence,
            roads_overlay=roads_for_overlay,
            road_edge_gdf=weak_edge_gdf,
            canada_basemap=canada_for_plot,
            connection_label="Weak",
            output_path=weak_connectivity_plot_path,
        )

        plot_road_edge_connectivity(
            regions_with_road_presence=regions_with_road_presence,
            roads_overlay=roads_for_overlay,
            road_edge_gdf=strong_edge_gdf,
            canada_basemap=canada_for_plot,
            connection_label="Strong",
            output_path=strong_connectivity_plot_path,
        )

        print(f"Exported plot: {road_presence_plot_path.name}")
        print(f"Exported plot: {weak_connectivity_plot_path.name}")
        print(f"Exported plot: {strong_connectivity_plot_path.name}")

    regions_with_roads = int(region_road_presence["has_road"].sum())
    weak_edges = int(weak_connections["has_road_connection"].sum())
    strong_edges = int(strong_connections["has_road_connection"].sum())

    weak_edge_share = (
        weak_edges / len(weak_connections)
        if len(weak_connections) > 0
        else 0
    )

    strong_edge_share = (
        strong_edges / len(strong_connections)
        if len(strong_connections) > 0
        else 0
    )

    print(
        f"Regions with roads: "
        f"{regions_with_roads:,} / {len(region_road_presence):,}"
    )

    print(
        f"Weak road-enabled edges: "
        f"{weak_edges:,} / {len(weak_connections):,}"
    )

    print(
        f"Strong road-enabled edges: "
        f"{strong_edges:,} / {len(strong_connections):,}"
    )

    summary = {
        "graph_file": graph_node_path.name,
        "edge_file": graph_edge_path.name,
        "resolution_deg": resolution,
        "keep_method": keep_method,
        "regions": len(regions),
        "graph_edges": len(graph_edges),
        "road_overlay_rows": len(road_region_overlay),
        "unique_roads_matched": road_region_overlay["road_id"].nunique(),
        "regions_with_roads": regions_with_roads,
        "weak_road_edges": weak_edges,
        "strong_road_edges": strong_edges,
        "weak_road_edge_share": weak_edge_share,
        "strong_road_edge_share": strong_edge_share,
    }

    summary.update(weak_output_files)
    summary.update(strong_output_files)

    if plot_outputs:
        summary.update(
            {
                "road_presence_plot_file": road_presence_plot_path.name,
                "weak_connectivity_plot_file": weak_connectivity_plot_path.name,
                "strong_connectivity_plot_file": strong_connectivity_plot_path.name,
            }
        )

    return summary


def main() -> None:
    """Run the road-connectivity mapping workflow for all processed graphs.

    Creates the road-connectivity output folder, discovers processed graph node
    files, loads static road and basemap datasets, builds weak and strong
    road-connectivity outputs for each graph, and writes the consolidated
    road-connectivity summary CSV.
    """
    PROCESSED_ROAD_CONNECTIVITY.mkdir(parents=True, exist_ok=True)

    graph_node_files = find_graph_node_files()

    roads_overlay, canada_basemap = load_static_datasets()

    road_connectivity_summary_rows = []

    for graph_node_path in graph_node_files:
        summary = build_road_connectivity_for_graph(
            graph_node_path=graph_node_path,
            roads_overlay=roads_overlay,
            canada_basemap=canada_basemap,
            plot_outputs=PLOT_OUTPUTS,
        )

        road_connectivity_summary_rows.append(summary)

    road_connectivity_summary = pd.DataFrame(road_connectivity_summary_rows)

    road_connectivity_summary = (
        road_connectivity_summary
        .sort_values(
            by=[
                "resolution_deg",
                "keep_method",
            ]
        )
        .reset_index(drop=True)
    )

    road_connectivity_summary_path = (
        PROCESSED_ROAD_CONNECTIVITY
        / "road_connectivity_summary.csv"
    )

    road_connectivity_summary.to_csv(
        road_connectivity_summary_path,
        index=False,
    )

    print("\nRoad connectivity processing complete.")
    print(f"Exported summary: {road_connectivity_summary_path.name}")


if __name__ == "__main__":
    main()
