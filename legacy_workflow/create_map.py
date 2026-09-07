"""Decode and map geospatial CANOE/TEMOA model outputs.

This script reads solved CANOE/TEMOA output flows and renders transport routes
using the matching graph-node polygons, processed basemap polygons, and optional
road-overlay and road-connectivity layers as geographic context.

The plotting workflow preserves the visual formatting of the original script
while moving top-level execution into ``main()`` and passing data objects
explicitly between functions instead of relying on module-level mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import TypeAlias
from urllib.error import URLError

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from rasterio.errors import RasterioError
from shapely.geometry import LineString
from xyzservices import providers

# =============================================================================
# Project import path
# =============================================================================

def find_project_root() -> Path:
    """Locate the repository root using project-specific sentinel paths.

    Searches upward from both this script's path and the current working
    directory. The first directory containing both ``data_files/`` and
    ``db_mgmt.py`` is treated as the project root.

    Returns
    -------
    Path
        Absolute path to the detected project root.

    Raises
    ------
    FileNotFoundError
        If no parent directory contains the expected project structure.
    """
    search_starts = [
        Path(__file__).resolve(),
        Path.cwd().resolve(),
    ]

    for start in search_starts:
        for candidate in [start, *start.parents]:
            if (
                (candidate / "data_files").exists()
                and (candidate / "db_mgmt.py").exists()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate project root. Expected to find data_files/ and db_mgmt.py."
    )


PROJECT_ROOT = find_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

import db_mgmt as mgmt


# =============================================================================
# Constants
# =============================================================================

MIN_TRANSPORT_FLOW = 1e-3
MIN_PROCESS_FLOW = 1e-3

PLOT_WEB_TILES = True
PLOT_ROAD_OVERLAY = False
PLOT_ROAD_EDGE_LAYER = False
PLOT_PARALLEL_TRANSPORT_ARCS = True

TechStyle: TypeAlias = tuple[pd.DataFrame, str, str, float]
PointStyle: TypeAlias = tuple[pd.DataFrame, str]


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class ProjectPaths:
    """Resolved filesystem paths for the project mapping workflow.

    Attributes
    ----------
    project_root : Path
        Root directory of the repository or active project workspace.
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model run outputs.
    data_files : Path
        Directory containing raw and processed model/geospatial input data.
    """

    project_root: Path
    output_root: Path
    data_files: Path


@dataclass(frozen=True)
class SelectedRun:
    """Selected CANOE/TEMOA output run and SQLite database.

    Attributes
    ----------
    run_dir : Path
        Timestamped model-run directory selected from ``output_files/``.
    db_path : Path
        SQLite database selected from within the model-run directory.
    """

    run_dir: Path
    db_path: Path


@dataclass(frozen=True)
class GeospatialPaths:
    """Resolved geospatial input and figure-output paths for a selected run.

    Attributes
    ----------
    basemap_stem : str
        Canonical basemap identifier inferred from the selected database or run
        folder name.
    node_path : Path
        Path to the graph-node polygon GeoPackage for the inferred basemap.
    edge_path : Path
        Path to the graph-edge CSV for the inferred basemap.
    basemap_path : Path
        Path to the processed basemap polygon GeoPackage.
    road_edge_gpkg_path : Path | None
        Optional path to the road-enabled graph-edge GeoPackage, if available.
    figure_dir : Path
        Directory where generated figures are saved.
    fig_stem : str
        Filename stem used for generated figure outputs.
    """

    basemap_stem: str
    node_path: Path
    edge_path: Path
    basemap_path: Path
    road_edge_gpkg_path: Path | None
    figure_dir: Path
    fig_stem: str


@dataclass(frozen=True)
class PlotSpacing:
    """Resolution-aware plotting parameters for map readability.

    Attributes
    ----------
    diagnostic_mode : bool
        If ``True``, disables random node jitter so plotted locations remain
        exactly aligned with model-region coordinates.
    grid_res_deg : float
        Inferred spatial grid resolution in decimal degrees.
    jitter_deg : float
        Maximum random lon/lat offset applied to process-point markers.
    parallel_offset_m : float
        Offset distance, in metres, used to separate parallel transport arcs
        along the same corridor.
    parallel_max_offset_m : float
        Maximum allowed parallel transport-arc offset, in metres.
    """

    diagnostic_mode: bool
    grid_res_deg: float
    jitter_deg: float
    parallel_offset_m: float
    parallel_max_offset_m: float


@dataclass
class ModelTables:
    """CANOE/TEMOA tables required to build map layers.

    Attributes
    ----------
    flow_out : pd.DataFrame
        Output flow table used to derive process activity and transport flows.
    demand : pd.DataFrame
        Demand table used to identify and size gasoline demand markers.
    limit_capacity : pd.DataFrame
        Capacity-limit table used to locate available CO2 capture capacity.
    """

    flow_out: pd.DataFrame
    demand: pd.DataFrame
    limit_capacity: pd.DataFrame


@dataclass
class GeospatialData:
    """Loaded geospatial layers used to render model-output maps.

    Attributes
    ----------
    sites : gpd.GeoDataFrame
        Graph-node/model-region polygons indexed by region ID, with centroid
        longitude and latitude fields used for plotting.
    edges : pd.DataFrame
        Graph-edge table linking transport pseudo-regions to source and target
        model regions.
    basemap : gpd.GeoDataFrame
        Processed basemap polygons used as geographic context.
    road_edge_layer : gpd.GeoDataFrame | None
        Optional road-enabled graph-edge geometries used to show available
        road-connected corridors.
    """

    sites: gpd.GeoDataFrame
    edges: pd.DataFrame
    basemap: gpd.GeoDataFrame
    road_edge_layer: gpd.GeoDataFrame | None


@dataclass
class PlotLayers:
    """Prepared plotting layers derived from model and geospatial tables.

    Attributes
    ----------
    tech_points : dict[str, POINT_STYLE]
        Node-level process layers keyed by display name. Each value contains the
        point DataFrame and its plotting color.
    tech_links : dict[str, TECH_STYLE]
        Transport-flow layers keyed by display name. Each value contains the link
        DataFrame, plotting color, line style, and width factor.
    demand_pts : pd.DataFrame
        Demand-point table containing coordinates and demand magnitudes.
    size_demand : pd.Series
        Marker sizes derived from demand magnitudes for plotting.
    """

    tech_points: dict[str, PointStyle]
    tech_links: dict[str, TechStyle]
    demand_pts: pd.DataFrame
    size_demand: pd.Series


# =============================================================================
# Project and run selection
# =============================================================================

def resolve_project_paths() -> ProjectPaths:
    """Prepared plotting layers derived from model and geospatial tables.

    Attributes
    ----------
    tech_points : dict[str, POINT_STYLE]
        Node-level process layers keyed by display name. Each value contains
        the point DataFrame and its plotting color.
    tech_links : dict[str, TECH_STYLE]
        Transport-flow layers keyed by display name. Each value contains the
        link DataFrame, plotting color, line style, and width factor.
    demand_pts : pd.DataFrame
        Demand-point table with longitude, latitude, and demand magnitude.
    size_demand : pd.Series
        Marker sizes computed from demand magnitudes for plotting.
    """

    script_path = Path(__file__).resolve()

    if (
        (script_path.parent / "output_files").exists()
        and (script_path.parent / "data_files").exists()
    ):
        project_root = script_path.parent
    elif (
        (script_path.parent.parent / "output_files").exists()
        and (script_path.parent.parent / "data_files").exists()
    ):
        project_root = script_path.parent.parent
    else:
        project_root = Path.cwd().resolve()

    return ProjectPaths(
        project_root=project_root,
        output_root=project_root / "output_files",
        data_files=project_root / "data_files",
    )


def select_model_run(output_root: Path) -> Path:
    """Prompt the user to select a model-run directory.

    Searches ``output_root`` for subdirectories containing at least one SQLite
    database, prints the valid options, and returns the directory selected by
    index.

    Parameters
    ----------
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model-run outputs.

    Returns
    -------
    Path
        Selected model-run directory.

    Raises
    ------
    SystemExit
        If no valid run directories are found or if the selected index is
        invalid.
    """
    runs = sorted(
        run_dir
        for run_dir in output_root.iterdir()
        if run_dir.is_dir() and any(run_dir.glob("*.sqlite"))
    )

    if not runs:
        print(f"No valid runs found in {output_root.resolve()}")
        sys.exit(1)

    print("\nAvailable model runs:")
    for index, run_dir in enumerate(runs):
        sqlite_files = sorted(run_dir.glob("*.sqlite"))
        print(f"  [{index}] {run_dir.name}  ({len(sqlite_files)} sqlite file(s))")

    selected_index = input("\nSelect run index: ").strip()

    try:
        return runs[int(selected_index)]
    except (ValueError, IndexError):
        print("Invalid selection.")
        sys.exit(1)


def select_sqlite_database(run_dir: Path) -> Path:
    """Select a SQLite database from a model-run directory.

    If the run directory contains one SQLite database, that file is selected
    automatically. If multiple SQLite databases are present, prints the
    available files with their sizes and prompts the user to select one by
    index.

    Parameters
    ----------
    run_dir : Path
        Model-run directory containing one or more SQLite database files.

    Returns
    -------
    Path
        Selected SQLite database path.

    Raises
    ------
    SystemExit
        If multiple database files are available and the selected index is
        invalid.
    """

    sqlite_files = sorted(run_dir.glob("*.sqlite"))

    if len(sqlite_files) == 1:
        db_path = sqlite_files[0]
    else:
        print("\nAvailable SQLite files:")
        for index, sqlite_path in enumerate(sqlite_files):
            size_mb = sqlite_path.stat().st_size / 1e6
            print(f"  [{index}] {sqlite_path.name}  ({size_mb:.1f} MB)")

        selected_index = input("\nSelect database index: ").strip()

        try:
            db_path = sqlite_files[int(selected_index)]
        except (ValueError, IndexError):
            print("Invalid database selection.")
            sys.exit(1)

    print(f"  Selected database: {db_path}")
    return db_path


def select_run_and_database(output_root: Path) -> SelectedRun:
    """Select a model run and SQLite database for mapping.

    Prompts the user to choose a model-run directory from ``output_root``,
    then selects the SQLite database to read from that run. The selected paths
    are returned as a ``SelectedRun`` container.

    Parameters
    ----------
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model-run outputs.

    Returns
    -------
    SelectedRun
        Selected model-run directory and SQLite database path.

    Raises
    ------
    SystemExit
        If no valid run is found or if the user makes an invalid selection.
    """

    run_dir = select_model_run(output_root)
    db_path = select_sqlite_database(run_dir)
    return SelectedRun(run_dir=run_dir, db_path=db_path)


# =============================================================================
# Path inference and validation
# =============================================================================

def infer_basemap_stem(db_path: Path) -> str:
    """Infer the canonical basemap stem from a database or run-folder name.

    Searches the selected SQLite database filename first, then the parent run
    directory name, for a basemap identifier matching the expected CANOE
    geospatial naming convention. Common database prefixes such as
    ``input_``, ``solved_``, and ``CANOE_geospatial_`` are ignored before
    matching.

    Parameters
    ----------
    db_path : Path
        Path to the selected CANOE/TEMOA SQLite database.

    Returns
    -------
    str
        Matched basemap stem, such as
        ``provinces_only_basemap_75km_centroid`` or
        ``canada_basemap_0.5deg_intersects``.

    Raises
    ------
    SystemExit
        If no valid basemap stem can be inferred from the database filename or
        parent run-folder name.
    """

    search_texts = [
        db_path.stem,
        db_path.parent.name,
    ]

    for text in search_texts:
        text = re.sub(r"^(input_|solved_)", "", text)
        text = re.sub(r"^CANOE_geospatial_", "", text)

        match = re.search(
            r"[A-Za-z0-9_]+_basemap_"
            r"\d+(?:\.\d+)?(?:deg|km)_"
            r"(?:centroid|intersects)",
            text,
        )

        if match:
            return match.group(0)

    print("Could not infer basemap stem.")
    print(f"  Database filename: {db_path.stem}")
    print(f"  Run folder:        {db_path.parent.name}")
    sys.exit(1)


def build_figure_stem(selected_run: SelectedRun) -> str:
    """Build a figure filename stem from the selected run and database.

    Uses the model-run directory name as the primary figure tag and appends the
    selected database stem when the database identifier is not already present
    in the run name. The standard ``CANOE_geospatial_`` database prefix is
    removed before comparison to keep figure filenames shorter.

    Parameters
    ----------
    selected_run : SelectedRun
        Selected model-run directory and SQLite database path.

    Returns
    -------
    str
        Filename stem used when saving generated map figures.
    """

    run_tag = selected_run.run_dir.name.replace(" ", "_")
    db_tag = selected_run.db_path.stem

    if db_tag.startswith("CANOE_geospatial_"):
        db_tag = db_tag[len("CANOE_geospatial_"):]

    return run_tag if db_tag in run_tag else f"{run_tag}_{db_tag}"


def infer_geospatial_paths(
    data_files: Path,
    selected_run: SelectedRun,
) -> GeospatialPaths:
    """Infer and validate geospatial paths for a selected model run.

    Infers the basemap stem from the selected SQLite database or run-folder
    name, then constructs the required paths to graph nodes, graph edges, and
    processed basemap polygons. Optional road-overlay and road-enabled edge
    layers are discovered when matching files are available. Figure outputs are
    saved beside the selected database.

    Parameters
    ----------
    data_files : Path
        Project ``data_files`` directory containing processed geospatial inputs.
    selected_run : SelectedRun
        Selected model-run directory and SQLite database path.

    Returns
    -------
    GeospatialPaths
        Resolved required geospatial input paths, optional road-context paths,
        and figure-output naming metadata.

    Raises
    ------
    SystemExit
        If the basemap stem cannot be inferred or if any required geospatial
        input file is missing.
    """

    basemap_stem = infer_basemap_stem(selected_run.db_path)

    graph_dir = data_files / "processed" / "graph"
    basemap_dir = data_files / "processed" / "basemaps"
    road_connectivity_dir = data_files / "processed" / "road_connectivity"

    node_path = graph_dir / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = graph_dir / f"{basemap_stem}_graph_edges.csv"
    basemap_path = basemap_dir / f"{basemap_stem}.gpkg"

    road_edge_candidates = sorted(
        road_connectivity_dir.glob(
            f"{basemap_stem}_road_connectivity_*_road_edges.gpkg"
        )
    )

    road_edge_gpkg_path = road_edge_candidates[0] if road_edge_candidates else None

    required_paths = {
        "Graph nodes": node_path,
        "Graph edges": edge_path,
        "Processed basemap": basemap_path,
    }

    missing_paths = {
        name: path for name, path in required_paths.items() if not path.exists()
    }
    if missing_paths:
        print(f"Missing required geospatial files for basemap '{basemap_stem}':")
        for name, path in missing_paths.items():
            print(f"  {name}: {path} (exists: {path.exists()})")
        sys.exit(1)

    figure_dir = selected_run.db_path.parent
    figure_dir.mkdir(parents=True, exist_ok=True)

    paths = GeospatialPaths(
        basemap_stem=basemap_stem,
        node_path=node_path,
        edge_path=edge_path,
        basemap_path=basemap_path,
        road_edge_gpkg_path=road_edge_gpkg_path,
        figure_dir=figure_dir,
        fig_stem=build_figure_stem(selected_run),
    )

    print(f"\nInferred basemap stem: {paths.basemap_stem}")
    print(f"  Graph nodes:       {paths.node_path.name}")
    print(f"  Graph edges:       {paths.edge_path.name}")
    print(f"  Basemap polygons:  {paths.basemap_path.name}")
    print(
        "  Road edge layer:   "
        f"{paths.road_edge_gpkg_path.name if paths.road_edge_gpkg_path else 'not found'}"
    )

    return paths


# =============================================================================
# Data loading and spacing
# =============================================================================

def load_model_tables(db_path: Path) -> ModelTables:
    """Load CANOE/TEMOA tables required by the mapping workflow.

    Reads the selected SQLite database into DataFrames and extracts the output
    flow, demand, and capacity-limit tables used to construct process-point,
    demand-point, and transport-flow map layers.

    Parameters
    ----------
    db_path : Path
        Path to the selected CANOE/TEMOA SQLite database.

    Returns
    -------
    ModelTables
        Copies of the database tables required for map-layer preparation.

    Raises
    ------
    KeyError
        If the database does not contain ``OutputFlowOut``, ``Demand``, or
        ``LimitCapacity``.
    """

    db_tables = mgmt.sqlite_to_dfs(str(db_path))

    return ModelTables(
        flow_out=db_tables["OutputFlowOut"].copy(),
        demand=db_tables["Demand"].copy(),
        limit_capacity=db_tables["LimitCapacity"].copy(),
    )


def load_geospatial_data(paths: GeospatialPaths) -> GeospatialData:
    """Load and normalize geospatial layers for mapping.

    Reads graph-node polygons, graph-edge records, processed basemap polygons,
    and the optional road-enabled edge layer from the resolved paths. Region
    identifiers are coerced to strings, graph nodes are indexed by ``region``,
    and all geospatial context layers are reprojected to match the graph-node
    CRS.

    Parameters
    ----------
    paths : GeospatialPaths
        Resolved geospatial input paths and optional road-context paths.

    Returns
    -------
    GeospatialData
        Loaded graph nodes, graph edges, basemap polygons, and optional
        road-enabled edge geometry prepared for downstream plotting.

    Raises
    ------
    FileNotFoundError
        If a required graph-node, graph-edge, or basemap file is missing.
    KeyError
        If required identifier columns are missing from the graph-node or
        graph-edge inputs.
    ValueError
        If any loaded geospatial layer has no assigned coordinate reference
        system.
    """

    sites = gpd.read_file(paths.node_path)
    edges = pd.read_csv(paths.edge_path)
    basemap = gpd.read_file(paths.basemap_path)

    road_edge_layer = None
    if (
        paths.road_edge_gpkg_path is not None
        and paths.road_edge_gpkg_path.exists()
    ):
        road_edge_layer = gpd.read_file(paths.road_edge_gpkg_path)

    if sites.crs is None:
        raise ValueError(
            f"Graph-node layer has no assigned CRS: {paths.node_path}"
        )

    if basemap.crs is None:
        raise ValueError(
            f"Basemap layer has no assigned CRS: {paths.basemap_path}"
        )

    if road_edge_layer is not None and road_edge_layer.crs is None:
        raise ValueError(
            "Road-enabled edge layer has no assigned CRS: "
            f"{paths.road_edge_gpkg_path}"
        )

    target_crs = sites.crs

    sites["region"] = sites["region"].astype(str)
    sites["site_id"] = sites["region"]
    sites = sites.set_index("region", drop=True)

    edges["edge_region"] = edges["edge_region"].astype(str)
    edges["region_from"] = edges["region_from"].astype(str)
    edges["region_to"] = edges["region_to"].astype(str)

    if basemap.crs != target_crs:
        basemap = basemap.to_crs(target_crs)

    if (
        road_edge_layer is not None
        and road_edge_layer.crs != target_crs
    ):
        road_edge_layer = road_edge_layer.to_crs(target_crs)

    return GeospatialData(
        sites=sites,
        edges=edges,
        basemap=basemap,
        road_edge_layer=road_edge_layer,
    )


def build_point_geodataframe(
    frame: pd.DataFrame,
    target_crs,
    lon_col: str = "lon",
    lat_col: str = "lat",
) -> gpd.GeoDataFrame:
    """Create point geometries from longitude and latitude coordinates.

    The input table is copied, the configured coordinate columns are converted to
    numeric values, and rows with missing or non-numeric coordinates are removed.
    The remaining coordinates are interpreted as WGS84 longitude and latitude,
    converted to point geometry, and reprojected to ``target_crs``.

    Parameters
    ----------
    frame : pd.DataFrame
        Source table containing longitude and latitude columns.
    target_crs
        Coordinate reference system to which the point geometries are reprojected.
        Accepts any CRS representation supported by GeoPandas.
    lon_col : str, default="lon"
        Name of the longitude column in ``frame``.
    lat_col : str, default="lat"
        Name of the latitude column in ``frame``.

    Returns
    -------
    gpd.GeoDataFrame
        Copy of the valid input rows with point geometry in ``target_crs``.
    """

    points = frame.copy()
    points[lon_col] = pd.to_numeric(points[lon_col], errors="coerce")
    points[lat_col] = pd.to_numeric(points[lat_col], errors="coerce")
    points = points.dropna(subset=[lon_col, lat_col]).copy()

    return gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points[lon_col], points[lat_col]),
        crs="EPSG:4326",
    ).to_crs(target_crs)


def native_plot_crs(geodata: GeospatialData):
    """Return the coordinate reference system used for native-coordinate plotting.

    The graph-node layer defines the native spatial reference for model-region
    geometry and associated map layers. This function validates that the node layer
    has an assigned coordinate reference system before returning it.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded geospatial layers containing the graph-node GeoDataFrame.

    Returns
    -------
    object
        Coordinate reference system assigned to ``geodata.sites``.

    Raises
    ------
    ValueError
        If the graph-node layer has no assigned coordinate reference system.
    """

    if geodata.sites.crs is None:
        raise ValueError("Graph-node layer has no CRS.")
    return geodata.sites.crs


def infer_degree_resolution(basemap_stem: str) -> float | None:
    """Infer grid resolution in decimal degrees from a basemap stem.

    Parses basemap names containing tokens such as ``_1deg_`` or
    ``_0.5deg_`` and returns the numeric grid resolution. If no degree
    resolution token is found, returns ``None`` so callers can use a fallback
    spacing method.

    Parameters
    ----------
    basemap_stem : str
        Basemap identifier, such as ``canada_basemap_1deg_intersects`` or
        ``canada_basemap_0.5deg_centroid``.

    Returns
    -------
    float | None
        Parsed grid resolution in decimal degrees, or ``None`` if the naming
        pattern does not contain a degree-resolution token.
    """

    match = re.search(r"_(\d+(?:\.\d+)?)deg(?:_|$)", basemap_stem)
    return float(match.group(1)) if match else None


def infer_centroid_spacing_deg(sites_gdf: gpd.GeoDataFrame) -> float:
    """Estimate representative graph-centroid spacing in decimal degrees.

    The function uses existing ``lon`` and ``lat`` columns when available.
    Otherwise, it computes geometry centroids in the layer's native CRS and
    reprojects them to WGS84. Representative longitudinal and latitudinal spacing
    is calculated from the median positive difference between unique centroid
    coordinates, and the smaller valid spacing is returned.

    Parameters
    ----------
    sites_gdf : gpd.GeoDataFrame
        Graph-node layer containing centroid coordinates or polygon geometries.

    Returns
    -------
    float
        Estimated representative centroid spacing in decimal degrees. Returns
        ``1.0`` when no positive finite spacing can be inferred.

    Raises
    ------
    ValueError
        If ``sites_gdf`` has no assigned coordinate reference system.
    """

    if sites_gdf.crs is None:
        raise ValueError(
            "Cannot infer plot spacing from a layer without a CRS."
        )

    if {"lon", "lat"}.issubset(sites_gdf.columns):
        lon_vals = np.sort(
            pd.to_numeric(
                sites_gdf["lon"],
                errors="coerce",
            ).dropna().unique()
        )
        lat_vals = np.sort(
            pd.to_numeric(
                sites_gdf["lat"],
                errors="coerce",
            ).dropna().unique()
        )
    else:
        centroids = sites_gdf.geometry.centroid
        centroid_gdf = gpd.GeoDataFrame(
            geometry=centroids,
            crs=sites_gdf.crs,
        ).to_crs(epsg=4326)

        lon_vals = np.sort(centroid_gdf.geometry.x.unique())
        lat_vals = np.sort(centroid_gdf.geometry.y.unique())

    lon_diffs = np.diff(lon_vals)
    lat_diffs = np.diff(lat_vals)

    lon_step = (
        np.median(lon_diffs[lon_diffs > 1e-9])
        if np.any(lon_diffs > 1e-9)
        else np.nan
    )
    lat_step = (
        np.median(lat_diffs[lat_diffs > 1e-9])
        if np.any(lat_diffs > 1e-9)
        else np.nan
    )

    candidates = [
        float(abs(step))
        for step in (lon_step, lat_step)
        if np.isfinite(step) and abs(step) > 0
    ]

    return min(candidates) if candidates else 1.0


def configure_resolution_aware_spacing(
    basemap_stem: str,
    sites_gdf: gpd.GeoDataFrame,
    diagnostic_mode: bool = False,
) -> PlotSpacing:
    """Configure plotting offsets from the inferred spatial resolution.

    Infers the model grid resolution from the basemap stem, falling back to
    centroid-coordinate spacing when needed. The resulting resolution is used
    to scale point jitter and parallel transport-arc offsets so figures remain
    readable across different grid sizes.

    Parameters
    ----------
    basemap_stem : str
        Basemap identifier used to parse degree resolution when available.
    sites_gdf : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame containing centroid coordinate
        columns used for fallback spacing inference.
    diagnostic_mode : bool, default=False
        If ``True``, disables point jitter so plotted process nodes remain
        exactly aligned with their model-region coordinates.

    Returns
    -------
    PlotSpacing
        Resolution-aware plotting parameters for point jitter and parallel
        transport-arc offsets.
    """

    grid_res_deg = infer_degree_resolution(basemap_stem)
    if grid_res_deg is None:
        grid_res_deg = infer_centroid_spacing_deg(sites_gdf)

    if diagnostic_mode:
        jitter_deg = 0.0
    else:
        jitter_deg = 0.12 * grid_res_deg

    parallel_offset_m = 0.06 * grid_res_deg * 111_000
    parallel_max_offset_m = 0.25 * grid_res_deg * 111_000

    return PlotSpacing(
        diagnostic_mode=diagnostic_mode,
        grid_res_deg=grid_res_deg,
        jitter_deg=jitter_deg,
        parallel_offset_m=parallel_offset_m,
        parallel_max_offset_m=parallel_max_offset_m,
    )


def print_loaded_data_summary(
    tables: ModelTables,
    geodata: GeospatialData,
    spacing: PlotSpacing,
) -> None:
    """Print summary diagnostics for loaded mapping inputs.

    Reports resolution-aware plotting settings, model-output table size, graph
    and basemap layer sizes, and optional road-context layer counts. This is a
    console-only diagnostic used to confirm that the selected run and inferred
    geospatial inputs loaded as expected.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables used by the mapping workflow.
    geodata : GeospatialData
        Loaded graph, basemap, and optional road-context layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for jitter and transport-line
        offsets.

    Returns
    -------
    None
        This function only prints diagnostics to the console.
    """

    print("\nResolution-aware plot spacing:")
    print(f"  DIAGNOSTIC_MODE:        {spacing.diagnostic_mode}")
    print(f"  GRID_RES_DEG:           {spacing.grid_res_deg:,.4f}")
    print(f"  JITTER_DEG:             {spacing.jitter_deg:,.4f}")
    print(f"  PARALLEL_OFFSET_M:      {spacing.parallel_offset_m:,.0f}")
    print(f"  PARALLEL_MAX_OFFSET_M:  {spacing.parallel_max_offset_m:,.0f}")

    print(f"\nOutputFlowOut rows: {len(tables.flow_out):,}")
    print(f"Graph-node polygons: {len(geodata.sites):,}")
    print(f"Graph edges: {len(geodata.edges):,}")
    print(f"Basemap polygons: {len(geodata.basemap):,}")
    print(f"Native map CRS: {geodata.sites.crs}")
    print("Web map CRS: EPSG:3857")
    if geodata.road_edge_layer is not None:
        print(f"Road-enabled edge geometries: {len(geodata.road_edge_layer):,}")


# =============================================================================
# Layer preparation
# =============================================================================

def add_site_coords(
    frame: pd.DataFrame,
    sites: gpd.GeoDataFrame,
    idx_col: str = "region",
) -> pd.DataFrame:
    """Attach model-region centroid coordinates to a DataFrame.

    Copies the input table, indexes it by ``idx_col`` when needed, and joins
    ``lon`` and ``lat`` from the graph-node GeoDataFrame. Rows whose region IDs
    are not present in ``sites`` are retained with missing coordinate values.

    Parameters
    ----------
    frame : pd.DataFrame
        Table containing a model-region identifier column.
    sites : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame indexed by region ID and
        containing ``lon`` and ``lat`` columns.
    idx_col : str, default="region"
        Column in ``frame`` used to align rows with the ``sites`` index.

    Returns
    -------
    pd.DataFrame
        Copy of ``frame`` with joined ``lon`` and ``lat`` columns.
    """

    output_frame = frame.copy()
    if idx_col != output_frame.index.name:
        output_frame = output_frame.set_index(idx_col, drop=False)
    return output_frame.join(sites[["lon", "lat"]], how="left")


def slice_with_coords(
    flow_out: pd.DataFrame,
    sites: gpd.GeoDataFrame,
    tech_name: str,
) -> pd.DataFrame:
    """Build a coordinate-enriched process-flow layer for one technology.

    Filters ``OutputFlowOut`` to the requested node-level process technology,
    coerces flow values to numeric, removes flows below ``MIN_PROCESS_FLOW``,
    aggregates remaining flow by region and technology, and joins model-region
    centroid coordinates for plotting.

    Parameters
    ----------
    flow_out : pd.DataFrame
        CANOE/TEMOA ``OutputFlowOut`` table.
    sites : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame indexed by region ID and
        containing ``lon`` and ``lat`` columns.
    tech_name : str
        Process technology name to extract from ``flow_out``.

    Returns
    -------
    pd.DataFrame
        Aggregated process-flow table with ``region``, ``tech``, ``flow``,
        ``lon``, and ``lat`` columns.
    """

    tech_flow = flow_out.loc[flow_out["tech"] == tech_name].copy()
    tech_flow["flow"] = pd.to_numeric(tech_flow["flow"], errors="coerce")
    tech_flow = tech_flow.loc[tech_flow["flow"] > MIN_PROCESS_FLOW].copy()

    tech_flow = (
        tech_flow
        .groupby(["region", "tech"], as_index=False)
        .agg(flow=("flow", "sum"))
    )

    return add_site_coords(tech_flow, sites, idx_col="region")


def add_from_to_coords(
    flow_links: pd.DataFrame,
    edges: pd.DataFrame,
) -> pd.DataFrame:
    """Attach source/target graph coordinates to transport-flow records.

    Filters transport-flow records to positive flows above ``MIN_TRANSPORT_FLOW``,
    joins each transport pseudo-region to the graph-edge table, drops links
    without valid endpoint coordinates, and aggregates duplicate flow records by
    edge and technology.

    Parameters
    ----------
    flow_links : pd.DataFrame
        Transport subset of ``OutputFlowOut`` where ``region`` identifies a
        graph-edge pseudo-region.
    edges : pd.DataFrame
        Graph-edge table containing ``edge_region``, endpoint region IDs, and
        endpoint coordinates.

    Returns
    -------
    pd.DataFrame
        Aggregated transport-flow table with source/target region IDs,
        endpoint coordinates, technology name, and total flow.
    """

    link_flow = flow_links.copy()
    link_flow["flow"] = pd.to_numeric(link_flow["flow"], errors="coerce")
    link_flow = link_flow.loc[link_flow["flow"] > MIN_TRANSPORT_FLOW].copy()

    link_flow = link_flow.merge(
        edges[[
            "edge_region",
            "region_from",
            "region_to",
            "lon_from",
            "lat_from",
            "lon_to",
            "lat_to",
        ]],
        left_on="region",
        right_on="edge_region",
        how="left",
        validate="many_to_one",
    )

    link_flow = link_flow.dropna(
        subset=["lon_from", "lat_from", "lon_to", "lat_to"]
    ).copy()
    if link_flow.empty:
        return link_flow

    group_cols = [
        "region",
        "tech",
        "edge_region",
        "region_from",
        "region_to",
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
    ]

    return (
        link_flow
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )


def build_node_layers(tables: ModelTables, geodata: GeospatialData) -> dict[str, PointStyle]:
    """Build node-level process and capacity layers for plotting.

    Creates coordinate-enriched point layers for electricity generation,
    hydrogen production, methanol production, and gasoline production from
    ``OutputFlowOut``. CO2 capture is handled separately using positive
    ``CO2_CAP`` entries from ``LimitCapacity`` and is treated as a plotted
    capacity layer.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables containing output flows and capacity limits.
    geodata : GeospatialData
        Loaded graph-node geospatial data used to attach centroid coordinates.

    Returns
    -------
    dict[str, POINT_STYLE]
        Dictionary of point layers keyed by display name. Each value contains
        a coordinate-enriched DataFrame and its plotting color.
    """

    elc_gen = slice_with_coords(tables.flow_out, geodata.sites, "ELC_GEN")
    h2_plant = slice_with_coords(tables.flow_out, geodata.sites, "H2_PLANT")
    metoh_plant = slice_with_coords(tables.flow_out, geodata.sites, "METOH_PLANT")
    gsl_plant = slice_with_coords(tables.flow_out, geodata.sites, "GSL_PLANT")

    co2_cap = tables.limit_capacity.loc[
        tables.limit_capacity["tech_or_group"] == "CO2_CAP"
    ].copy()
    co2_cap["flow"] = pd.to_numeric(co2_cap["capacity"], errors="coerce")
    co2_cap = co2_cap.loc[co2_cap["flow"] > 0].copy()
    co2_cap = co2_cap.groupby("region", as_index=False).agg(flow=("flow", "sum"))
    co2_cap = add_site_coords(co2_cap, geodata.sites, idx_col="region")

    return {
        "Electricity": (elc_gen, "blue"),
        "H2": (h2_plant, "green"),
        "CO2": (co2_cap, "gray"),
        "Methanol": (metoh_plant, "orange"),
        "Gasoline": (gsl_plant, "brown"),
    }


def build_transport_layers(
    flow_out: pd.DataFrame,
    edges: pd.DataFrame,
) -> dict[str, TechStyle]:
    """Build transport-flow layers and plotting styles.

    Extracts supported pipeline, truck, and electricity-transmission
    technologies from ``OutputFlowOut``, attaches graph-edge endpoint
    coordinates, and packages each layer with its display style. Solid lines
    represent pipeline/transmission modes, while dashed lines represent truck
    transport modes.

    Parameters
    ----------
    flow_out : pd.DataFrame
        CANOE/TEMOA ``OutputFlowOut`` table containing transport-flow records.
    edges : pd.DataFrame
        Graph-edge table used to map transport pseudo-regions to source and
        target model-region coordinates.

    Returns
    -------
    dict[str, TECH_STYLE]
        Dictionary of transport layers keyed by display name. Each value
        contains the transport-flow DataFrame, plotting color, line style, and
        width factor.
    """

    h2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_PIPE"], edges)
    h2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_TRUCK"], edges)

    gsl_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_PIPE"], edges)
    gsl_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_TRUCK"], edges)

    meth_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_PIPE"], edges)
    meth_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_TRUCK"], edges)

    co2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_PIPE"], edges)
    co2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_TRUCK"], edges)

    elc_trans = add_from_to_coords(flow_out.loc[flow_out.tech == "ELC_TRANS"], edges)

    return {
        "H2 pipeline": (h2_pipe, "green", "-", 2.5),
        "H2 truck": (h2_truck, "green", "--", 1.0),
        "CO2 pipeline": (co2_pipe, "gray", "-", 2.5),
        "CO2 truck": (co2_truck, "gray", "--", 1.0),
        "Methanol pipeline": (meth_pipe, "orange", "-", 2.5),
        "Methanol truck": (meth_truck, "orange", "--", 1.0),
        "Gasoline pipeline": (gsl_pipe, "brown", "-", 2.5),
        "Gasoline truck": (gsl_truck, "brown", "--", 1.0),
        "Electricity transmission": (elc_trans, "blue", "-", 2.0),
    }


def build_demand_layer(tables: ModelTables, geodata: GeospatialData) -> tuple[pd.DataFrame, pd.Series]:
    """Build gasoline demand-point markers for plotting.

    Filters the ``Demand`` table to positive gasoline demand records
    identified by ``d_gsl``, attaches model-region centroid coordinates, and
    computes square-root-scaled marker sizes for map rendering.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables containing the demand table.
    geodata : GeospatialData
        Loaded graph-node geospatial data used to attach centroid coordinates.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series]
        Demand-point table with ``lon``, ``lat``, and ``demand`` columns, plus
        the corresponding marker-size series used for plotting.
    """

    demand_pts = tables.demand.loc[tables.demand["commodity"] == "d_gsl"].copy()
    demand_pts["demand"] = pd.to_numeric(demand_pts["demand"], errors="coerce")
    demand_pts = demand_pts.loc[demand_pts["demand"] > 0].copy()
    demand_pts = add_site_coords(demand_pts, geodata.sites, idx_col="region")
    demand_pts = demand_pts[["lon", "lat", "demand"]].dropna().copy()

    if not demand_pts.empty:
        demand_pts["demand"] = demand_pts["demand"].astype(float)
        size_demand = np.sqrt(demand_pts["demand"] / demand_pts["demand"].max()) * 500
    else:
        size_demand = pd.Series(dtype=float)

    return demand_pts, size_demand


def build_plot_layers(tables: ModelTables, geodata: GeospatialData) -> PlotLayers:
    """Build all render-ready plotting layers.

    Orchestrates node-layer, transport-layer, and demand-layer construction
    from the loaded model tables and geospatial inputs. The returned container
    is used by downstream diagnostics and figure-building functions.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables used to derive process, transport, and demand
        layers.
    geodata : GeospatialData
        Loaded geospatial graph and context data used to attach coordinates and
        edge topology.

    Returns
    -------
    PlotLayers
        Prepared node, transport, and demand layers ready for plotting.
    """

    tech_points = build_node_layers(tables, geodata)
    tech_links = build_transport_layers(tables.flow_out, geodata.edges)
    demand_pts, size_demand = build_demand_layer(tables, geodata)

    return PlotLayers(
        tech_points=tech_points,
        tech_links=tech_links,
        demand_pts=demand_pts,
        size_demand=size_demand,
    )


def print_layer_diagnostics(layers: PlotLayers) -> None:
    """Print summary diagnostics for prepared plotting layers.

    Reports the number of node-layer records, transport links, total transport
    flow by transport layer, and demand nodes. This is a console-only diagnostic
    used to check whether layer construction produced plausible plotting inputs.

    Parameters
    ----------
    layers : PlotLayers
        Prepared node, transport, and demand plotting layers.

    Returns
    -------
    None
        This function only prints diagnostics to the console.
    """

    print("\nNode layers:")
    for name, (points_df, _) in layers.tech_points.items():
        print(f"{name}: {len(points_df):,}")

    print("\nTransport layers:")
    for name, values in layers.tech_links.items():
        links = values[0]
        total_flow = links["flow"].sum() if "flow" in links.columns else 0
        print(f"{name}: {len(links):,} links, total flow = {total_flow:,.2f}")

    print(f"\nDemand nodes: {len(layers.demand_pts):,}")


# =============================================================================
# Context plotting
# =============================================================================

def plot_context_layers_schematic(
    ax: Axes,
    geodata: GeospatialData,
) -> None:
    """Plot context layers in the selected graph's native CRS.

    Draws only processed Canada/province boundaries in the graph-node CRS.
    This supports both geographic degree grids and projected kilometre grids
    without mixing coordinate units.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the context layers are drawn.
    geodata : GeospatialData
        Loaded basemap, model-region, and optional road-context layers.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    geodata.basemap.plot(
        ax=ax,
        color="none",
        edgecolor="0.70",
        linewidth=0.5,
        alpha=0.7,
        zorder=0,
    )



