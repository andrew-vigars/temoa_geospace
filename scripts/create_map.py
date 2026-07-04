# =============================================================================
# create_map.py
#
# Decode CANOE/TEMOA geospatial output flows and plot transport routes.
# This version uses the matching graph-node polygons, processed basemap polygons,
# and road-overlay/connectivity layers as geographic context behind model outputs.
#
# Structural refactor notes:
# - Plot formatting is intentionally preserved from the original script.
# - Top-level execution is moved into main().
# - Data objects are passed explicitly instead of accessed through globals.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import TypeAlias

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D
from shapely.geometry import LineString

# =============================================================================
# Project import path
# =============================================================================

def find_project_root() -> Path:
    """Return repository root from script location or current working directory."""
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
PLOT_ROAD_OVERLAY = True
PLOT_ROAD_EDGE_LAYER = True
PLOT_PARALLEL_TRANSPORT_ARCS = True

TECH_STYLE: TypeAlias = tuple[pd.DataFrame, str, str, float]
POINT_STYLE: TypeAlias = tuple[pd.DataFrame, str]


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project-level paths."""

    project_root: Path
    output_root: Path
    data_files: Path


@dataclass(frozen=True)
class SelectedRun:
    """Selected model run and database."""

    run_dir: Path
    db_path: Path


@dataclass(frozen=True)
class GeospatialPaths:
    """Geospatial input paths inferred from a selected database."""

    basemap_stem: str
    node_path: Path
    edge_path: Path
    basemap_path: Path
    road_overlay_path: Path | None
    road_edge_gpkg_path: Path | None
    figure_dir: Path
    fig_stem: str


@dataclass(frozen=True)
class PlotSpacing:
    """Resolution-aware plotting offsets and jitter."""

    diagnostic_mode: bool
    grid_res_deg: float
    jitter_deg: float
    parallel_offset_m: float
    parallel_max_offset_m: float


@dataclass
class ModelTables:
    """CANOE/TEMOA output and input tables needed for mapping."""

    flow_out: pd.DataFrame
    demand: pd.DataFrame
    limit_capacity: pd.DataFrame


@dataclass
class GeospatialData:
    """Loaded geospatial context for mapping."""

    sites: gpd.GeoDataFrame
    edges: pd.DataFrame
    basemap: gpd.GeoDataFrame
    road_overlay: gpd.GeoDataFrame | None
    road_edge_layer: gpd.GeoDataFrame | None


@dataclass
class PlotLayers:
    """Prepared node, transport, and demand layers."""

    tech_points: dict[str, POINT_STYLE]
    tech_links: dict[str, TECH_STYLE]
    demand_pts: pd.DataFrame
    size_demand: pd.Series


# =============================================================================
# Project and run selection
# =============================================================================

def resolve_project_paths() -> ProjectPaths:
    """Resolve project root from either repository root or scripts directory."""

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
    """Interactively select a model run folder containing a SQLite database."""

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
    """Interactively select a SQLite database from the selected run folder."""

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
    """Select a model run and database."""

    run_dir = select_model_run(output_root)
    db_path = select_sqlite_database(run_dir)
    return SelectedRun(run_dir=run_dir, db_path=db_path)


# =============================================================================
# Path inference and validation
# =============================================================================

def infer_basemap_stem(db_path: Path) -> str:
    """Infer basemap stem from selected CANOE geospatial database or run name."""

    search_texts = [
        db_path.stem,
        db_path.parent.name,
    ]

    for text in search_texts:
        text = re.sub(r"^(input_|solved_)", "", text)
        text = re.sub(r"^CANOE_geospatial_", "", text)

        match = re.search(
            r"canada_basemap_\d+(?:\.\d+)?deg_(?:centroid|intersects)",
            text,
        )

        if match:
            return match.group(0)

    print("Could not infer basemap stem.")
    print(f"  Database filename: {db_path.stem}")
    print(f"  Run folder:        {db_path.parent.name}")
    sys.exit(1)


def build_figure_stem(selected_run: SelectedRun) -> str:
    """Build output figure filename stem from run and database names."""

    run_tag = selected_run.run_dir.name.replace(" ", "_")
    db_tag = selected_run.db_path.stem

    if db_tag.startswith("CANOE_geospatial_"):
        db_tag = db_tag[len("CANOE_geospatial_"):]

    return run_tag if db_tag in run_tag else f"{run_tag}_{db_tag}"


def infer_geospatial_paths(
    data_files: Path,
    selected_run: SelectedRun,
) -> GeospatialPaths:
    """Infer all geospatial inputs from a selected database."""

    basemap_stem = infer_basemap_stem(selected_run.db_path)

    graph_dir = data_files / "processed" / "graph"
    basemap_dir = data_files / "processed" / "basemaps"
    road_connectivity_dir = data_files / "processed" / "road_connectivity"

    node_path = graph_dir / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = graph_dir / f"{basemap_stem}_graph_edges.csv"
    basemap_path = basemap_dir / f"{basemap_stem}.gpkg"

    road_overlay_candidates = sorted(
        road_connectivity_dir.glob(f"*__{basemap_stem}_graph_nodes*_road_overlay.gpkg")
    )
    road_edge_candidates = sorted(
        road_connectivity_dir.glob(f"*__{basemap_stem}_graph_nodes*_road_edges.gpkg")
    )

    road_overlay_path = road_overlay_candidates[0] if road_overlay_candidates else None
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
        road_overlay_path=road_overlay_path,
        road_edge_gpkg_path=road_edge_gpkg_path,
        figure_dir=figure_dir,
        fig_stem=build_figure_stem(selected_run),
    )

    print(f"\nInferred basemap stem: {paths.basemap_stem}")
    print(f"  Graph nodes:       {paths.node_path.name}")
    print(f"  Graph edges:       {paths.edge_path.name}")
    print(f"  Basemap polygons:  {paths.basemap_path.name}")
    print(
        "  Road overlay:      "
        f"{paths.road_overlay_path.name if paths.road_overlay_path else 'not found'}"
    )
    print(
        "  Road edge layer:   "
        f"{paths.road_edge_gpkg_path.name if paths.road_edge_gpkg_path else 'not found'}"
    )

    return paths


# =============================================================================
# Data loading and spacing
# =============================================================================

def load_model_tables(db_path: Path) -> ModelTables:
    """Load model output and input tables used by the mapping workflow."""

    db_tables = mgmt.sqlite_to_dfs(str(db_path))

    return ModelTables(
        flow_out=db_tables["OutputFlowOut"].copy(),
        demand=db_tables["Demand"].copy(),
        limit_capacity=db_tables["LimitCapacity"].copy(),
    )


def load_geospatial_data(paths: GeospatialPaths) -> GeospatialData:
    """Load graph nodes, graph edges, basemap, and optional road context layers."""

    sites = gpd.read_file(paths.node_path)
    edges = pd.read_csv(paths.edge_path)
    basemap = gpd.read_file(paths.basemap_path)

    road_overlay = None
    if paths.road_overlay_path and paths.road_overlay_path.exists():
        road_overlay = gpd.read_file(paths.road_overlay_path)

    road_edge_layer = None
    if paths.road_edge_gpkg_path and paths.road_edge_gpkg_path.exists():
        road_edge_layer = gpd.read_file(paths.road_edge_gpkg_path)

    sites["region"] = sites["region"].astype(str)
    sites["site_id"] = sites["region"]
    sites = sites.set_index("region", drop=True)

    edges["edge_region"] = edges["edge_region"].astype(str)
    edges["region_from"] = edges["region_from"].astype(str)
    edges["region_to"] = edges["region_to"].astype(str)

    if basemap.crs != sites.crs:
        basemap = basemap.to_crs(sites.crs)
    if road_overlay is not None and road_overlay.crs != sites.crs:
        road_overlay = road_overlay.to_crs(sites.crs)
    if road_edge_layer is not None and road_edge_layer.crs != sites.crs:
        road_edge_layer = road_edge_layer.to_crs(sites.crs)

    return GeospatialData(
        sites=sites,
        edges=edges,
        basemap=basemap,
        road_overlay=road_overlay,
        road_edge_layer=road_edge_layer,
    )


def infer_degree_resolution(basemap_stem: str) -> float | None:
    """Infer degree grid resolution from names like canada_basemap_1deg_intersects."""

    match = re.search(r"_(\d+(?:\.\d+)?)deg(?:_|$)", basemap_stem)
    return float(match.group(1)) if match else None


def infer_centroid_spacing_deg(sites_gdf: gpd.GeoDataFrame) -> float:
    """Fallback spacing from model-region centroid coordinates."""

    lon_vals = np.sort(
        pd.to_numeric(sites_gdf["lon"], errors="coerce").dropna().unique()
    )
    lat_vals = np.sort(
        pd.to_numeric(sites_gdf["lat"], errors="coerce").dropna().unique()
    )

    lon_step = np.median(np.diff(lon_vals)) if len(lon_vals) > 1 else np.nan
    lat_step = np.median(np.diff(lat_vals)) if len(lat_vals) > 1 else np.nan

    candidates = [
        abs(step)
        for step in [lon_step, lat_step]
        if np.isfinite(step) and abs(step) > 0
    ]
    return min(candidates) if candidates else 1.0


def configure_resolution_aware_spacing(
    basemap_stem: str,
    sites_gdf: gpd.GeoDataFrame,
    diagnostic_mode: bool = False,
) -> PlotSpacing:
    """Return resolution-aware node jitter and transport arc offsets."""

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
    """Print loaded table counts and resolution-aware plotting settings."""

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
    if geodata.road_overlay is not None:
        print(f"Road-overlay geometries: {len(geodata.road_overlay):,}")
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
    """Join lon/lat model-region coordinates onto a table."""

    output_frame = frame.copy()
    if idx_col != output_frame.index.name:
        output_frame = output_frame.set_index(idx_col, drop=False)
    return output_frame.join(sites[["lon", "lat"]], how="left")


def slice_with_coords(
    flow_out: pd.DataFrame,
    sites: gpd.GeoDataFrame,
    tech_name: str,
) -> pd.DataFrame:
    """Aggregate positive node-level process output and attach lon/lat coordinates."""

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
    """Attach graph edge coordinates to positive transport flows."""

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


def build_node_layers(tables: ModelTables, geodata: GeospatialData) -> dict[str, POINT_STYLE]:
    """Build node-level plotting layers."""

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
) -> dict[str, TECH_STYLE]:
    """Build transport plotting layers."""

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
    """Build gasoline demand markers and marker sizes."""

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
    """Build all node, transport, and demand plotting layers."""

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
    """Print node, transport, and demand layer diagnostics."""

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
    ax: plt.Axes,
    geodata: GeospatialData,
) -> None:
    """Plot basemap polygon boundaries and model-region polygons in lon/lat."""

    geodata.basemap.plot(
        ax=ax,
        color="none",
        edgecolor="0.70",
        linewidth=0.5,
        alpha=0.7,
        zorder=0,
    )

    geodata.sites.plot(
        ax=ax,
        color="none",
        edgecolor="0.85",
        linewidth=0.25,
        alpha=0.75,
        zorder=1,
    )

    if PLOT_ROAD_OVERLAY and geodata.road_overlay is not None:
        geodata.road_overlay.plot(
            ax=ax,
            color="0.35",
            linewidth=0.15,
            alpha=0.10,
            zorder=2,
        )

    if PLOT_ROAD_EDGE_LAYER and geodata.road_edge_layer is not None:
        geodata.road_edge_layer.plot(
            ax=ax,
            color="0.15",
            linewidth=0.35,
            alpha=0.30,
            zorder=3,
        )


def plot_context_layers_web(
    ax: plt.Axes,
    geodata: GeospatialData,
) -> None:
    """Plot context layers in EPSG:3857 for optional web-tile background."""

    basemap_web = geodata.basemap.to_crs(epsg=3857)
    sites_web_poly = geodata.sites.to_crs(epsg=3857)

    basemap_web.plot(
        ax=ax,
        color="none",
        edgecolor="0.55",
        linewidth=0.5,
        alpha=0.75,
        zorder=1,
    )

    sites_web_poly.plot(
        ax=ax,
        color="none",
        edgecolor="0.80",
        linewidth=0.25,
        alpha=0.75,
        zorder=2,
    )

    if PLOT_ROAD_OVERLAY and geodata.road_overlay is not None:
        geodata.road_overlay.to_crs(epsg=3857).plot(
            ax=ax,
            color="0.25",
            linewidth=0.15,
            alpha=0.12,
            zorder=3,
        )

    if PLOT_ROAD_EDGE_LAYER and geodata.road_edge_layer is not None:
        geodata.road_edge_layer.to_crs(epsg=3857).plot(
            ax=ax,
            color="0.10",
            linewidth=0.35,
            alpha=0.35,
            zorder=4,
        )


# =============================================================================
# Transport plotting
# =============================================================================

def combined_transport_links(
    tech_links: dict[str, TECH_STYLE],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Combine transport layers and assign one parallel offset per corridor/technology."""

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
    tech_links: dict[str, TECH_STYLE],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Print simple diagnostics for multiplex corridors."""

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
    ax: plt.Axes,
    tech_links: dict[str, TECH_STYLE],
    spacing: PlotSpacing,
) -> None:
    """Plot transport lines in lon/lat schematic mode."""

    transport_gdf_3857 = combined_transport_links(tech_links, spacing)
    if transport_gdf_3857.empty:
        return

    transport_gdf = transport_gdf_3857.to_crs(epsg=4326)

    for _, group in transport_gdf.groupby("display_name", sort=False):
        max_flow = group["flow"].max()
        if max_flow <= 0 or not np.isfinite(max_flow):
            continue

        for row in group.itertuples(index=False):
            x_vals, y_vals = row.geometry.xy
            linewidth = row.width_factor * (0.5 + 2.5 * np.sqrt(row.flow / max_flow))
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
    ax: plt.Axes,
    tech_links: dict[str, TECH_STYLE],
    spacing: PlotSpacing,
) -> None:
    """Plot transport lines in EPSG:3857 web-map mode."""

    transport_gdf_3857 = combined_transport_links(tech_links, spacing)
    if transport_gdf_3857.empty:
        return

    for _, group in transport_gdf_3857.groupby("display_name", sort=False):
        max_flow = group["flow"].max()
        if max_flow <= 0 or not np.isfinite(max_flow):
            continue

        for row in group.itertuples(index=False):
            x_vals, y_vals = row.geometry.xy
            linewidth = row.width_factor * (0.5 + 2.5 * np.sqrt(row.flow / max_flow))
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
    ax: plt.Axes,
    tech_links: dict[str, TECH_STYLE],
    geodata: GeospatialData,
    bbox_to_anchor: tuple[float, float],
    fontsize: int | None = None,
    loc: str = "upper right",
) -> None:
    """Build figure legend using the same proxies as the original script."""

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))

    proxies = [
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
        Line2D([0], [0], color="0.70", lw=0.8, label="Canada/province polygons"),
        Line2D([0], [0], color="0.80", lw=0.8, label="Model region polygons"),
    ]
    if geodata.road_overlay is not None:
        context_proxies.append(
            Line2D([0], [0], color="0.35", lw=0.8, label="Road overlay")
        )
    if geodata.road_edge_layer is not None:
        context_proxies.append(
            Line2D([0], [0], color="0.10", lw=0.8, label="Road-enabled graph edges")
        )

    handles2 = context_proxies + list(by_label.values()) + proxies
    labels2 = (
        [proxy.get_label() for proxy in context_proxies]
        + list(by_label.keys())
        + [proxy.get_label() for proxy in proxies]
    )
    by_label2 = dict(zip(labels2, handles2))

    ax.legend(
        by_label2.values(),
        by_label2.keys(),
        title="Legend",
        frameon=False,
        loc=loc,
        bbox_to_anchor=bbox_to_anchor,
        fontsize=fontsize,
    )


def plot_points_lonlat(
    ax: plt.Axes,
    tech_points: dict[str, POINT_STYLE],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
) -> None:
    """Plot process and demand points in lon/lat schematic mode."""

    if not demand_pts.empty:
        ax.scatter(
            demand_pts["lon"],
            demand_pts["lat"],
            s=size_demand * 0.3,
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
        if points_plot.empty:
            continue

        points_plot["flow"] = pd.to_numeric(points_plot["flow"], errors="coerce")
        points_plot = points_plot.dropna(subset=["flow"])
        points_plot = points_plot.loc[points_plot["flow"] > 0].copy()
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
        size = np.sqrt(points_plot["flow"] / points_plot["flow"].max()) * 150

        ax.scatter(
            points_plot["lon_plot"],
            points_plot["lat_plot"],
            s=size,
            alpha=0.65,
            label=name,
            c=color,
            zorder=30,
        )


def plot_points_web(
    ax: plt.Axes,
    tech_points: dict[str, POINT_STYLE],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
) -> None:
    """Plot process and demand points in EPSG:3857 web-map mode."""

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
    """Build and save the polygon-context schematic figure."""

    fig, ax = plt.subplots(figsize=(10, 6))

    plot_context_layers_schematic(ax, geodata)

    ax.scatter(
        geodata.sites["lon"],
        geodata.sites["lat"],
        s=3,
        alpha=0.12,
        label="Region centroid",
        c="gray",
        zorder=10,
    )

    plot_points_lonlat(
        ax,
        layers.tech_points,
        layers.demand_pts,
        layers.size_demand,
        spacing,
    )
    plot_transport_lines_schematic(ax, layers.tech_links, spacing)

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        geodata=geodata,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
    )

    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    sns.despine(top=True, right=True, bottom=True, left=True)
    plt.tight_layout()

    fig_path = paths.figure_dir / f"{paths.fig_stem}_polygon_context.png"
    plt.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.show()
    print(f"Saved polygon-context figure: {fig_path}")

    return fig_path


def save_basemap_overlay_figure(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build and save the contextily + polygon/road overlay figure."""

    fig, ax = plt.subplots(figsize=(9, 10))

    plot_context_layers_web(ax, geodata)

    if PLOT_WEB_TILES:
        try:
            ctx.add_basemap(
                ax,
                source=ctx.providers.OpenStreetMap.Mapnik,
                zorder=0,
            )
        except Exception as exc:  # pragma: no cover - depends on web/tile service.
            print(
                "Contextily basemap failed; continuing with local layers only. "
                f"Reason: {exc}"
            )

    sites_web_points = gpd.GeoDataFrame(
        geodata.sites,
        geometry=gpd.points_from_xy(geodata.sites["lon"], geodata.sites["lat"]),
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
    plot_transport_lines_web(ax, layers.tech_links, spacing)

    ax.set_title("", fontsize=14, pad=12)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        geodata=geodata,
        bbox_to_anchor=(1.35, 1.0),
        fontsize=10,
    )

    ax.set_xticks([])
    ax.set_yticks([])
    plt.tight_layout()

    fig_path = paths.figure_dir / f"{paths.fig_stem}_basemap_polygon_overlay.svg"
    plt.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.show()
    print(f"Saved basemap polygon-overlay figure: {fig_path}")

    return fig_path


# =============================================================================
# Main workflow
# =============================================================================

def main() -> None:
    """Run the interactive figure-generation workflow."""

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

    # The original workflow defined this diagnostic helper but did not call it in
    # the final plotting path. Keep it available without changing console output.
    _ = PLOT_PARALLEL_TRANSPORT_ARCS

    save_polygon_context_figure(geodata, layers, spacing, geospatial_paths)
    save_basemap_overlay_figure(geodata, layers, spacing, geospatial_paths)


if __name__ == "__main__":
    main()
