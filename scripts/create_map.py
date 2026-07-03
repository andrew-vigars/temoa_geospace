# =============================================================================
# fcreate_map.py
#
# Decode CANOE/TEMOA geospatial output flows and plot transport routes.
# This version uses the matching graph-node polygons, processed basemap polygons,
# and road-overlay/connectivity layers as geographic context behind model outputs.
# =============================================================================

from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd
import geopandas as gpd
import contextily as ctx
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.lines import Line2D
from shapely.geometry import LineString

import db_mgmt as mgmt


# =============================================================================
# Select model run
# =============================================================================

SCRIPT_PATH = Path(__file__).resolve()

if (SCRIPT_PATH.parent / "output_files").exists() and (SCRIPT_PATH.parent / "data_files").exists():
    PROJECT_ROOT = SCRIPT_PATH.parent
elif (SCRIPT_PATH.parent.parent / "output_files").exists() and (SCRIPT_PATH.parent.parent / "data_files").exists():
    PROJECT_ROOT = SCRIPT_PATH.parent.parent
else:
    PROJECT_ROOT = Path.cwd().resolve()

OUTPUT_ROOT = PROJECT_ROOT / "output_files"
DATA_FILES = PROJECT_ROOT / "data_files"
runs = sorted([
    d for d in OUTPUT_ROOT.iterdir()
    if d.is_dir() and any(d.glob("*.sqlite"))
])

if not runs:
    print(f"No valid runs found in {OUTPUT_ROOT.resolve()}")
    sys.exit(1)

print("\nAvailable model runs:")
for i, r in enumerate(runs):
    sqlite_files = sorted(r.glob("*.sqlite"))
    print(f"  [{i}] {r.name}  ({len(sqlite_files)} sqlite file(s))")

run_idx = input("\nSelect run index: ").strip()

try:
    selected_run = runs[int(run_idx)]
except (ValueError, IndexError):
    print("Invalid selection.")
    sys.exit(1)


# =============================================================================
# Select SQLite database
# =============================================================================

sqlite_files = sorted(selected_run.glob("*.sqlite"))

if len(sqlite_files) == 1:
    db_path = sqlite_files[0]
else:
    print("\nAvailable SQLite files:")
    for i, f in enumerate(sqlite_files):
        print(f"  [{i}] {f.name}  ({f.stat().st_size / 1e6:.1f} MB)")

    db_idx = input("\nSelect database index: ").strip()

    try:
        db_path = sqlite_files[int(db_idx)]
    except (ValueError, IndexError):
        print("Invalid database selection.")
        sys.exit(1)

print(f"  → {db_path}")


# =============================================================================
# Infer paths from selected database
# =============================================================================

# Expected db_path.stem pattern:
#   CANOE_geospatial_<basemap_stem>_roads_<connection_method>
# Example:
#   CANOE_geospatial_canada_basemap_1deg_centroid_roads_weak
match = re.search(r"canada_basemap_.+?(?=_roads_)", db_path.stem)

if not match:
    print(f"Could not infer basemap stem from database filename: {db_path.stem}")
    sys.exit(1)

BASEMAP_STEM = match.group(0)

GRAPH_DIR = DATA_FILES / "processed" / "graph"
BASEMAP_DIR = DATA_FILES / "processed" / "basemaps"
ROAD_CONNECTIVITY_DIR = DATA_FILES / "processed" / "road_connectivity"

NODE_PATH = GRAPH_DIR / f"{BASEMAP_STEM}_graph_nodes.gpkg"
EDGE_PATH = GRAPH_DIR / f"{BASEMAP_STEM}_graph_edges.csv"
BASEMAP_PATH = BASEMAP_DIR / f"{BASEMAP_STEM}.gpkg"

# Road overlay files are built from the selected road network and graph-node stem.
# Keep this optional because a model run can exist before overlay files are exported.
ROAD_OVERLAY_CANDIDATES = sorted(
    ROAD_CONNECTIVITY_DIR.glob(f"*__{BASEMAP_STEM}_graph_nodes*_road_overlay.gpkg")
)
ROAD_EDGE_CANDIDATES = sorted(
    ROAD_CONNECTIVITY_DIR.glob(f"*__{BASEMAP_STEM}_graph_nodes*_road_edges.gpkg")
)

