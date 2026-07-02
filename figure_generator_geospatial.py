import numpy as np
import pandas as pd
import seaborn as sns
import db_mgmt as mgmt
import geopandas as gpd
import contextily as ctx
import matplotlib.pyplot as plt
from pathlib import Path
from matplotlib.lines import Line2D
from shapely.geometry import LineString
import re
import sys


# =============================================================================
# Select model run
# =============================================================================

output_dir = Path("output_files")

runs = sorted([
    d for d in output_dir.iterdir()
    if d.is_dir() and any(d.glob("*.sqlite"))
])

if not runs:
    print(f"No valid runs found in {output_dir.resolve()}")
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
# Paths
# =============================================================================

# Derive basemap stem from the selected database's filename rather than
# hardcoding it, so this script works for any basemap/connection-method
# combination without manual edits.
# Expected db_path.stem pattern:
#   CANOE_geospatial_<basemap_stem>_roads_<connection_method>
match = re.search(r"canada_basemap_.+?(?=_roads_)", db_path.stem)

if not match:
    print(f"Could not infer basemap stem from database filename: {db_path.stem}")
    sys.exit(1)

BASEMAP_STEM = match.group(0)

GRAPH_DIR = Path("data_files") / "processed" / "graph"

NODE_PATH = GRAPH_DIR / f"{BASEMAP_STEM}_graph_nodes.gpkg"
EDGE_PATH = GRAPH_DIR / f"{BASEMAP_STEM}_graph_edges.csv"

if not NODE_PATH.exists() or not EDGE_PATH.exists():
    print(f"Missing graph files for basemap '{BASEMAP_STEM}':")
    print(f"  {NODE_PATH} (exists: {NODE_PATH.exists()})")
    print(f"  {EDGE_PATH} (exists: {EDGE_PATH.exists()})")
    sys.exit(1)

print(f"Inferred basemap stem: {BASEMAP_STEM}")
print(f"  Graph nodes: {NODE_PATH.name}")
print(f"  Graph edges: {EDGE_PATH.name}")

FIGURE_DIR = Path("figures") / "geospatial_transport" / selected_run.name
FIGURE_DIR.mkdir(parents=True, exist_ok=True)

# The run folder name and the database stem both encode the basemap and
# connection method, so concatenating them naively duplicates that
# information and can produce filenames long enough to exceed Windows'
# MAX_PATH (260 chars). Strip the redundant prefix and skip appending
# db_tag entirely when it's already implied by run_tag.
run_tag = selected_run.name.replace(" ", "_")

db_tag = db_path.stem
if db_tag.startswith("CANOE_geospatial_"):
    db_tag = db_tag[len("CANOE_geospatial_"):]

fig_stem = run_tag if db_tag in run_tag else f"{run_tag}_{db_tag}"


# =============================================================================
# Plot settings
# =============================================================================

MIN_TRANSPORT_FLOW = 0
MIN_PROCESS_FLOW = 0

JITTER_DEG = 1.5


# =============================================================================
# Load data
# =============================================================================

data = mgmt.sqlite_to_dfs(str(db_path))

flowIn = data["OutputFlowIn"].copy()
flowOut = data["OutputFlowOut"].copy()
demand = data["Demand"].copy()
limit_capacity = data["LimitCapacity"].copy()

sites = gpd.read_file(NODE_PATH)
edges = pd.read_csv(EDGE_PATH)

sites["region"] = sites["region"].astype(str)
sites["site_id"] = sites["region"]
sites = sites.set_index("region", drop=True)

edges["edge_region"] = edges["edge_region"].astype(str)
edges["region_from"] = edges["region_from"].astype(str)
edges["region_to"] = edges["region_to"].astype(str)

print(f"\nOutputFlowOut rows: {len(flowOut):,}")
print(f"Graph nodes: {len(sites):,}")
print(f"Graph edges: {len(edges):,}")


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

    out = add_site_coords(out, idx_col="region")
    return out