def plot_context_layers_web(
    ax: Axes,
    geodata: GeospatialData,
) -> None:
    """Plot geographic context layers in Web Mercator coordinates.

    Reprojects processed Canada/province polygons to EPSG:3857 before
    drawing them on an existing
    Matplotlib axis. This prepares the context layers to align with optional
    web-tile basemaps added by Contextily.

    Parameters
    ----------
    ax : Axes
        Matplotlib axis on which projected context layers are drawn.
    geodata : GeospatialData
        Loaded basemap, model-region, and optional road-context layers.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    basemap_web = geodata.basemap.to_crs(epsg=3857)
    basemap_web.plot(
        ax=ax,
        color="none",
        edgecolor="0.55",
        linewidth=0.5,
        alpha=0.75,
        zorder=1,
    )



# =============================================================================
# Transport plotting
# =============================================================================

def combined_transport_links(
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Combine transport layers and offset parallel corridor geometries.

    Concatenates positive transport-flow layers, validates required endpoint
    coordinate columns, aggregates duplicate edge/technology records, and
    assigns a canonical undirected corridor key for each source-target pair.
    Where multiple transport technologies use the same corridor, parallel
    offsets are assigned so overlapping routes can be visually separated.

    Line geometries are first constructed in EPSG:4326 from endpoint
    lon/lat coordinates, then reprojected to EPSG:3857 before metre-scale
    offsets are applied.

    Parameters
    ----------
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters controlling parallel route offsets.

    Returns
    -------
    gpd.GeoDataFrame
        Combined transport-flow GeoDataFrame in EPSG:3857 with offset line
        geometries and plotting metadata. Returns an empty GeoDataFrame if no
        valid positive transport links are available.
    """

    frames = []

    for display_name, values in tech_links.items():
        links, color, linestyle, width_factor = values
        if links.empty:
            continue

        required_cols = [
            "region",
            "tech",
            "region_from",
            "region_to",
            "lon_from",
            "lat_from",
            "lon_to",
            "lat_to",
            "flow",
        ]
        missing_cols = [col for col in required_cols if col not in links.columns]
        if missing_cols:
            print(f"Skipping {display_name}; missing columns: {missing_cols}")
            continue

        layer = links[required_cols].dropna().copy()
        layer["flow"] = pd.to_numeric(layer["flow"], errors="coerce")
        layer = layer.loc[layer["flow"] > 0].copy()
        if layer.empty:
            continue

        layer["display_name"] = display_name
        layer["color"] = color
        layer["linestyle"] = linestyle
        layer["width_factor"] = width_factor

        layer["route_a"] = layer[["region_from", "region_to"]].min(axis=1)
        layer["route_b"] = layer[["region_from", "region_to"]].max(axis=1)
        layer["route_key"] = layer["route_a"] + "__" + layer["route_b"]
        layer["is_reversed_from_canonical"] = layer["region_from"] != layer["route_a"]

        frames.append(layer)

    if not frames:
        return gpd.GeoDataFrame(columns=[])

    combined = pd.concat(frames, ignore_index=True)

    group_cols = [
        "region",
        "tech",
        "display_name",
        "color",
        "linestyle",
        "width_factor",
        "region_from",
        "region_to",
        "route_a",
        "route_b",
        "route_key",
        "is_reversed_from_canonical",
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
    ]
    combined = (
        combined
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )

    tech_order = {
        "Electricity transmission": 0,
        "H2 pipeline": 1,
        "CO2 pipeline": 2,
        "Methanol pipeline": 3,
        "Gasoline pipeline": 4,
        "H2 truck": 5,
        "CO2 truck": 6,
        "Methanol truck": 7,
        "Gasoline truck": 8,
    }
    combined["plot_order"] = combined["display_name"].map(tech_order).fillna(999)
    combined = combined.sort_values(
        ["route_key", "plot_order", "display_name"]
    ).copy()

    combined["parallel_rank"] = combined.groupby("route_key").cumcount()
    combined["parallel_count"] = combined.groupby("route_key")["display_name"].transform("count")

    raw_offset = (
        combined["parallel_rank"] - (combined["parallel_count"] - 1) / 2.0
    ) * spacing.parallel_offset_m
    combined["offset_m"] = raw_offset.clip(
        -spacing.parallel_max_offset_m,
        spacing.parallel_max_offset_m,
    )

    combined.loc[combined["is_reversed_from_canonical"], "offset_m"] *= -1

    base_geom = [
        LineString([(row.lon_from, row.lat_from), (row.lon_to, row.lat_to)])
        for row in combined.itertuples(index=False)
    ]

    transport_gdf = gpd.GeoDataFrame(combined, geometry=base_geom, crs="EPSG:4326")
    transport_gdf_3857 = transport_gdf.to_crs(epsg=3857)

    offset_geoms = []
    for row in transport_gdf_3857.itertuples(index=False):
        coords = list(row.geometry.coords)
        x1, y1 = coords[0]
        x2, y2 = coords[-1]
        dx = x2 - x1
        dy = y2 - y1
        length = float(np.hypot(dx, dy))

        if length == 0 or not np.isfinite(length):
            offset_geoms.append(row.geometry)
            continue

        ux = -dy / length
        uy = dx / length

        offset = float(row.offset_m)
        offset_geoms.append(
            LineString([
                (x1 + ux * offset, y1 + uy * offset),
                (x2 + ux * offset, y2 + uy * offset),
            ])
        )

    transport_gdf_3857 = transport_gdf_3857.copy()
    transport_gdf_3857["geometry"] = offset_geoms

    return transport_gdf_3857