ROAD_OVERLAY_PATH = ROAD_OVERLAY_CANDIDATES[0] if ROAD_OVERLAY_CANDIDATES else None
ROAD_EDGE_GPKG_PATH = ROAD_EDGE_CANDIDATES[0] if ROAD_EDGE_CANDIDATES else None

required_paths = {
    "Graph nodes": NODE_PATH,
    "Graph edges": EDGE_PATH,
    "Processed basemap": BASEMAP_PATH,
}

missing = {name: path for name, path in required_paths.items() if not path.exists()}
if missing:
    print(f"Missing required geospatial files for basemap '{BASEMAP_STEM}':")
    for name, path in missing.items():
        print(f"  {name}: {path} (exists: {path.exists()})")
    sys.exit(1)

FIGURE_DIR = db_path.parent
FIGURE_DIR.mkdir(parents=True, exist_ok=True)

run_tag = selected_run.name.replace(" ", "_")
db_tag = db_path.stem
if db_tag.startswith("CANOE_geospatial_"):
    db_tag = db_tag[len("CANOE_geospatial_"):]
fig_stem = run_tag if db_tag in run_tag else f"{run_tag}_{db_tag}"

print(f"\nInferred basemap stem: {BASEMAP_STEM}")
print(f"  Graph nodes:       {NODE_PATH.name}")
print(f"  Graph edges:       {EDGE_PATH.name}")
print(f"  Basemap polygons:  {BASEMAP_PATH.name}")
print(f"  Road overlay:      {ROAD_OVERLAY_PATH.name if ROAD_OVERLAY_PATH else 'not found'}")
print(f"  Road edge layer:   {ROAD_EDGE_GPKG_PATH.name if ROAD_EDGE_GPKG_PATH else 'not found'}")


# =============================================================================
# Plot settings
# =============================================================================

MIN_TRANSPORT_FLOW = 1e-3
MIN_PROCESS_FLOW = 1e-3

# These are assigned after geospatial data is loaded using the selected basemap resolution.
JITTER_DEG = None

PLOT_WEB_TILES = True
PLOT_ROAD_OVERLAY = True
PLOT_ROAD_EDGE_LAYER = True

# Parallel corridor plotting.
# Used to separate multiple transport technologies that use the same model edge.
PLOT_PARALLEL_TRANSPORT_ARCS = True
PARALLEL_OFFSET_M = None
PARALLEL_MAX_OFFSET_M = None



# =============================================================================
# Load model and geospatial data
# =============================================================================

data = mgmt.sqlite_to_dfs(str(db_path))

flow_out = data["OutputFlowOut"].copy()
demand = data["Demand"].copy()
limit_capacity = data["LimitCapacity"].copy()

# Graph nodes are the model regions. These are polygons with lon/lat columns.
sites = gpd.read_file(NODE_PATH)
edges = pd.read_csv(EDGE_PATH)
basemap = gpd.read_file(BASEMAP_PATH)

road_overlay = None
if ROAD_OVERLAY_PATH and ROAD_OVERLAY_PATH.exists():
    road_overlay = gpd.read_file(ROAD_OVERLAY_PATH)

road_edge_layer = None
if ROAD_EDGE_GPKG_PATH and ROAD_EDGE_GPKG_PATH.exists():
    road_edge_layer = gpd.read_file(ROAD_EDGE_GPKG_PATH)

sites["region"] = sites["region"].astype(str)
sites["site_id"] = sites["region"]
sites = sites.set_index("region", drop=True)


# =============================================================================
# Resolution-aware visual spacing
# =============================================================================

def infer_degree_resolution(basemap_stem: str) -> float | None:
    """Infer degree grid resolution from names like canada_basemap_1deg_intersects."""
    match = re.search(r"_(\d+(?:\.\d+)?)deg(?:_|$)", basemap_stem)
    return float(match.group(1)) if match else None