def add_from_to_coords(df_links):
    df = df_links.copy()

    df["flow"] = pd.to_numeric(df["flow"], errors="coerce")
    df = df.loc[df["flow"] > MIN_TRANSPORT_FLOW].copy()

    df = df.merge(
        edges[
            [
                "edge_region",
                "region_from",
                "region_to",
                "lon_from",
                "lat_from",
                "lon_to",
                "lat_to",
            ]
        ],
        left_on="region",
        right_on="edge_region",
        how="left",
        validate="many_to_one",
        suffixes=("", "_edge"),
    )

    df = df.dropna(
        subset=["lon_from", "lat_from", "lon_to", "lat_to"]
    ).copy()

    return df


def plot_transport_lines_schematic(ax, tech_links):
    for name, values in tech_links.items():
        links, color, linestyle, width_factor = values

        if links.empty:
            continue

        links_plot = links[["lon_from", "lat_from", "lon_to", "lat_to", "flow"]].dropna().copy()
        links_plot["flow"] = pd.to_numeric(links_plot["flow"], errors="coerce")
        links_plot = links_plot.loc[links_plot["flow"] > 0].copy()

        if links_plot.empty:
            continue

        max_flow = links_plot["flow"].max()

        for _, row in links_plot.iterrows():
            lw = width_factor * (0.5 + 2.5 * np.sqrt(row["flow"] / max_flow))

            ax.plot(
                [row["lon_from"], row["lon_to"]],
                [row["lat_from"], row["lat_to"]],
                color=color,
                linestyle=linestyle,
                linewidth=lw,
                alpha=0.75,
            )


def plot_transport_lines_web(ax, tech_links):
    for name, values in tech_links.items():
        links, color, linestyle, width_factor = values

        if links.empty:
            continue

        links_plot = links[["lon_from", "lat_from", "lon_to", "lat_to", "flow"]].dropna().copy()
        links_plot["flow"] = pd.to_numeric(links_plot["flow"], errors="coerce")
        links_plot = links_plot.loc[links_plot["flow"] > 0].copy()

        if links_plot.empty:
            continue

        max_flow = links_plot["flow"].max()

        for _, row in links_plot.iterrows():
            line = gpd.GeoSeries(
                [LineString([(row["lon_from"], row["lat_from"]), (row["lon_to"], row["lat_to"])])],
                crs="EPSG:4326",
            ).to_crs(epsg=3857)

            x, y = line.geometry.iloc[0].xy
            lw = width_factor * (0.5 + 2.5 * np.sqrt(row["flow"] / max_flow))

            ax.plot(x, y, color=color, linestyle=linestyle, linewidth=lw, alpha=0.7)


# =============================================================================
# Node layers
# =============================================================================

elc_gen = slice_with_coords(flowOut, "ELC_GEN")
h2_plant = slice_with_coords(flowOut, "H2_PLANT")
metoh_plant = slice_with_coords(flowOut, "METOH_PLANT")
gsl_plant = slice_with_coords(flowOut, "GSL_PLANT")


# CO2 emitters from exogenous capacity
co2_cap = limit_capacity.loc[
    limit_capacity["tech_or_group"] == "CO2_CAP"
].copy()

co2_cap["flow"] = pd.to_numeric(
    co2_cap["capacity"],
    errors="coerce",
)

co2_cap = co2_cap.loc[co2_cap["flow"] > 0].copy()

co2_cap = (
    co2_cap
    .groupby("region", as_index=False)
    .agg(flow=("flow", "sum"))
)

co2_cap = add_site_coords(co2_cap, idx_col="region")


# =============================================================================
# Transport layers
# =============================================================================

h2_pipe = add_from_to_coords(flowOut.loc[flowOut.tech == "H2_PIPE"])
h2_truck = add_from_to_coords(flowOut.loc[flowOut.tech == "H2_TRUCK"])

gsl_pipe = add_from_to_coords(flowOut.loc[flowOut.tech == "GSL_PIPE"])
gsl_truck = add_from_to_coords(flowOut.loc[flowOut.tech == "GSL_TRUCK"])

meth_pipe = add_from_to_coords(flowOut.loc[flowOut.tech == "METOH_PIPE"])
meth_truck = add_from_to_coords(flowOut.loc[flowOut.tech == "METOH_TRUCK"])