def summarize_parallel_corridors(
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Print diagnostics for corridors with parallel transport layers.

    Builds the combined transport GeoDataFrame, summarizes active corridors by
    canonical route key, and reports how many corridors carry more than one
    transport technology or mode. The returned GeoDataFrame can be reused for
    plotting or further corridor diagnostics.

    Parameters
    ----------
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used to construct offset transport
        geometries.

    Returns
    -------
    gpd.GeoDataFrame
        Combined transport-flow GeoDataFrame returned by
        ``combined_transport_links``. Returns an empty GeoDataFrame if no
        positive transport links are available.
    """

    transport_gdf = combined_transport_links(tech_links, spacing)
    if transport_gdf.empty:
        print("\nParallel corridor diagnostics: no positive transport links.")
        return transport_gdf

    corridor_summary = (
        transport_gdf
        .groupby("route_key", as_index=False)
        .agg(
            n_parallel_layers=("display_name", "count"),
            technologies=("display_name", lambda x: ", ".join(sorted(set(x)))),
            total_flow=("flow", "sum"),
        )
        .sort_values(["n_parallel_layers", "total_flow"], ascending=[False, False])
    )

    multiplex = corridor_summary.loc[
        corridor_summary["n_parallel_layers"] > 1
    ].copy()

    print("\nParallel corridor diagnostics:")
    print(f"Active transport corridor-tech rows: {len(transport_gdf):,}")
    print(f"Unique active corridors: {len(corridor_summary):,}")
    print(f"Corridors with >1 active transport layer: {len(multiplex):,}")

    if not multiplex.empty:
        print("\nTop multiplex corridors:")
        print(multiplex.head(10).to_string(index=False))

    return transport_gdf


def plot_transport_lines_schematic(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
    target_crs,
) -> None:
    """Plot transport-flow lines in the graph layer's native coordinate system.

    Transport links are first combined into a Web Mercator GeoDataFrame so any
    configured parallel-corridor offsets can be applied consistently in metres.
    The resulting geometries are reprojected to ``target_crs`` and plotted by
    display layer. Line widths are scaled within each layer using the square root
    of flow relative to that layer's maximum positive flow.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the transport lines are drawn.
    tech_links : dict[str, TECH_STYLE]
        Transport-flow layers keyed by display name. Each value contains the link
        DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting settings used when combining and offsetting
        parallel transport links.
    target_crs
        Coordinate reference system used by the destination plot axes. Accepts
        any CRS representation supported by GeoPandas.

    Returns
    -------
    None
    """

    transport_gdf_3857 = combined_transport_links(
        tech_links,
        spacing,
    )

    if transport_gdf_3857.empty:
        return

    transport_gdf = transport_gdf_3857.to_crs(target_crs)

    for _, group in transport_gdf.groupby(
        "display_name",
        sort=False,
    ):
        flow_values = pd.to_numeric(
            group["flow"],
            errors="coerce",
        ).to_numpy(dtype=float)

        valid_flows = flow_values[
            np.isfinite(flow_values) & (flow_values > 0)
        ]

        if valid_flows.size == 0:
            continue

        max_flow = float(valid_flows.max())

        for row in group.itertuples(index=False):
            geometry = row.geometry

            if not isinstance(geometry, LineString):
                raise TypeError(
                    "Transport layers must contain LineString geometries, "
                    f"not {type(geometry).__name__}."
                )

            flow_value = pd.to_numeric(
                row.flow,
                errors="coerce",
            )

            if pd.isna(flow_value):
                continue

            flow = float(np.asarray(flow_value, dtype=float).item())

            if not np.isfinite(flow) or flow <= 0:
                continue

            width_factor_value = pd.to_numeric(
                row.width_factor,
                errors="coerce",
            )

            if pd.isna(width_factor_value):
                continue

            width_factor = float(
                np.asarray(
                    width_factor_value,
                    dtype=float,
                ).item()
            )

            x_vals, y_vals = geometry.xy

            linewidth = width_factor * (
                0.5
                + 2.5
                * np.sqrt(flow / max_flow)
            )

            ax.plot(
                x_vals,
                y_vals,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=linewidth,
                alpha=0.78,
                zorder=20,
            )


def plot_transport_lines_web(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> None:
    """Plot transport-flow lines in EPSG:3857 Web Mercator.

    Builds combined offset transport geometries and draws each transport layer
    directly in Web Mercator coordinates. Line widths are scaled within each
    displayed transport layer using the square root of relative positive flow.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the transport lines are drawn.
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used to separate parallel
        transport arcs.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    transport_gdf_3857 = combined_transport_links(
        tech_links,
        spacing,
    )

    if transport_gdf_3857.empty:
        return

    for _, group in transport_gdf_3857.groupby(
        "display_name",
        sort=False,
    ):
        flow_values = pd.to_numeric(
            group["flow"],
            errors="coerce",
        ).to_numpy(dtype=float)

        valid_flows = flow_values[
            np.isfinite(flow_values) & (flow_values > 0)
        ]

        if valid_flows.size == 0:
            continue

        max_flow = float(valid_flows.max())

        for row in group.itertuples(index=False):
            geometry = row.geometry

            if not isinstance(geometry, LineString):
                raise TypeError(
                    "Transport layers must contain LineString geometries, "
                    f"not {type(geometry).__name__}."
                )

            flow_value = pd.to_numeric(
                row.flow,
                errors="coerce",
            )

            if pd.isna(flow_value):
                continue

            flow = float(
                np.asarray(
                    flow_value,
                    dtype=float,
                ).item()
            )

            if not np.isfinite(flow) or flow <= 0:
                continue

            width_factor_value = pd.to_numeric(
                row.width_factor,
                errors="coerce",
            )

            if pd.isna(width_factor_value):
                continue

            width_factor = float(
                np.asarray(
                    width_factor_value,
                    dtype=float,
                ).item()
            )

            if not np.isfinite(width_factor) or width_factor <= 0:
                continue

            x_vals, y_vals = geometry.xy

            linewidth = width_factor * (
                0.5
                + 2.5
                * np.sqrt(flow / max_flow)
            )

            ax.plot(
                x_vals,
                y_vals,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=linewidth,
                alpha=0.72,
                zorder=20,
            )


# =============================================================================
# Point and legend plotting
# =============================================================================

def build_legend(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    bbox_to_anchor: tuple[float, float],
    fontsize: int | None = None,
    loc: str = "upper right",
) -> None:
    """Build a combined legend for context, point, and transport layers.

    Collects existing legend handles from the axis, adds proxy handles for
    transport technologies, and adds proxy handles for geographic context
    layers. Optional road-context legend entries are included only when the
    corresponding geospatial layers are available.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes whose legend is updated.
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value provides the color,
        line style, and width factor used to create legend proxies.
    geodata : GeospatialData
        Loaded geospatial context used to determine whether optional road-layer
        legend entries should be included.
    bbox_to_anchor : tuple[float, float]
        Anchor position passed to ``ax.legend`` for legend placement.
    fontsize : int | None, default=None
        Optional legend font size.
    loc : str, default="upper right"
        Legend location passed to ``ax.legend``.

    Returns
    -------
    None
        This function updates the legend on ``ax``.
    """

    handles, labels = ax.get_legend_handles_labels()

    labelled_handles: dict[str, object] = {
        str(label): handle
        for label, handle in zip(labels, handles)
        if str(label)
    }

    transport_proxies = [
        Line2D(
            [0],
            [0],
            color=values[1],
            lw=1.5 * values[3],
            linestyle=values[2],
            label=name,
        )
        for name, values in tech_links.items()
    ]

    context_proxies = [
        Line2D(
            [0],
            [0],
            color="0.70",
            lw=0.8,
            label="Canada/province polygons",
        ),
    ]

    combined_handles = [
        *context_proxies,
        *labelled_handles.values(),
        *transport_proxies,
    ]

    combined_labels = [
        str(proxy.get_label())
        for proxy in context_proxies
    ] + [
        *labelled_handles.keys(),
    ] + [
        str(proxy.get_label())
        for proxy in transport_proxies
    ]

    unique_legend_entries = dict(
        zip(
            combined_labels,
            combined_handles,
        )
    )

    legend_labels: list[str] = list(unique_legend_entries.keys())
    legend_handles = list(unique_legend_entries.values())

    ax.legend(
        legend_handles,
        legend_labels,
        title="Legend",
        frameon=False,
        loc=loc,
        bbox_to_anchor=bbox_to_anchor,
        fontsize=fontsize,
    )


def plot_points_native(
    ax: Axes,
    tech_points: dict[str, PointStyle],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
    target_crs,
) -> None:
    """Plot demand and process-point layers in the graph layer's native CRS.

    Demand coordinates are converted from WGS84 longitude and latitude to
    ``target_crs`` and plotted using marker sizes supplied by ``size_demand``.
    Process points are filtered to positive finite flows, assigned deterministic
    coordinate jitter to reduce marker overlap, reprojected to ``target_crs``, and
    scaled relative to the maximum flow within each technology layer.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the point layers are drawn.
    tech_points : dict[str, POINT_STYLE]
        Process-point layers keyed by display name. Each value contains a point
        DataFrame and plotting color.
    demand_pts : pd.DataFrame
        Demand-point table containing longitude and latitude coordinates.
    size_demand : pd.Series
        Demand marker sizes indexed consistently with ``demand_pts``.
    spacing : PlotSpacing
        Resolution-aware plotting settings containing the process-point jitter
        distance in decimal degrees.
    target_crs
        Coordinate reference system used by the destination plot axes. Accepts any
        CRS representation supported by GeoPandas.

    Returns
    -------
    None
    """

    if not demand_pts.empty:
        demand_gdf = build_point_geodataframe(
            demand_pts,
            target_crs=target_crs,
        )
        ax.scatter(
            demand_gdf.geometry.x,
            demand_gdf.geometry.y,
            s=size_demand.loc[demand_gdf.index] * 0.3,
            linewidth=1.5,
            alpha=1,
            marker="x",
            label="Demand",
            c="black",
            zorder=30,
        )

    rng = np.random.default_rng(42)

    for name, (points_df, color) in tech_points.items():
        if points_df.empty or "flow" not in points_df.columns:
            continue

        points_plot = points_df[["lon", "lat", "flow"]].dropna().copy()
        points_plot["flow"] = pd.to_numeric(
            points_plot["flow"],
            errors="coerce",
        )
        points_plot = points_plot.loc[
            points_plot["flow"] > 0
        ].dropna(subset=["flow"])

        if points_plot.empty:
            continue

        points_plot["lon_plot"] = points_plot["lon"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_plot),
        )
        points_plot["lat_plot"] = points_plot["lat"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_plot),
        )

        points_gdf = build_point_geodataframe(
            points_plot,
            target_crs=target_crs,
            lon_col="lon_plot",
            lat_col="lat_plot",
        )
        sizes = np.sqrt(
            points_gdf["flow"] / points_gdf["flow"].max()
        ) * 150

        ax.scatter(
            points_gdf.geometry.x,
            points_gdf.geometry.y,
            s=sizes,
            alpha=0.65,
            label=name,
            c=color,
            zorder=30,
        )