def infer_centroid_spacing_deg(sites_gdf: gpd.GeoDataFrame) -> float:
    """Fallback spacing from model-region centroid coordinates."""
    lon_vals = np.sort(pd.to_numeric(sites_gdf["lon"], errors="coerce").dropna().unique())
    lat_vals = np.sort(pd.to_numeric(sites_gdf["lat"], errors="coerce").dropna().unique())

    lon_step = np.median(np.diff(lon_vals)) if len(lon_vals) > 1 else np.nan
    lat_step = np.median(np.diff(lat_vals)) if len(lat_vals) > 1 else np.nan

    candidates = [abs(x) for x in [lon_step, lat_step] if np.isfinite(x) and abs(x) > 0]
    return min(candidates) if candidates else 1.0


def configure_resolution_aware_spacing(
    basemap_stem: str,
    sites_gdf: gpd.GeoDataFrame,
    diagnostic_mode: bool = False,
) -> tuple[float, float, float, float]:
    """Return grid resolution, node jitter in degrees, arc offset m, and max arc offset m.

    Node jitter is only for visual separation of colocated facilities. It should never be
    large enough to move a point into a different model cell during diagnostic plots.
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

    return grid_res_deg, jitter_deg, parallel_offset_m, parallel_max_offset_m


DIAGNOSTIC_MODE = False
GRID_RES_DEG, JITTER_DEG, PARALLEL_OFFSET_M, PARALLEL_MAX_OFFSET_M = configure_resolution_aware_spacing(
    BASEMAP_STEM,
    sites,
    diagnostic_mode=DIAGNOSTIC_MODE,
)

print("\nResolution-aware plot spacing:")
print(f"  DIAGNOSTIC_MODE:        {DIAGNOSTIC_MODE}")
print(f"  GRID_RES_DEG:           {GRID_RES_DEG:,.4f}")
print(f"  JITTER_DEG:             {JITTER_DEG:,.4f}")
print(f"  PARALLEL_OFFSET_M:      {PARALLEL_OFFSET_M:,.0f}")
print(f"  PARALLEL_MAX_OFFSET_M:  {PARALLEL_MAX_OFFSET_M:,.0f}")

edges["edge_region"] = edges["edge_region"].astype(str)
edges["region_from"] = edges["region_from"].astype(str)
edges["region_to"] = edges["region_to"].astype(str)

# Use graph-node CRS as the plotting CRS anchor. In current workflow this is EPSG:4326.
if basemap.crs != sites.crs:
    basemap = basemap.to_crs(sites.crs)
if road_overlay is not None and road_overlay.crs != sites.crs:
    road_overlay = road_overlay.to_crs(sites.crs)
if road_edge_layer is not None and road_edge_layer.crs != sites.crs:
    road_edge_layer = road_edge_layer.to_crs(sites.crs)

print(f"\nOutputFlowOut rows: {len(flow_out):,}")
print(f"Graph-node polygons: {len(sites):,}")
print(f"Graph edges: {len(edges):,}")
print(f"Basemap polygons: {len(basemap):,}")
if road_overlay is not None:
    print(f"Road-overlay geometries: {len(road_overlay):,}")
if road_edge_layer is not None:
    print(f"Road-enabled edge geometries: {len(road_edge_layer):,}")


# =============================================================================
# Helper functions
# =============================================================================

def add_site_coords(df, idx_col="region"):
    if idx_col != df.index.name:
        df = df.set_index(idx_col, drop=False)
    return df.join(sites[["lon", "lat"]], how="left")


def slice_with_coords(df, tech_name):
    out = df.loc[df["tech"] == tech_name].copy()
    out["flow"] = pd.to_numeric(out["flow"], errors="coerce")
    out = out.loc[out["flow"] > MIN_PROCESS_FLOW].copy()

    out = (
        out
        .groupby(["region", "tech"], as_index=False)
        .agg(flow=("flow", "sum"))
    )

    return add_site_coords(out, idx_col="region")


def add_from_to_coords(df_links):
    """Attach graph edge coordinates to positive transport flows.

    OutputFlowOut can contain multiple rows for the same region-tech pair if later
    model versions add periods, seasons, TOD, scenarios, or output commodities.
    For mapping, collapse these to one visible line per edge and technology.
    """
    df = df_links.copy()
    df["flow"] = pd.to_numeric(df["flow"], errors="coerce")
    df = df.loc[df["flow"] > MIN_TRANSPORT_FLOW].copy()

    df = df.merge(
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

    df = df.dropna(subset=["lon_from", "lat_from", "lon_to", "lat_to"]).copy()
    if df.empty:
        return df

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
        df
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )


def plot_context_layers_schematic(ax):
    """Plot basemap polygon boundaries and model-region polygons in lon/lat."""
    basemap.plot(
        ax=ax,
        color="none",
        edgecolor="0.70",
        linewidth=0.5,
        alpha=0.7,
        zorder=0,
    )

    sites.plot(
        ax=ax,
        color="none",
        edgecolor="0.85",
        linewidth=0.25,
        alpha=0.75,
        zorder=1,
    )

    if PLOT_ROAD_OVERLAY and road_overlay is not None:
        road_overlay.plot(
            ax=ax,
            color="0.35",
            linewidth=0.15,
            alpha=0.10,
            zorder=2,
        )

    if PLOT_ROAD_EDGE_LAYER and road_edge_layer is not None:
        road_edge_layer.plot(
            ax=ax,
            color="0.15",
            linewidth=0.35,
            alpha=0.30,
            zorder=3,
        )


def plot_context_layers_web(ax):
    """Plot context layers in EPSG:3857 for optional web-tile background."""
    basemap_web = basemap.to_crs(epsg=3857)
    sites_web_poly = sites.to_crs(epsg=3857)

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

    if PLOT_ROAD_OVERLAY and road_overlay is not None:
        road_overlay.to_crs(epsg=3857).plot(
            ax=ax,
            color="0.25",
            linewidth=0.15,
            alpha=0.12,
            zorder=3,
        )

    if PLOT_ROAD_EDGE_LAYER and road_edge_layer is not None:
        road_edge_layer.to_crs(epsg=3857).plot(
            ax=ax,
            color="0.10",
            linewidth=0.35,
            alpha=0.35,
            zorder=4,
        )


def _combined_transport_links(tech_links):
    """Combine transport layers and assign one parallel offset per corridor/technology."""
    frames = []

    for display_name, values in tech_links.items():
        links, color, linestyle, width_factor = values
        if links.empty:
            continue

        required = [
            "region", "tech", "region_from", "region_to",
            "lon_from", "lat_from", "lon_to", "lat_to", "flow",
        ]
        missing_cols = [c for c in required if c not in links.columns]
        if missing_cols:
            print(f"Skipping {display_name}; missing columns: {missing_cols}")
            continue

        tmp = links[required].dropna().copy()
        tmp["flow"] = pd.to_numeric(tmp["flow"], errors="coerce")
        tmp = tmp.loc[tmp["flow"] > 0].copy()
        if tmp.empty:
            continue

        tmp["display_name"] = display_name
        tmp["color"] = color
        tmp["linestyle"] = linestyle
        tmp["width_factor"] = width_factor

        # Infrastructure-corridor identity is undirected for plotting.
        tmp["route_a"] = tmp[["region_from", "region_to"]].min(axis=1)
        tmp["route_b"] = tmp[["region_from", "region_to"]].max(axis=1)
        tmp["route_key"] = tmp["route_a"] + "__" + tmp["route_b"]
        tmp["is_reversed_from_canonical"] = tmp["region_from"] != tmp["route_a"]

        frames.append(tmp)

    if not frames:
        return gpd.GeoDataFrame(columns=[])

    combined = pd.concat(frames, ignore_index=True)

    # Collapse accidental duplicates after the layer merge while preserving style.
    group_cols = [
        "region", "tech", "display_name", "color", "linestyle", "width_factor",
        "region_from", "region_to", "route_a", "route_b", "route_key",
        "is_reversed_from_canonical", "lon_from", "lat_from", "lon_to", "lat_to",
    ]
    combined = (
        combined
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )

    # Stable ordering inside each corridor bundle.
    # Pipes first, transmission near centre, trucks last. This makes figures reproducible.
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
    combined = combined.sort_values(["route_key", "plot_order", "display_name"]).copy()

    combined["parallel_rank"] = combined.groupby("route_key").cumcount()
    combined["parallel_count"] = combined.groupby("route_key")["display_name"].transform("count")

    raw_offset = (combined["parallel_rank"] - (combined["parallel_count"] - 1) / 2.0) * PARALLEL_OFFSET_M
    combined["offset_m"] = raw_offset.clip(-PARALLEL_MAX_OFFSET_M, PARALLEL_MAX_OFFSET_M)

    # Keep offset side consistent for R_i-R_j and R_j-R_i.
    combined.loc[combined["is_reversed_from_canonical"], "offset_m"] *= -1

    base_geom = [
        LineString([(r.lon_from, r.lat_from), (r.lon_to, r.lat_to)])
        for r in combined.itertuples(index=False)
    ]

    gdf = gpd.GeoDataFrame(combined, geometry=base_geom, crs="EPSG:4326")
    gdf_3857 = gdf.to_crs(epsg=3857)

    offset_geoms = []
    for row in gdf_3857.itertuples(index=False):
        coords = list(row.geometry.coords)
        x1, y1 = coords[0]
        x2, y2 = coords[-1]
        dx = x2 - x1
        dy = y2 - y1
        length = float(np.hypot(dx, dy))

        if length == 0 or not np.isfinite(length):
            offset_geoms.append(row.geometry)
            continue

        # Unit perpendicular vector.
        ux = -dy / length
        uy = dx / length

        off = float(row.offset_m)
        offset_geoms.append(
            LineString([
                (x1 + ux * off, y1 + uy * off),
                (x2 + ux * off, y2 + uy * off),
            ])
        )

    gdf_3857 = gdf_3857.copy()
    gdf_3857["geometry"] = offset_geoms

    return gdf_3857


def summarize_parallel_corridors(tech_links):
    """Print simple diagnostics for multiplex corridors."""
    transport_gdf = _combined_transport_links(tech_links)
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

    multiplex = corridor_summary.loc[corridor_summary["n_parallel_layers"] > 1].copy()

    print("\nParallel corridor diagnostics:")
    print(f"Active transport corridor-tech rows: {len(transport_gdf):,}")
    print(f"Unique active corridors: {len(corridor_summary):,}")
    print(f"Corridors with >1 active transport layer: {len(multiplex):,}")

    if not multiplex.empty:
        print("\nTop multiplex corridors:")
        print(multiplex.head(10).to_string(index=False))

    return transport_gdf


def plot_transport_lines_schematic(ax, tech_links):
    transport_gdf_3857 = _combined_transport_links(tech_links)
    if transport_gdf_3857.empty:
        return

    transport_gdf = transport_gdf_3857.to_crs(epsg=4326)

    for display_name, group in transport_gdf.groupby("display_name", sort=False):
        max_flow = group["flow"].max()
        if max_flow <= 0 or not np.isfinite(max_flow):
            continue

        for row in group.itertuples(index=False):
            x, y = row.geometry.xy
            lw = row.width_factor * (0.5 + 2.5 * np.sqrt(row.flow / max_flow))
            ax.plot(
                x,
                y,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=lw,
                alpha=0.78,
                zorder=20,
            )


def plot_transport_lines_web(ax, tech_links):
    transport_gdf_3857 = _combined_transport_links(tech_links)
    if transport_gdf_3857.empty:
        return

    for display_name, group in transport_gdf_3857.groupby("display_name", sort=False):
        max_flow = group["flow"].max()
        if max_flow <= 0 or not np.isfinite(max_flow):
            continue

        for row in group.itertuples(index=False):
            x, y = row.geometry.xy
            lw = row.width_factor * (0.5 + 2.5 * np.sqrt(row.flow / max_flow))
            ax.plot(
                x,
                y,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=lw,
                alpha=0.72,
                zorder=20,
            )

def build_legend(ax, tech_links, bbox_to_anchor, fontsize=None, loc="upper right"):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))

    proxies = [
        Line2D(
            [0], [0],
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
    if road_overlay is not None:
        context_proxies.append(Line2D([0], [0], color="0.35", lw=0.8, label="Road overlay"))
    if road_edge_layer is not None:
        context_proxies.append(Line2D([0], [0], color="0.10", lw=0.8, label="Road-enabled graph edges"))

    handles2 = context_proxies + list(by_label.values()) + proxies
    labels2 = [p.get_label() for p in context_proxies] + list(by_label.keys()) + [p.get_label() for p in proxies]
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


def plot_points_lonlat(ax, tech_points, demand_pts, size_demand):
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

    for name, (dfp, color) in tech_points.items():
        if dfp.empty or "flow" not in dfp.columns:
            continue

        dfp_plot = dfp[["lon", "lat", "flow"]].dropna().copy()
        if dfp_plot.empty:
            continue

        dfp_plot["flow"] = pd.to_numeric(dfp_plot["flow"], errors="coerce")
        dfp_plot = dfp_plot.dropna(subset=["flow"])
        dfp_plot = dfp_plot.loc[dfp_plot["flow"] > 0].copy()
        if dfp_plot.empty:
            continue

        dfp_plot["lon_plot"] = dfp_plot["lon"] + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp_plot))
        dfp_plot["lat_plot"] = dfp_plot["lat"] + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp_plot))
        size = np.sqrt(dfp_plot["flow"] / dfp_plot["flow"].max()) * 150

        ax.scatter(
            dfp_plot["lon_plot"],
            dfp_plot["lat_plot"],
            s=size,
            alpha=0.65,
            label=name,
            c=color,
            zorder=30,
        )


def plot_points_web(ax, tech_points, demand_pts, size_demand):
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

    for name, (dfp, color) in tech_points.items():
        if dfp.empty or "flow" not in dfp.columns:
            continue

        dfp = dfp.dropna(subset=["lon", "lat", "flow"]).copy()
        if dfp.empty:
            continue

        dfp["flow"] = pd.to_numeric(dfp["flow"], errors="coerce")
        dfp = dfp.dropna(subset=["flow"])
        dfp = dfp.loc[dfp["flow"] > 0].copy()
        if dfp.empty:
            continue

        dfp["lon_plot"] = dfp["lon"] + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp))
        dfp["lat_plot"] = dfp["lat"] + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp))

        dfp_gdf = gpd.GeoDataFrame(
            dfp,
            geometry=gpd.points_from_xy(dfp["lon_plot"], dfp["lat_plot"]),
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        sizes = np.sqrt(dfp["flow"] / dfp["flow"].max()) * 90

        ax.scatter(
            dfp_gdf.geometry.x,
            dfp_gdf.geometry.y,
            s=sizes,
            color=color,
            alpha=0.65,
            label=name,
            zorder=30,
        )


# =============================================================================
# Node layers
# =============================================================================

elc_gen = slice_with_coords(flow_out, "ELC_GEN")
h2_plant = slice_with_coords(flow_out, "H2_PLANT")
metoh_plant = slice_with_coords(flow_out, "METOH_PLANT")
gsl_plant = slice_with_coords(flow_out, "GSL_PLANT")

co2_cap = limit_capacity.loc[limit_capacity["tech_or_group"] == "CO2_CAP"].copy()
co2_cap["flow"] = pd.to_numeric(co2_cap["capacity"], errors="coerce")
co2_cap = co2_cap.loc[co2_cap["flow"] > 0].copy()
co2_cap = co2_cap.groupby("region", as_index=False).agg(flow=("flow", "sum"))
co2_cap = add_site_coords(co2_cap, idx_col="region")


# =============================================================================
# Transport layers
# =============================================================================

h2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_PIPE"])
h2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_TRUCK"])

gsl_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_PIPE"])
gsl_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_TRUCK"])

meth_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_PIPE"])
meth_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_TRUCK"])

co2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_PIPE"])
co2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_TRUCK"])

elc_trans = add_from_to_coords(flow_out.loc[flow_out.tech == "ELC_TRANS"])


# =============================================================================
# Plot config
# =============================================================================

tech_points = {
    "Electricity": (elc_gen, "blue"),
    "H2": (h2_plant, "green"),
    "CO2": (co2_cap, "gray"),
    "Methanol": (metoh_plant, "orange"),
    "Gasoline": (gsl_plant, "brown"),
}

tech_links = {
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


# =============================================================================
# Demand marker
# =============================================================================

demand_pts = demand.loc[demand["commodity"] == "d_gsl"].copy()
demand_pts["demand"] = pd.to_numeric(demand_pts["demand"], errors="coerce")
demand_pts = demand_pts.loc[demand_pts["demand"] > 0].copy()
demand_pts = add_site_coords(demand_pts, idx_col="region")
demand_pts = demand_pts[["lon", "lat", "demand"]].dropna().copy()

if not demand_pts.empty:
    demand_pts["demand"] = demand_pts["demand"].astype(float)
    size_demand = np.sqrt(demand_pts["demand"] / demand_pts["demand"].max()) * 500
else:
    size_demand = pd.Series(dtype=float)


# =============================================================================
# Diagnostics
# =============================================================================

print("\nNode layers:")
for name, (dfp, _) in tech_points.items():
    print(f"{name}: {len(dfp):,}")

print("\nTransport layers:")
for name, values in tech_links.items():
    links = values[0]
    total_flow = links["flow"].sum() if "flow" in links.columns else 0
    print(f"{name}: {len(links):,} links, total flow = {total_flow:,.2f}")

print(f"\nDemand nodes: {len(demand_pts):,}")


# =============================================================================
# Plot 1: polygon-context schematic figure
# =============================================================================

fig, ax = plt.subplots(figsize=(10, 6))

plot_context_layers_schematic(ax)

ax.scatter(
    sites["lon"],
    sites["lat"],
    s=3,
    alpha=0.12,
    label="Region centroid",
    c="gray",
    zorder=10,
)

plot_points_lonlat(ax, tech_points, demand_pts, size_demand)
plot_transport_lines_schematic(ax, tech_links)

build_legend(
    ax=ax,
    tech_links=tech_links,
    loc="center left",
    bbox_to_anchor=(1.02, 0.5),
)

ax.set_xticks([])
ax.set_yticks([])
ax.set_aspect("equal", adjustable="box")
sns.despine(top=True, right=True, bottom=True, left=True)
plt.tight_layout()

fig_path = FIGURE_DIR / f"{fig_stem}_polygon_context.png"
plt.savefig(fig_path, dpi=300, bbox_inches="tight")
plt.show()
print(f"Saved polygon-context figure: {fig_path}")


# =============================================================================
# Plot 2: contextily + polygon/road overlay figure
# =============================================================================

fig, ax = plt.subplots(figsize=(9, 10))

plot_context_layers_web(ax)

if PLOT_WEB_TILES:
    try:
        ctx.add_basemap(
            ax,
            source=ctx.providers.OpenStreetMap.Mapnik,
            zorder=0,
        )
    except Exception as exc:
        print(f"Contextily basemap failed; continuing with local layers only. Reason: {exc}")

sites_web_points = gpd.GeoDataFrame(
    sites,
    geometry=gpd.points_from_xy(sites["lon"], sites["lat"]),
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

plot_points_web(ax, tech_points, demand_pts, size_demand)
plot_transport_lines_web(ax, tech_links)

ax.set_title("", fontsize=14, pad=12)
ax.grid(False)
for spine in ax.spines.values():
    spine.set_visible(False)

build_legend(
    ax=ax,
    tech_links=tech_links,
    bbox_to_anchor=(1.35, 1.0),
    fontsize=10,
)

ax.set_xticks([])
ax.set_yticks([])
plt.tight_layout()

fig_path = FIGURE_DIR / f"{fig_stem}_basemap_polygon_overlay.svg"
plt.savefig(fig_path, dpi=300, bbox_inches="tight")
plt.show()
print(f"Saved basemap polygon-overlay figure: {fig_path}")