import numpy as np
import pandas as pd
import seaborn as sns  # optional
import db_mgmt as mgmt
import geopandas as gpd
import contextily as ctx
import matplotlib.pyplot as plt
from pathlib import Path
from matplotlib.lines import Line2D
from shapely.geometry import LineString

import sys
from pathlib import Path

# CLI based call for selecting run and sites_dict
output_dir = Path("output_files")

# Find all valid runs (folders containing CANOE_geospatial.sqlite)
runs = sorted([
    d for d in output_dir.iterdir()
    if d.is_dir() and (d / "CANOE_geospatial.sqlite").exists()
])

if not runs:
    print(f"No valid runs found in {output_dir.resolve()}")
    sys.exit(1)

print("\nAvailable model runs:")
for i, r in enumerate(runs):
    size_mb = (r / "CANOE_geospatial.sqlite").stat().st_size / 1e6
    print(f"  [{i}] {r.name}  ({size_mb:.1f} MB)")

run_idx = input("\nSelect run index: ").strip()
try:
    selected_run = runs[int(run_idx)]
except (ValueError, IndexError):
    print("Invalid selection.")
    sys.exit(1)

db_path = selected_run / "CANOE_geospatial.sqlite"
print(f"  → {db_path}")

# Find available site dictionaries
site_files = sorted(Path(".").glob("sites_dict_*.csv"))

if not site_files:
    print("No sites_dict_*.csv files found.")
    sys.exit(1)

print("\nAvailable site dictionaries:")
for i, f in enumerate(site_files):
    print(f"  [{i}] {f.name}")

site_idx = input("\nSelect sites index: ").strip()
try:
    selected_sites = site_files[int(site_idx)]
except (ValueError, IndexError):
    print("Invalid selection.")
    sys.exit(1)

print(f"  → {selected_sites}\n")

# Figure output naming based on timestamp model run and resolution
run_tag = selected_run.name.replace(" ", "_")  # e.g. "2026-06-10_1801"
sites_tag = selected_sites.stem  # e.g. "sites_dict_1"
fig_stem = f"{run_tag}_{sites_tag}"  # e.g. "2026-06-10_1801_sites_dict_1"

# -------------------------------
# Load data
# -------------------------------
data = mgmt.sqlite_to_dfs(str(db_path))

sites = pd.read_csv(selected_sites)
sites['region'] = sites['site_id']
sites = sites.set_index('region', drop=True)

flowIn  = data['OutputFlowIn'].copy()
flowOut = data['OutputFlowOut'].copy()

# -------------------------------
# Parse region_from / region_to (vectorized)
# -------------------------------
flowOut = flowOut.copy()
flowOut['region_from'] = pd.NA
flowOut['region_to']   = pd.NA

mask_pair = ~flowOut['region'].isin(sites.index)
# split like "AAA-BBB" -> ["AAA","BBB"] only where needed
split = flowOut.loc[mask_pair, 'region'].str.split('-', n=1, expand=True)
flowOut.loc[mask_pair, 'region_from'] = split[0]
flowOut.loc[mask_pair, 'region_to']   = split[1]

# -------------------------------
# Helper: add lon/lat by index (region)
# -------------------------------
def add_site_coords(df, idx_col='region'):
    """Join lon/lat from `sites` using df[idx_col] as index."""
    if idx_col != df.index.name:
        df = df.set_index(idx_col, drop=False)
    return df.join(sites[['lon', 'lat']], how='left')

# -------------------------------
# Slice techs and attach coordinates
# -------------------------------
def slice_with_coords(df, tech_name):
    out = df.loc[df['tech'] == tech_name].copy()
    out = add_site_coords(out, idx_col='region')
    return out

elc_gen   = slice_with_coords(flowOut, 'ELC_GEN')
h2_plant  = slice_with_coords(flowOut, 'H2_PLANT')
co2_cap   = slice_with_coords(flowOut, 'CO2_CAP')
metoh_plant= slice_with_coords(flowOut, 'METOH_PLANT')
gsl_plant = slice_with_coords(flowOut, 'GSL_PLANT')
gsl_demand= slice_with_coords(flowOut, 'GSL_DEMAND')

# Transport links
def add_from_to_coords(df_links):
    """Map lon/lat for region_from and region_to."""
    df = df_links.copy()
    df['lon_from'] = df['region_from'].map(sites['lon'])
    df['lat_from'] = df['region_from'].map(sites['lat'])
    df['lon_to']   = df['region_to'].map(sites['lon'])
    df['lat_to']   = df['region_to'].map(sites['lat'])
    return df

