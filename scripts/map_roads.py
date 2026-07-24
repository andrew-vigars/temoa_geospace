"""
Map filtered road networks onto Geospatial-CANOE region graphs.

This module implements Stage 4 of the Geospatial-CANOE preprocessing workflow.
It loads the profile-specific filtered road-network GeoPackage and matching
Stage 2 graph products, overlays road segments onto graph regions, and derives
road-enabled graph edges using the connectivity methods selected in the shared
TOML build profile.

Two connectivity methods are supported:

1. Weak connectivity, where both endpoint regions contain at least one road
   segment.
2. Strong connectivity, where both endpoint regions intersect at least one
   common road segment.

For each graph and configured method, the module exports region-level road
presence, edge-level connectivity results, road-enabled edge geometries, and
optional diagnostic maps. A profile-level summary table records the resulting
coverage and output filenames.

Inputs
------
data_files/processed/nrn/
    {study_area}_filtered_road_networks.gpkg
data_files/processed/basemaps/
    {study_area}_boundary_wgs84.gpkg
data_files/processed/graph/
    {basemap_stem}_graph_nodes.gpkg
    {basemap_stem}_graph_edges.csv

Outputs
-------
data_files/processed/road_connectivity/
    {output_stem}_weak_region_road_presence.csv
    {output_stem}_weak_road_edge_connections.csv
    {output_stem}_weak_road_edges.gpkg
    {output_stem}_strong_region_road_presence.csv
    {output_stem}_strong_road_edge_connections.csv
    {output_stem}_strong_road_edges.gpkg
    {output_stem}_road_region_overlay.gpkg
    {study_area}_road_connectivity_summary.csv

Optional outputs
----------------
data_files/processed/road_connectivity/plots/
    {graph_stem}_road_presence.png
    {graph_stem}_{connection_method}_connectivity.png
"""

import argparse
from pathlib import Path
from typing import TypedDict

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
# TypedDict for road-connectivity method results
# =============================================================================

class RoadConnectivityMethodResult(TypedDict):
    """Typed result container for one road-connectivity method.

    Attributes
    ----------
    connections : pd.DataFrame
        Graph-edge table containing the calculated road-connectivity indicators.
    edge_gdf : gpd.GeoDataFrame
        Road-enabled graph-edge geometries for the connectivity method.
    enabled_edges : int
        Number of graph edges classified as road-enabled.
    edge_share : float
        Fraction of graph edges classified as road-enabled.
    """

    connections: pd.DataFrame
    edge_gdf: gpd.GeoDataFrame
    enabled_edges: int
    edge_share: float

# =============================================================================
# Load helpers
# =============================================================================

