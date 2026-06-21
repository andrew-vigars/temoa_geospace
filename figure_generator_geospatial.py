# =============================================================================
# figure_generator_geospatial.py
#
# Decode CANOE/TEMOA geospatial output flows and plot transport routes.
# Inputs are selected from timestamped output_files runs.
# Figures are saved under figures/geospatial_transport/<run_name>/.
# =============================================================================

from pathlib import Path
import sys

import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import seaborn as sns
from shapely.geometry import LineString

import db_mgmt as mgmt


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent

OUTPUT_ROOT = PROJECT_ROOT / "output_files"

FIGURE_ROOT = PROJECT_ROOT / "figures"
FIGURE_TYPE = "geospatial_transport"

BASEMAP_PATH = (
    PROJECT_ROOT
    / "data_files"
    / "processed"
    / "basemaps"
    / "canada_basemap_1deg_centroid.gpkg"
)


# =============================================================================
# Transport technologies to plot
# =============================================================================

TRANSPORT_TECHS = [
    "CO2_TRUCK",
    "H2_TRUCK",
    "GSL_TRUCK",
    "METOH_TRUCK",
    "CO2_PIPE",
    "H2_PIPE",
    "GSL_PIPE",
    "METOH_PIPE",
    "ELC_TRANS",
]


# =============================================================================
# Select timestamped model run
# =============================================================================

runs = sorted(
    [
        run_dir
        for run_dir in OUTPUT_ROOT.iterdir()
        if run_dir.is_dir() and any(run_dir.glob("*.sqlite"))
    ]
)

if not runs:
    print(f"No valid runs found in {OUTPUT_ROOT.resolve()}")
    sys.exit(1)

print("\nAvailable model runs:")
for i, run_dir in enumerate(runs):
    sqlite_files = sorted(run_dir.glob("*.sqlite"))
    print(f"  [{i}] {run_dir.name} ({len(sqlite_files)} sqlite file(s))")

run_idx = input("\nSelect run index: ").strip()

try:
    selected_run = runs[int(run_idx)]
except (ValueError, IndexError):
    print("Invalid run selection.")
    sys.exit(1)


# =============================================================================
# Select SQLite database from chosen run
# =============================================================================

sqlite_files = sorted(selected_run.glob("*.sqlite"))

if not sqlite_files:
    print(f"No SQLite files found in {selected_run}")
    sys.exit(1)

if len(sqlite_files) == 1:
    db_path = sqlite_files[0]
else:
    print("\nAvailable SQLite files:")
    for i, db_file in enumerate(sqlite_files):
        size_mb = db_file.stat().st_size / 1e6
        print(f"  [{i}] {db_file.name} ({size_mb:.1f} MB)")

    db_idx = input("\nSelect database index: ").strip()

    try:
        db_path = sqlite_files[int(db_idx)]
    except (ValueError, IndexError):
        print("Invalid database selection.")
        sys.exit(1)

print(f"\nSelected run: {selected_run.name}")
print(f"Selected database: {db_path.name}")


# =============================================================================
# Figure output directory
# =============================================================================

figure_dir = (
    FIGURE_ROOT
    / FIGURE_TYPE
    / selected_run.name
)

figure_dir.mkdir(
    parents=True,
    exist_ok=True,
)

print(f"Figure output directory: {figure_dir}")


# =============================================================================
# Load model outputs and geospatial basemap
# =============================================================================

if not BASEMAP_PATH.exists():
    raise FileNotFoundError(f"Basemap not found: {BASEMAP_PATH}")

data = mgmt.sqlite_to_dfs(str(db_path))

flow_out = data["OutputFlowOut"].copy()
regions = gpd.read_file(BASEMAP_PATH)

regions = regions.set_index("region", drop=False)

print(f"OutputFlowOut rows: {len(flow_out):,}")
print(f"Basemap regions: {len(regions):,}")


# =============================================================================
# Decode transport edge pseudo-regions
# =============================================================================

flow_edges = flow_out.loc[
    flow_out["region"].astype(str).str.contains("-", regex=False)
].copy()

flow_edges = flow_edges.loc[
    flow_edges["tech"].isin(TRANSPORT_TECHS)
].copy()

flow_edges[["region_from", "region_to"]] = (
    flow_edges["region"]
    .astype(str)
    .str.split("-", n=1, expand=True)
)

flow_edges["lon_from"] = flow_edges["region_from"].map(regions["lon"])
flow_edges["lat_from"] = flow_edges["region_from"].map(regions["lat"])
flow_edges["lon_to"] = flow_edges["region_to"].map(regions["lon"])
flow_edges["lat_to"] = flow_edges["region_to"].map(regions["lat"])

flow_edges["flow"] = pd.to_numeric(
    flow_edges["flow"],
    errors="coerce",
)

flow_edges = flow_edges.dropna(
    subset=[
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
        "flow",
    ]
).copy()

flow_edges = flow_edges.loc[
    flow_edges["flow"] > 0
].copy()

print(f"Decoded positive transport flow edges: {len(flow_edges):,}")

if flow_edges.empty:
    print("No positive transport flows found.")
    sys.exit(0)


# =============================================================================
# Build centroid-to-centroid line geometries
# =============================================================================

flow_edges["geometry"] = flow_edges.apply(
    lambda row: LineString(
        [
            (row["lon_from"], row["lat_from"]),
            (row["lon_to"], row["lat_to"]),
        ]
    ),
    axis=1,
)

flow_edges_gdf = gpd.GeoDataFrame(
    flow_edges,
    geometry="geometry",
    crs="EPSG:4326",
)


# =============================================================================
# Summarize transport flows
# =============================================================================

flow_summary = (
    flow_edges
    .groupby("tech", as_index=False)
    .agg(
        n_edges=("region", "count"),
        total_flow=("flow", "sum"),
        max_flow=("flow", "max"),
    )
    .sort_values("total_flow", ascending=False)
)

print("\nTransport flow summary:")
print(flow_summary)


# =============================================================================
# Plot geospatial transport flows
# =============================================================================

sns.set_theme(style="white")

fig, ax = plt.subplots(figsize=(10, 8))

regions.plot(
    ax=ax,
    color="lightgray",
    edgecolor="none",
    alpha=0.25,
)

palette = sns.color_palette(
    "tab10",
    n_colors=flow_edges_gdf["tech"].nunique(),
)

tech_palette = {
    tech: palette[i]
    for i, tech in enumerate(sorted(flow_edges_gdf["tech"].unique()))
}

for tech, group in flow_edges_gdf.groupby("tech"):
    group = group.copy()

    linewidth = (
        0.5
        + 3.5 * (group["flow"] / group["flow"].max()) ** 0.5
    )

    group.plot(
        ax=ax,
        color=tech_palette[tech],
        linewidth=linewidth,
        alpha=0.75,
        label=tech,
    )

ax.set_title(
    "CANOE Geospatial Transport Flows",
    fontsize=14,
)

ax.set_axis_off()

ax.legend(
    title="Technology",
    frameon=False,
    loc="upper left",
    bbox_to_anchor=(1.02, 1.0),
)

plt.tight_layout()

run_tag = selected_run.name.replace(" ", "_")
db_tag = db_path.stem.replace(" ", "_")

fig_path = (
    figure_dir
    / f"{run_tag}_{db_tag}_transport_flows.png"
)

plt.savefig(
    fig_path,
    dpi=300,
    bbox_inches="tight",
)

plt.show()

print(f"\nSaved figure: {fig_path}")