def plot_points_web(
    ax: Axes,
    tech_points: dict[str, PointStyle],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
) -> None:
    """Plot demand and process-point layers in Web Mercator coordinates.

    Converts demand and process-point coordinates from EPSG:4326 to EPSG:3857
    before drawing them on a web-map axis. Demand markers are plotted at their
    model-region centroid coordinates, while process markers receive
    reproducible random jitter before reprojection to reduce visual overlap.
    Process marker sizes are square-root-scaled within each technology layer
    using relative positive flow magnitude.

    Parameters
    ----------
    ax : Axes
        Matplotlib axis on which projected point layers are drawn.
    tech_points : dict[str, POINT_STYLE]
        Process-point layers keyed by display name. Each value contains a
        coordinate-enriched DataFrame and plotting color.
    demand_pts : pd.DataFrame
        Demand-point table containing ``lon``, ``lat``, and ``demand`` columns.
    size_demand : pd.Series
        Precomputed demand marker sizes.
    spacing : PlotSpacing
        Resolution-aware plotting parameters controlling process-point jitter.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    if not demand_pts.empty:
        demand_pts_gdf = gpd.GeoDataFrame(
            demand_pts,
            geometry=gpd.points_from_xy(demand_pts["lon"], demand_pts["lat"]),
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        ax.scatter(
            demand_pts_gdf.geometry.x,
            demand_pts_gdf.geometry.y,
            s=size_demand * 0.3,
            color="black",
            alpha=1,
            marker="x",
            linewidths=1.2,
            label="Demand",
            zorder=30,
        )

    rng = np.random.default_rng(42)

    for name, (points_df, color) in tech_points.items():
        if points_df.empty or "flow" not in points_df.columns:
            continue

        points_df = points_df.dropna(subset=["lon", "lat", "flow"]).copy()
        if points_df.empty:
            continue

        points_df["flow"] = pd.to_numeric(points_df["flow"], errors="coerce")
        points_df = points_df.dropna(subset=["flow"])
        points_df = points_df.loc[points_df["flow"] > 0].copy()
        if points_df.empty:
            continue

        points_df["lon_plot"] = points_df["lon"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_df),
        )
        points_df["lat_plot"] = points_df["lat"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_df),
        )

        points_gdf = gpd.GeoDataFrame(
            points_df,
            geometry=gpd.points_from_xy(points_df["lon_plot"], points_df["lat_plot"]),
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        sizes = np.sqrt(points_df["flow"] / points_df["flow"].max()) * 90

        ax.scatter(
            points_gdf.geometry.x,
            points_gdf.geometry.y,
            s=sizes,
            color=color,
            alpha=0.65,
            label=name,
            zorder=30,
        )


# =============================================================================
# Figure builders
# =============================================================================

def save_polygon_context_figure(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build, display, and save the polygon-context schematic figure.

    Creates a native-CRS schematic map showing Canada/province boundaries,
    region centroids, process and demand points, and transport-flow lines. The
    figure is saved as a PNG in the selected run's figure directory.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded basemap, model-region, graph, and optional road-context layers.
    layers : PlotLayers
        Prepared process-point, demand-point, and transport-flow plotting
        layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for point jitter and
        transport-line offsets.
    paths : GeospatialPaths
        Resolved geospatial paths and figure-output naming metadata.

    Returns
    -------
    Path
        Path to the saved polygon-context PNG figure.
    """

    fig, ax = plt.subplots(figsize=(10, 6))

    plot_context_layers_schematic(ax, geodata)

    target_crs = native_plot_crs(geodata)
    site_centroids = geodata.sites.geometry.centroid

    ax.scatter(
        site_centroids.x,
        site_centroids.y,
        s=3,
        alpha=0.12,
        label="Region centroid",
        c="gray",
        zorder=10,
    )

    plot_points_native(
        ax,
        layers.tech_points,
        layers.demand_pts,
        layers.size_demand,
        spacing,
        target_crs,
    )

    plot_transport_lines_schematic(
        ax,
        layers.tech_links,
        spacing,
        target_crs,
    )

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
    )

    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")

    sns.despine(
        ax=ax,
        top=True,
        right=True,
        bottom=True,
        left=True,
    )

    fig.tight_layout()

    epsg_code = target_crs.to_epsg()
    crs_tag = (
        f"epsg{epsg_code}"
        if epsg_code is not None
        else "native_crs"
    )

    fig_path = (
        paths.figure_dir
        / f"{paths.fig_stem}_polygon_context_{crs_tag}.png"
    )

    fig.savefig(
        fig_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)

    print(f"Saved polygon-context figure: {fig_path}")

    return fig_path