h2_pipe   = add_from_to_coords(flowOut.loc[flowOut.tech=='H2_PIPE'])
gsl_pipe  = add_from_to_coords(flowOut.loc[flowOut.tech=='GSL_PIPE'])
meth_pipe = add_from_to_coords(flowOut.loc[flowOut.tech=='METH_PIPE'])
co2_pipe  = add_from_to_coords(flowOut.loc[flowOut.tech=='CO2_PIPE'])
elc_trans = add_from_to_coords(flowOut.loc[flowOut.tech=='ELC_TRANS'])

# -------------------------------
# Plot config (consistent dictionaries)
# -------------------------------
tech_points = {
    'Electricity' : (elc_gen,   'blue'),
    'H2'          : (h2_plant,  'green'),
    'CO2'         : (co2_cap,   'gray'),
    'Methanol'    : (metoh_plant,'orange'),
    'Gasoline'    : (gsl_plant, 'brown'),
}

tech_links = {
    'Electricity transport' : (elc_trans, 'blue'),
    'H2 transport'          : (h2_pipe,   'green'),
    'CO2 transport'         : (co2_pipe,  'gray'),
    'Methanol transport'    : (meth_pipe, 'orange'),
    'Gasoline transport'    : (gsl_pipe,  'brown'),
}

# Demand for marker 'x'
demand_pts = sites.loc[sites['demand'].fillna(0) > 0, ['lon', 'lat', 'demand']].copy()
if not demand_pts.empty:
    demand_pts['demand'] = demand_pts['demand'].astype(float)
    size_demand = (demand_pts['demand'] / demand_pts['demand'].max()) * 500
else:
    size_demand = pd.Series(dtype=float)

# -------------------------------
# Plot
# -------------------------------
fig, ax = plt.subplots(figsize=(10, 6))

# Base regions (faint)
ax.scatter(sites['lon'], sites['lat'], s=10, alpha=0.12, label='Region', c='gray')

# Demand markers
if not demand_pts.empty:
    ax.scatter(demand_pts['lon'], demand_pts['lat'], s=15, alpha=1, marker='x', label='Demand', c='black')

# Reproducible jitter
rng = np.random.default_rng(42)
jitter_deg = 1.5

# Determine global max flow for scaling bubble sizes consistently across techs
all_flows = pd.concat([
    df['flow'] for df, _ in tech_points.values()
    if not df.empty and 'flow' in df.columns
])
global_max_flow = all_flows.max()

# Plot each tech’s point bubbles
for name, (dfp, color) in tech_points.items():
    print(name)
    if dfp.empty or 'flow' not in dfp.columns:
        continue
    dfp_plot = dfp[['lon','lat','flow']].dropna().copy()
    if dfp_plot.empty:
        continue
    dfp_plot['flow'] = pd.to_numeric(dfp_plot['flow'], errors='coerce')
    dfp_plot = dfp_plot.dropna(subset=['flow'])
    if dfp_plot.empty:
        continue

    # jitter
    dfp_plot['lon_plot'] = dfp_plot['lon'] + rng.uniform(-jitter_deg, jitter_deg, size=len(dfp_plot))
    dfp_plot['lat_plot'] = dfp_plot['lat'] + rng.uniform(-jitter_deg, jitter_deg, size=len(dfp_plot))

    size = np.sqrt(dfp_plot['flow'] / global_max_flow) * 150
    ax.scatter(dfp_plot['lon_plot'], dfp_plot['lat_plot'], s=size, alpha=0.6, label=name, c=color)

# Transport arrows
arrow_jitter = 0.2
for name, (links, color) in tech_links.items():
    if links.empty:
        continue
    links_plot = links[['lon_from','lat_from','lon_to','lat_to']].dropna().copy()
    if links_plot.empty:
        continue
    for _, row in links_plot.iterrows():
        ax.annotate(
            '',
            xy=(row['lon_to'] + rng.uniform(-arrow_jitter, arrow_jitter),
                row['lat_to'] + rng.uniform(-arrow_jitter, arrow_jitter)),
            xytext=(row['lon_from'] + rng.uniform(-arrow_jitter, arrow_jitter),
                    row['lat_from'] + rng.uniform(-arrow_jitter, arrow_jitter)),
            arrowprops=dict(arrowstyle='->', color=color, lw=1, alpha=1)
        )

# Title, axes style
# ax.set_title('Sites, Demand, and Tech Flows')
# sns.despine(top=True, right=True, left=True, bottom=True)  # optional

# Legend (dedupe)
handles, labels = ax.get_legend_handles_labels()
by_label = dict(zip(labels, handles))