def find_graph_node_files(
    config: GeospatialBuildConfig,
) -> list[Path]:
    """Find graph-node GeoPackages associated with the selected build profile.

    A profile-specific filename pattern is constructed from the configured study-area
    label and basemap retention method. Matching graph-node GeoPackages are collected
    from ``PROCESSED_GRAPH``, sorted for deterministic processing, and reported to the
    console.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the study-area label and
        basemap retention method.

    Returns
    -------
    list[Path]
        Sorted paths to graph-node GeoPackages matching the selected build profile.

    Raises
    ------
    FileNotFoundError
        If no graph-node GeoPackages match the expected profile-specific filename
        pattern.
    """

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
    """Infer the graph-edge CSV paired with a graph-node GeoPackage.

    The graph-node filename suffix is replaced with the corresponding graph-edge
    CSV suffix, and the resulting filename is resolved within ``PROCESSED_GRAPH``.

    Parameters
    ----------
    graph_node_path : Path
        Path to a graph-node GeoPackage whose filename ends with
        ``_graph_nodes.gpkg``.

    Returns
    -------
    Path
        Expected path to the corresponding ``_graph_edges.csv`` file.
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
    """Load and validate the configured road and study-area boundary layers.

    The profile-specific filtered road-network GeoPackage and processed WGS84
    study-area boundary are located and loaded. The selected road layer is
    validated for a defined CRS, required metadata columns, and consistency with
    the configured study-area label. A reduced road-overlay table is then created
    with a unique sequential ``road_id`` for each geometry.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the study-area label and
        configured road-connectivity layer.

    Returns
    -------
    tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]
        Prepared road-overlay geometries and the processed study-area boundary.

    Raises
    ------
    FileNotFoundError
        If the configured road-network GeoPackage or study-area boundary
        GeoPackage does not exist.
    ValueError
        If either loaded layer lacks a defined CRS, the road layer is missing
        required columns, or its ``study_area`` values do not match the configured
        study-area label.
    """

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

    The function verifies that each input contains the columns required by the
    road-connectivity workflow. It also confirms that the graph-node and road
    layers have defined and matching coordinate reference systems, that graph-node
    region identifiers are unique, and that every graph edge references valid
    source and destination regions.

    Parameters
    ----------
    regions : gpd.GeoDataFrame
        Graph-node polygons and metadata, including unique ``region`` identifiers.
    graph_edges : pd.DataFrame
        Graph-edge table containing source and destination region identifiers.
    roads : gpd.GeoDataFrame
        Road-overlay geometries with unique ``road_id`` values and road classes.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If required columns are missing, either spatial layer has an undefined CRS,
        the spatial-layer CRSs do not match, graph-node region identifiers are not
        unique, or graph edges reference unknown regions.
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
    """Build the road-to-region spatial overlay.

    Road geometries are spatially joined to graph-node polygons using the
    ``intersects`` predicate. Each returned row represents one road segment and
    graph-region intersection, preserving the road attributes and adding the
    matching region identifier.

    Parameters
    ----------
    roads_overlay : gpd.GeoDataFrame
        Prepared road-segment geometries and attributes, including ``road_id``.
    regions : gpd.GeoDataFrame
        Graph-node polygons containing ``region`` identifiers.

    Returns
    -------
    gpd.GeoDataFrame
        Road-overlay records matched to intersecting graph regions.
    """

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
    """Build region-level road-presence indicators for all graph nodes.

    Unique road segments and total road-region intersections are counted for each
    region represented in the spatial overlay. These counts are merged onto the
    complete graph-node table so regions without roads are retained with zero
    counts. A boolean ``has_road`` field identifies regions containing at least one
    intersecting road segment.

    Parameters
    ----------
    regions : gpd.GeoDataFrame
        Graph-node polygons and profile metadata for all model regions.
    road_region_overlay : gpd.GeoDataFrame
        Road-segment records matched to intersecting graph regions.

    Returns
    -------
    pd.DataFrame
        Region-level table containing graph metadata, road-segment counts,
        road-region intersection counts, and a boolean ``has_road`` indicator.
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
    """Build weak road-connectivity indicators for graph edges.

    Road-presence values are mapped from the region-level lookup onto each edge's
    source and destination regions. An edge is classified as weakly road-enabled
    only when both endpoint regions contain at least one road segment. The returned
    table preserves all original graph-edge fields and adds endpoint flags,
    connection status, method metadata, and the canonical region-pair identifier.

    Parameters
    ----------
    graph_edges : pd.DataFrame
        Graph-edge table containing ``region_from``, ``region_to``, and
        ``edge_region`` identifiers.
    region_road_presence : pd.DataFrame
        Region-level road-presence table containing ``region`` and boolean
        ``has_road`` fields.

    Returns
    -------
    pd.DataFrame
        Copy of the graph-edge table with ``has_road_from``, ``has_road_to``,
        ``has_road_connection``, ``connection_method``, and ``region_pair`` fields.
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
    """Build strong road-connectivity indicators for graph edges.

    The road-region overlay is reduced to unique ``road_id`` and ``region`` pairs,
    then converted into a lookup of road-segment sets for each graph region. For
    each graph edge, the source and destination road sets are intersected. An edge
    is classified as strongly road-enabled when the two endpoint regions share at
    least one road segment.

    Parameters
    ----------
    graph_edges : pd.DataFrame
        Graph-edge table containing ``region_from``, ``region_to``, and
        ``edge_region`` identifiers.
    road_region_overlay : gpd.GeoDataFrame
        Road-to-region intersection table containing ``road_id`` and ``region``
        fields.

    Returns
    -------
    pd.DataFrame
        Copy of the graph-edge table with the number of shared road segments, a
        boolean ``has_road_connection`` indicator, connection-method metadata, and
        the canonical ``region_pair`` identifier.
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
    """Build geometries for graph edges with valid road connections.

    The input table is validated for the native graph-coordinate columns produced
    by Stage 2. Rows without a road connection are removed, and each retained edge
    is converted to a ``LineString`` using its native source and destination
    coordinates. The resulting GeoDataFrame is assigned the graph-node CRS.

    Using native coordinates is required because the graph CRS may be projected.
    Constructing geometries from WGS84 longitude and latitude while assigning a
    projected CRS would place the lines incorrectly.

    Parameters
    ----------
    road_edge_connections : pd.DataFrame
        Graph-edge connection table containing native endpoint coordinates and a
        boolean ``has_road_connection`` field.
    regions_crs
        Coordinate reference system of the corresponding graph-node layer.

    Returns
    -------
    gpd.GeoDataFrame
        Road-enabled graph edges with ``LineString`` geometries in ``regions_crs``.

    Raises
    ------
    ValueError
        If any required native endpoint-coordinate column is missing.
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
    """Export road-connectivity products for one graph and connection method.

    Region-level road-presence data and graph-edge connection results are written
    to CSV, while road-enabled edge geometries are written to a GeoPackage. Any
    existing edge GeoPackage at the target path is removed before export to avoid
    retaining stale layers. The optional road-to-region overlay is exported in the
    same way when supplied.

    Parameters
    ----------
    output_dir : Path
        Directory where the road-connectivity outputs will be written.
    output_stem : str
        Common filename stem identifying the graph or basemap variant.
    connection_method : str
        Road-connectivity method represented by the outputs, such as ``"weak"`` or
        ``"strong"``.
    region_road_presence : pd.DataFrame
        Region-level road-presence table.
    road_edge_connections : pd.DataFrame
        Graph-edge table containing road-connection indicators and metadata.
    road_edge_gdf : gpd.GeoDataFrame
        Road-enabled graph-edge geometries.
    road_region_overlay : gpd.GeoDataFrame | None, optional
        Road-to-region intersection layer to export once for the graph, by default
        ``None``.

    Returns
    -------
    dict
        Mapping of summary-table field names to the exported output filenames.
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
    """Plot and save a diagnostic map of road presence by graph region.

    Region-level road-presence indicators are merged onto the graph-node polygons,
    with missing values treated as regions without roads. The regions are coloured
    by their boolean ``has_road`` status and plotted with the source road geometries
    and study-area boundary for geographic context. Grid metadata are used to build
    a resolution-specific plot title, and the completed figure is written to disk.

    Parameters
    ----------
    regions : gpd.GeoDataFrame
        Graph-node polygons containing region identifiers and grid metadata.
    region_road_presence : pd.DataFrame
        Region-level table containing the boolean ``has_road`` indicator.
    roads_overlay : gpd.GeoDataFrame
        Road geometries plotted as contextual overlays.
    study_area_boundary : gpd.GeoDataFrame
        Study-area boundary used to outline the plotted region.
    output_path : Path
        Destination path for the exported diagnostic figure.

    Returns
    -------
    None
    """

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
    """Plot and save road-enabled graph-edge connectivity.

    Graph regions are coloured by their ``has_road`` status, with the filtered
    road network and study-area boundary plotted for geographic context. Enabled
    graph-edge geometries are overlaid for the selected weak or strong connectivity
    method. Grid metadata are used to construct a resolution-specific title, and
    the completed figure is written to ``output_path``.

    Parameters
    ----------
    regions_with_road_presence : gpd.GeoDataFrame
        Graph-region polygons containing road-presence indicators and grid metadata.
    roads_overlay : gpd.GeoDataFrame
        Filtered road-network geometries used as contextual background.
    road_edge_gdf : gpd.GeoDataFrame
        Road-enabled graph-edge geometries for the selected connectivity method.
    study_area_boundary : gpd.GeoDataFrame
        Study-area boundary used to outline the plotted region.
    connection_label : str
        Human-readable connectivity-method label used in the figure title.
    output_path : Path
        Destination path for the exported PNG figure.

    Returns
    -------
    None
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
) -> dict[str, object]:
    """Build and export configured road connectivity for one graph.

    The graph-node GeoPackage and its paired graph-edge CSV are loaded, while the
    static road and study-area boundary layers are reprojected to the graph CRS
    when necessary. After validating the inputs, roads are intersected with graph
    regions to derive region-level road presence.

    Each connectivity method enabled in the build profile is then evaluated.
    Weak connectivity requires road presence in both endpoint regions, whereas
    strong connectivity requires the endpoints to share at least one intersecting
    road segment. The resulting connection tables and road-enabled edge geometries
    are exported, and diagnostic maps are optionally generated.

    Parameters
    ----------
    graph_node_path : Path
        Path to the graph-node GeoPackage for the graph being processed.
    roads_overlay : gpd.GeoDataFrame
        Prepared road-segment geometries and attributes.
    study_area_boundary : gpd.GeoDataFrame
        Processed study-area boundary used for diagnostic plotting.
    config : GeospatialBuildConfig
        Validated build profile containing the expected study area, selected road
        layer, enabled connectivity methods, and plotting preference.

    Returns
    -------
    dict[str, object]
        Summary record containing graph metadata, road-overlay statistics,
        connectivity results for each configured method, and exported filenames.

    Raises
    ------
    FileNotFoundError
        If the graph-edge CSV paired with ``graph_node_path`` does not exist.
    ValueError
        If graph or road inputs fail validation, the graph belongs to a different
        study area than the configuration, or an unsupported connectivity method
        is requested.
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

    regions_crs = regions.crs
    if regions_crs is None:
        raise ValueError(
            f"Graph node CRS is undefined: {graph_node_path}"
        )

    if roads_overlay.crs != regions_crs:
        roads_for_overlay = roads_overlay.to_crs(regions_crs)
    else:
        roads_for_overlay = roads_overlay.copy()

    if study_area_boundary.crs != regions_crs:
        boundary_for_plot = study_area_boundary.to_crs(regions_crs)
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
    resolution_unit = str(
        regions["resolution_unit"].iloc[0]
    )
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

    method_results: dict[
        str,
        RoadConnectivityMethodResult,
    ] = {}
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
                "Unsupported road-connectivity method: "
                f"{method!r}"
            )

        edge_gdf = build_road_edge_geometries(
            road_edge_connections=connections,
            regions_crs=regions_crs,
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
                if method
                == config.road_connectivity.methods[0]
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

    road_presence_plot_path: Path | None = None
    connectivity_plot_files: dict[str, str] = {}

    if config.road_connectivity.plot_outputs:
        plot_dir = (
            PROCESSED_ROAD_CONNECTIVITY
            / "plots"
        )
        plot_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

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

        for method, result in method_results.items():
            connectivity_plot_path = (
                plot_dir
                / f"{graph_stem}_{method}_connectivity.png"
            )

            plot_road_edge_connectivity(
                regions_with_road_presence=(
                    regions_with_road_presence
                ),
                roads_overlay=roads_for_overlay,
                road_edge_gdf=result["edge_gdf"],
                study_area_boundary=boundary_for_plot,
                connection_label=method.title(),
                output_path=connectivity_plot_path,
            )

            connectivity_plot_files[
                f"{method}_connectivity_plot_file"
            ] = connectivity_plot_path.name

        print(
            f"Exported plot: "
            f"{road_presence_plot_path.name}"
        )

        for plot_file in connectivity_plot_files.values():
            print(f"Exported plot: {plot_file}")

    regions_with_roads = int(
        region_road_presence["has_road"].sum()
    )

    print(
        "Regions with roads: "
        f"{regions_with_roads:,} / "
        f"{len(region_road_presence):,}"
    )

    for method, result in method_results.items():
        print(
            f"{method.title()} road-enabled edges: "
            f"{result['enabled_edges']:,} / "
            f"{len(result['connections']):,}"
        )

    summary: dict[str, object] = {
        "study_area": study_area,
        "graph_file": graph_node_path.name,
        "edge_file": graph_edge_path.name,
        "grid_type": grid_type,
        "resolution": resolution,
        "resolution_unit": resolution_unit,
        "keep_method": keep_method,
        "road_layer": (
            config.road_connectivity.road_layer
        ),
        "regions": len(regions),
        "graph_edges": len(graph_edges),
        "road_overlay_rows": len(
            road_region_overlay
        ),
        "unique_roads_matched": (
            road_region_overlay["road_id"].nunique()
        ),
        "regions_with_roads": regions_with_roads,
    }

    for method, result in method_results.items():
        summary[
            f"{method}_road_edges"
        ] = result["enabled_edges"]

        summary[
            f"{method}_road_edge_share"
        ] = result["edge_share"]

    summary.update(exported_files)

    if road_presence_plot_path is not None:
        summary["road_presence_plot_file"] = (
            road_presence_plot_path.name
        )
        summary.update(connectivity_plot_files)

    return summary


def parse_args() -> argparse.Namespace:
    """Parse the command-line argument for the Stage 4 build profile.

    The command-line interface requires the path to the TOML configuration shared
    across the Geospatial-CANOE preprocessing workflow.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments containing the required ``config`` path.
    """

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
    """Run the Stage 4 road-connectivity workflow for one build profile.

    The processed road-connectivity output directory is created, all graph-node
    GeoPackages matching the selected profile are discovered, and the configured
    road and study-area boundary layers are loaded once for reuse. Each graph is
    then processed to build and export its configured road-connectivity products.

    Per-graph summary records are combined, sorted by grid type, resolution, and
    retention method, written to a profile-specific CSV, and returned.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the study-area identity,
        basemap settings, road layer, connectivity methods, and plotting options.

    Returns
    -------
    pd.DataFrame
        Sorted summary table containing road-connectivity statistics and exported
        filenames for each processed graph.

    Raises
    ------
    FileNotFoundError
        If required graph, road-network, boundary, or paired graph-edge inputs are
        missing.
    ValueError
        If loaded graph or road inputs fail validation or contain unsupported
        configuration values.
    """

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
    """Load the shared TOML build profile and run Stage 4.

    The command-line arguments are parsed to obtain the build-profile path. The
    profile is then loaded, validated, printed to the console, and passed to the
    road-connectivity workflow.

    Returns
    -------
    None
    """

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_road_connectivity_build(config)


if __name__ == "__main__":
    main()