def save_basemap_overlay_figure(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build, display, and save the web-map basemap overlay figure.

    Creates an EPSG:3857 map showing projected basemap/context layers,
    optional Contextily web tiles, model-region centroids, process and demand
    points, and transport-flow lines. If web-tile loading fails, the figure is
    still generated using local geospatial layers only. The figure is saved as
    an SVG in the selected run's figure directory.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded basemap, model-region, graph, and optional road-context layers.
    layers : PlotLayers
        Prepared process-point, demand-point, and transport-flow plotting
        layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for point jitter and
        transport-line offsets.
    paths : GeospatialPaths
        Resolved geospatial paths and figure-output naming metadata.

    Returns
    -------
    Path
        Path to the saved basemap polygon-overlay SVG figure.
    """

    fig, ax = plt.subplots(figsize=(9, 10))

    plot_context_layers_web(ax, geodata)

    if PLOT_WEB_TILES:
        try:
            osm_provider = providers.query_name(
                "OpenStreetMap.Mapnik"
            )

            ctx.add_basemap(
                ax,
                source=osm_provider,
                zorder=0,
            )
        except (
            requests.RequestException,
            URLError,
            RasterioError,
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover - web service dependent
            print(
                "Contextily basemap failed; continuing with local layers only. "
                f"Reason: {exc}"
            )

    sites_web_points = gpd.GeoDataFrame(
        geodata.sites.copy(),
        geometry=gpd.points_from_xy(
            geodata.sites["lon"],
            geodata.sites["lat"],
        ),
        crs="EPSG:4326",
    ).to_crs(epsg=3857)

    sites_web_points.plot(
        ax=ax,
        color="gray",
        markersize=2,
        alpha=0.12,
        label="Region centroid",
        zorder=10,
    )

    plot_points_web(
        ax,
        layers.tech_points,
        layers.demand_pts,
        layers.size_demand,
        spacing,
    )

    plot_transport_lines_web(
        ax,
        layers.tech_links,
        spacing,
    )

    ax.set_title("", fontsize=14, pad=12)
    ax.grid(False)

    for spine in ax.spines.values():
        spine.set_visible(False)

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        bbox_to_anchor=(1.35, 1.0),
        fontsize=10,
    )

    ax.set_xticks([])
    ax.set_yticks([])

    fig.tight_layout()

    fig_path = (
        paths.figure_dir
        / f"{paths.fig_stem}_basemap_polygon_overlay.svg"
    )

    fig.savefig(
        fig_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)

    print(f"Saved basemap polygon-overlay figure: {fig_path}")

    return fig_path


# =============================================================================
# Main workflow
# =============================================================================

def main() -> None:
    """Run the interactive CANOE/TEMOA map-generation workflow.

    Resolves project paths, prompts the user to select a model run and SQLite
    database, infers matching geospatial inputs, loads model/geospatial data,
    builds render-ready plotting layers, prints diagnostics, and saves both
    the polygon-context and basemap-overlay figures.

    Returns
    -------
    None
        This function coordinates the CLI workflow and writes figure files to
        the selected run directory.
    """

    project_paths = resolve_project_paths()
    selected_run = select_run_and_database(project_paths.output_root)
    geospatial_paths = infer_geospatial_paths(project_paths.data_files, selected_run)

    tables = load_model_tables(selected_run.db_path)
    geodata = load_geospatial_data(geospatial_paths)
    spacing = configure_resolution_aware_spacing(
        geospatial_paths.basemap_stem,
        geodata.sites,
        diagnostic_mode=False,
    )

    print_loaded_data_summary(tables, geodata, spacing)

    layers = build_plot_layers(tables, geodata)
    print_layer_diagnostics(layers)

    save_polygon_context_figure(geodata, layers, spacing, geospatial_paths)
    save_basemap_overlay_figure(geodata, layers, spacing, geospatial_paths)


if __name__ == "__main__":
    main()