# Optional: include proxies so transport appears in legend even if no arrows visible initially
proxies = [Line2D([0],[0], color=c, lw=1, label=n) for n,(dfp,c) in tech_links.items()]
handles2 = list(by_label.values()) + proxies
labels2  = list(by_label.keys())   + [p.get_label() for p in proxies]
by_label2 = dict(zip(labels2, handles2))
ax.legend(by_label2.values(), by_label2.keys(), title='Legend', frameon=False, loc='upper right', bbox_to_anchor=(1.2, 0.75))

ax.set_xticks([])
ax.set_yticks([])

sns.despine(top=True, right=True, bottom=True, left=True)
plt.tight_layout()
plt.savefig(f'figures/{fig_stem}_schematic.png', dpi=300)
plt.show()

# --- Assuming you already have: sites, flowOut, etc. as before ---

# Convert sites to GeoDataFrame
sites_gdf = gpd.GeoDataFrame(
    sites,
    geometry=gpd.points_from_xy(sites['lon'], sites['lat']),
    crs="EPSG:4326"  # WGS84 lat/lon
)

# For plotting on a tile map (which uses Web Mercator)
sites_web = sites_gdf.to_crs(epsg=3857)

# Create figure
fig, ax = plt.subplots(figsize=(6, 8))

# --- Base map ---
sites_web.plot(ax=ax, color='gray', markersize=5, alpha=0.2, label='Region')

# Add contextily basemap (Canada view)
ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)

# --- Demand points ---
if not demand_pts.empty:
    demand_pts_gdf = gpd.GeoDataFrame(
        demand_pts,
        geometry=gpd.points_from_xy(demand_pts['lon'], demand_pts['lat']),
        crs="EPSG:4326"
    ).to_crs(epsg=3857)
    ax.scatter(
        demand_pts_gdf.geometry.x,
        demand_pts_gdf.geometry.y,
        s=15,
        color='black',
        alpha=1,
        marker='x',
        label='Demand'
    )

# --- Plot techs (loop from previous cleaned code) ---
rng = np.random.default_rng(42)
jitter_deg = 1.5

for name, (dfp, color) in tech_points.items():
    if dfp.empty or 'flow' not in dfp.columns:
        continue
    dfp = dfp.dropna(subset=['lon', 'lat', 'flow']).copy()
    if dfp.empty:
        continue

    dfp['flow'] = pd.to_numeric(dfp['flow'], errors='coerce')
    dfp = dfp.dropna(subset=['flow'])
    if dfp.empty:
        continue

    dfp['lon_plot'] = dfp['lon'] + rng.uniform(-jitter_deg, jitter_deg, size=len(dfp))
    dfp['lat_plot'] = dfp['lat'] + rng.uniform(-jitter_deg, jitter_deg, size=len(dfp))
    dfp_gdf = gpd.GeoDataFrame(
        dfp,
        geometry=gpd.points_from_xy(dfp['lon_plot'], dfp['lat_plot']),
        crs="EPSG:4326"
    ).to_crs(epsg=3857)

    sizes = np.sqrt(dfp['flow'] / global_max_flow) * 150
    ax.scatter(
        dfp_gdf.geometry.x,
        dfp_gdf.geometry.y,
        s=sizes,
        color=color,
        alpha=0.6,
        label=name
    )

# --- Arrows for transport (optional, could also be simplified with LineCollection) ---
for name, (links, color) in tech_links.items():
    if links.empty:
        continue
    links_plot = links[['lon_from','lat_from','lon_to','lat_to']].dropna().copy()
    for _, row in links_plot.iterrows():
        line = gpd.GeoSeries(
            [LineString([(row['lon_from'], row['lat_from']), (row['lon_to'], row['lat_to'])])],
            crs="EPSG:4326"
        ).to_crs(epsg=3857)
        ax.plot(*line.geometry[0].xy, color=color, lw=1, alpha=0.7)

# --- Styling ---
ax.set_title('', fontsize=14, pad=12)
# ax.legend(title='Legend', frameon=False, loc='upper left', bbox_to_anchor=(1.25, 1))

# Remove all gridlines, ticks, and spines
ax.grid(False)
for spine in ax.spines.values():
    spine.set_visible(False)


# Optional: include proxies so transport appears in legend even if no arrows visible initially
proxies = [Line2D([0],[0], color=c, lw=1, label=n) for n,(dfp,c) in tech_links.items()]
handles2 = list(by_label.values()) + proxies
labels2  = list(by_label.keys())   + [p.get_label() for p in proxies]
by_label2 = dict(zip(labels2, handles2))
# ax.legend(by_label2.values(), by_label2.keys(), title='', frameon=False, loc='upper right', bbox_to_anchor=(1.15, 1.0), fontsize=14)

ax.set_xticks([])
ax.set_yticks([])

plt.tight_layout()
plt.savefig(f'figures/{fig_stem}_basemap.svg', dpi=300)
plt.show()