co2_pipe = add_from_to_coords(flowOut.loc[flowOut.tech == "CO2_PIPE"])
co2_truck = add_from_to_coords(flowOut.loc[flowOut.tech == "CO2_TRUCK"])

elc_trans = add_from_to_coords(flowOut.loc[flowOut.tech == "ELC_TRANS"])


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

demand_pts = demand.loc[
    demand["commodity"] == "d_gsl"
].copy()

demand_pts["demand"] = pd.to_numeric(
    demand_pts["demand"],
    errors="coerce",
)

demand_pts = demand_pts.loc[
    demand_pts["demand"] > 0
].copy()

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
# Plot 1: schematic figure, original format
# =============================================================================

fig, ax = plt.subplots(figsize=(10, 6))

ax.scatter(
    sites["lon"],
    sites["lat"],
    s=3,
    alpha=0.05,
    label="Region",
    c="gray",
)

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

    dfp_plot["lon_plot"] = (
        dfp_plot["lon"]
        + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp_plot))
    )
    dfp_plot["lat_plot"] = (
        dfp_plot["lat"]
        + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp_plot))
    )

    size = np.sqrt(dfp_plot["flow"] / dfp_plot["flow"].max()) * 150

    ax.scatter(
        dfp_plot["lon_plot"],
        dfp_plot["lat_plot"],
        s=size,
        alpha=0.6,
        label=name,
        c=color,
    )


plot_transport_lines_schematic(ax, tech_links)

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

    handles2 = list(by_label.values()) + proxies
    labels2 = list(by_label.keys()) + [p.get_label() for p in proxies]
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

build_legend(
    ax=ax,
    tech_links=tech_links,
    loc="center left",
    bbox_to_anchor=(1.02, 0.5),
)

ax.set_xticks([])
ax.set_yticks([])

sns.despine(top=True, right=True, bottom=True, left=True)
plt.tight_layout()

fig_path = FIGURE_DIR / f"{fig_stem}_schematic.png"
plt.savefig(fig_path, dpi=300)
plt.show()

print(f"Saved schematic figure: {fig_path}")


# =============================================================================
# Plot 2: contextily basemap figure, original format
# =============================================================================

sites_gdf = gpd.GeoDataFrame(
    sites,
    geometry=gpd.points_from_xy(sites["lon"], sites["lat"]),
    crs="EPSG:4326",
)

sites_web = sites_gdf.to_crs(epsg=3857)

fig, ax = plt.subplots(figsize=(9, 10))

sites_web.plot(
    ax=ax,
    color="gray",
    markersize=2,
    alpha=0.08,
    label="Region",
)

ctx.add_basemap(
    ax,
    source=ctx.providers.OpenStreetMap.Mapnik,
)

if not demand_pts.empty:
    demand_pts_gdf = gpd.GeoDataFrame(
        demand_pts,
        geometry=gpd.points_from_xy(
            demand_pts["lon"],
            demand_pts["lat"],
        ),
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

    dfp["lon_plot"] = (
        dfp["lon"]
        + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp))
    )
    dfp["lat_plot"] = (
        dfp["lat"]
        + rng.uniform(-JITTER_DEG, JITTER_DEG, size=len(dfp))
    )

    dfp_gdf = gpd.GeoDataFrame(
        dfp,
        geometry=gpd.points_from_xy(
            dfp["lon_plot"],
            dfp["lat_plot"],
        ),
        crs="EPSG:4326",
    ).to_crs(epsg=3857)

    sizes = np.sqrt(dfp["flow"] / dfp["flow"].max()) * 90

    ax.scatter(
        dfp_gdf.geometry.x,
        dfp_gdf.geometry.y,
        s=sizes,
        color=color,
        alpha=0.6,
        label=name,
    )

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

fig_path = FIGURE_DIR / f"{fig_stem}_basemap.svg"
plt.savefig(fig_path, dpi=300, bbox_inches="tight")
plt.show()

print(f"Saved basemap figure: {fig_path}")