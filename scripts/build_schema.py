"""
build_schema.py

Encode a selected Geospatial-CANOE graph and road-connectivity product into a
CANOE/TEMOA-compatible SQLite database.

This script is the schema-building endpoint of the geospatial preprocessing
workflow. It preserves the Notebook 7 v3 logic while removing notebook-only
inspection/debug cells and replacing hardcoded basemap/connectivity choices with
runtime selection.

Inputs:
    data_files/processed/basemaps/canada_basemap_*deg_*.gpkg
    data_files/processed/graph/*_graph_nodes.gpkg
    data_files/processed/graph/*_graph_edges.csv
    data_files/processed/road_connectivity/*_road_edge_connections.csv
    data_files/processed/road_connectivity/*_road_edges.gpkg
    data_files/CANOE_geospatial.sqlite
    data_files/canoe_dataset_schema.sql
    data_files/sites_full.csv
    data_files/demand.csv
    data_files/processed/emissions/co2_large_facilities_2024/AirEmissions_GHG_2024.json
    or data_files/processed/emissions/co2_large_facilities_2024/Greenhouse gas emissions from large facilities - 2024.csv
    data_files/transport_techs.csv
    data_files/generation_efficiency.csv
    data_files/techs.csv
    data_files/commodities.csv

Outputs:
    data_files/processed/schema/CANOE_geospatial_{BASEMAP_STEM}_roads_{CONNECTION_METHOD}.sqlite
"""

from dataclasses import dataclass
from pathlib import Path
import math
import sys

import geopandas as gpd
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import db_mgmt


# =============================================================================
# Project paths
# =============================================================================

DATA_FILES = PROJECT_ROOT / "data_files"

RAW_BASEMAPS = DATA_FILES / "raw" / "basemaps"
PROCESSED_BASEMAPS = DATA_FILES / "processed" / "basemaps"
PROCESSED_GRAPH = DATA_FILES / "processed" / "graph"
PROCESSED_ROAD_CONNECTIVITY = DATA_FILES / "processed" / "road_connectivity"
PROCESSED_SCHEMA = DATA_FILES / "processed" / "schema"

RAW_BASEMAP_PATH = RAW_BASEMAPS / "lpr_000b21a_e.shp"
RAW_SCHEMA_PATH = DATA_FILES / "canoe_dataset_schema.sql"
BASELINE_SQLITE_PATH = DATA_FILES / "CANOE_geospatial.sqlite"

SITES_PATH = DATA_FILES / "sites_full.csv"
DEMAND_PATH = DATA_FILES / "demand.csv"

RAW_EMISSIONS_DIR = (
    DATA_FILES
    / "raw"
    / "emissions"
    / "co2_large_facilities_2024"
)

PROCESSED_EMISSIONS_DIR = (
    DATA_FILES
    / "processed"
    / "emissions"
    / "co2_large_facilities_2024"
)

CO2_SOURCE_CANDIDATES = [
    # Preferred raw source files from the download/batch stage.
    RAW_EMISSIONS_DIR / "AirEmissions_GHG_2024.json",
    RAW_EMISSIONS_DIR / "AirEmissions_GHG_2024.geojson",
    RAW_EMISSIONS_DIR / "Greenhouse gas emissions from large facilities - 2024.csv",

    # Backward-compatible processed/intermediate alternatives.
    PROCESSED_EMISSIONS_DIR / "AirEmissions_GHG_2024.json",
    PROCESSED_EMISSIONS_DIR / "AirEmissions_GHG_2024.geojson",
    PROCESSED_EMISSIONS_DIR / "Greenhouse gas emissions from large facilities - 2024.csv",
    PROCESSED_EMISSIONS_DIR / "co2_large_facilities_2024_clean.gpkg",
]
TRANSPORT_TECHS_PATH = DATA_FILES / "transport_techs.csv"
GEN_EFFICIENCIES_PATH = DATA_FILES / "generation_efficiency.csv"
TECHNOLOGIES_PATH = DATA_FILES / "techs.csv"
COMMODITIES_PATH = DATA_FILES / "commodities.csv"

DATA_ID = "GEO001"
STAT_CANADA_LAMPERT_CRS = "EPSG:3347"
CO2_KT_TO_T_FACTOR = 1000.0

PLANT_TECHS = {"GSL_PLANT", "METOH_PLANT"}
NODE_COSTVARIABLE_TECHS = ["ELC_GEN", "CO2_CAP", "GSL_BACKUP"]
NODE_COSTINVEST_TECHS = ["ELC_GEN", "CO2_CAP"]

# ETL cost-curve parameters inherited from the legacy workflow, but applied
# only to the selected geospatial graph nodes/edges in this script.
# This avoids reintroducing the legacy grouped-site topology.
ETL_COST_PARAMETERS = {
    "GSL_PLANT": {"a": 23334.0, "b": -0.4, "upper_vol": 1_000_000.0},
    "METOH_PLANT": {"a": 4500.0, "b": -0.3663, "upper_vol": 1_000_000.0},
    "GSL_PIPE": {"a": 45000.0, "b": -0.3, "upper_vol": 1_000_000.0},
    "CO2_PIPE": {"a": 45000.0, "b": -0.3, "upper_vol": 1_000_000.0},
    "METOH_PIPE": {"a": 45000.0, "b": -0.3, "upper_vol": 1_000_000.0},
    "H2_PIPE": {"a": 45000.0, "b": -0.3, "upper_vol": 1_000_000.0},
    "ELC_TRANS": {"a": 2000.0, "b": -0.3, "upper_vol": 1_000_000.0},
}
ETL_RESOLUTION = 5
ETL_SPACING = "log"


@dataclass(frozen=True)
class SchemaConfig:
    basemap_stem: str
    connection_method: str
    basemap_path: Path
    graph_node_path: Path
    graph_edge_path: Path
    road_edge_connections_path: Path
    road_edges_gpkg_path: Path
    road_region_overlay_path: Path
    output_sqlite_path: Path


@dataclass
class LoadedInputs:
    basemap: gpd.GeoDataFrame
    graph_nodes: gpd.GeoDataFrame
    graph_edges: pd.DataFrame
    road_edge_connections: pd.DataFrame
    road_edges_gdf: gpd.GeoDataFrame
    db: dict[str, pd.DataFrame]
    sites_raw: pd.DataFrame
    demand_raw: pd.DataFrame
    co2_raw: gpd.GeoDataFrame
    transport_techs_raw: pd.DataFrame
    gen_efficiencies_raw: pd.DataFrame
    technologies_raw: pd.DataFrame
    commodities_raw: pd.DataFrame


@dataclass
class CanonicalLinks:
    region_table: pd.DataFrame
    pipeline_links: pd.DataFrame
    road_links: pd.DataFrame
    valid_node_regions: set[str]
    valid_pipeline_edge_regions: set[str]
    valid_road_edge_regions: set[str]


@dataclass
class TechSpecs:
    pipeline_tech_specs: pd.DataFrame
    truck_tech_specs: pd.DataFrame
    transmission_tech_specs: pd.DataFrame
    pipe_techs: set[str]
    truck_techs: set[str]
    trans_techs: set[str]
    transport_techs: set[str]


@dataclass
class SnappedInputs:
    site_attributes: pd.DataFrame
    co2_facilities: gpd.GeoDataFrame
    co2_raw: gpd.GeoDataFrame


# =============================================================================
# Selection and validation helpers
# =============================================================================

def select_from_options(options: list[str], label: str) -> str:
    if not options:
        raise ValueError(f"No options available for {label}.")

    print(f"\nAvailable {label} options:")
    for i, option in enumerate(options):
        print(f"  [{i}] {option}")

    choice = input(f"\nSelect {label} index: ").strip()

    try:
        return options[int(choice)]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Invalid {label} selection: {choice}") from exc


def discover_basemap_stems() -> list[str]:
    basemap_paths = sorted(
        PROCESSED_BASEMAPS.glob("canada_basemap_*deg_*.gpkg")
    )
    return [path.stem for path in basemap_paths]


def select_schema_configuration() -> SchemaConfig:
    basemap_stem = select_from_options(
        discover_basemap_stems(),
        "basemap",
    )

    connection_method = select_from_options(
        ["weak", "strong"],
        "road connection method",
    )

    config = SchemaConfig(
        basemap_stem=basemap_stem,
        connection_method=connection_method,
        basemap_path=PROCESSED_BASEMAPS / f"{basemap_stem}.gpkg",
        graph_node_path=PROCESSED_GRAPH / f"{basemap_stem}_graph_nodes.gpkg",
        graph_edge_path=PROCESSED_GRAPH / f"{basemap_stem}_graph_edges.csv",
        road_edge_connections_path=(
            PROCESSED_ROAD_CONNECTIVITY
            / f"{basemap_stem}_road_connectivity_{connection_method}_road_edge_connections.csv"
        ),
        road_edges_gpkg_path=(
            PROCESSED_ROAD_CONNECTIVITY
            / f"{basemap_stem}_road_connectivity_{connection_method}_road_edges.gpkg"
        ),
        road_region_overlay_path=(
            PROCESSED_ROAD_CONNECTIVITY
            / f"{basemap_stem}_road_connectivity_road_region_overlay.gpkg"
        ),
        output_sqlite_path=(
            PROCESSED_SCHEMA
            / f"CANOE_geospatial_{basemap_stem}_{connection_method}.sqlite"
        ),
    )

    ensure_baseline_sqlite_exists()
    validate_required_paths(config)
    return config

def ensure_baseline_sqlite_exists() -> None:
    if BASELINE_SQLITE_PATH.exists():
        return

    if not RAW_SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Missing raw schema SQL: {RAW_SCHEMA_PATH}")

    print("\nBaseline SQLite not found.")
    print(f"Creating baseline SQLite from: {RAW_SCHEMA_PATH}")
    print(f"Output baseline SQLite: {BASELINE_SQLITE_PATH}")

    db_mgmt.convert_sql_to_sqlite(
        RAW_SCHEMA_PATH,
        BASELINE_SQLITE_PATH,
    )

def discover_co2_source_path() -> Path:
    for candidate in CO2_SOURCE_CANDIDATES:
        if candidate.exists():
            return candidate

    print("\nMissing CO2 source file. Checked:")
    for candidate in CO2_SOURCE_CANDIDATES:
        print(f"  {candidate}")

    raise FileNotFoundError("No supported CO2 source file found.")


def load_co2_facilities(path: Path) -> gpd.GeoDataFrame:
    """Load and normalize the 2024 large-facility GHG file.

    The raw source may be GeoJSON/JSON, CSV, or a previously cleaned GPKG.
    Downstream schema-building logic expects these canonical columns:
    facility_id, latitude, longitude, emissions_kt_co2e_per_year.
    """
    suffix = path.suffix.lower()

    if suffix in {".json", ".geojson", ".gpkg"}:
        gdf = gpd.read_file(path)
    elif suffix == ".csv":
        df = pd.read_csv(path)
        latitude_col = first_existing_column(df, ["Latitude", "latitude", "lat"])
        longitude_col = first_existing_column(df, ["Longitude", "longitude", "lon"])
        if latitude_col is None or longitude_col is None:
            raise ValueError(
                f"CSV CO2 file must contain latitude/longitude columns: {path}"
            )

        df[latitude_col] = pd.to_numeric(df[latitude_col], errors="coerce")
        df[longitude_col] = pd.to_numeric(df[longitude_col], errors="coerce")
        df = df.dropna(subset=[latitude_col, longitude_col]).copy()

        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df[longitude_col], df[latitude_col]),
            crs="EPSG:4326",
        )
    else:
        raise ValueError(f"Unsupported CO2 source file type: {path}")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    gdf = gdf.rename(
        columns={
            "Name": "facility_name",
            "Company": "company",
            "Facility type": "facility_type",
            "City": "city",
            "Province": "province",
            "Latitude": "latitude",
            "Longitude": "longitude",
            "Total GHG emissions": "emissions_kt_co2e_per_year",
            "Year": "year",
            "Report year": "report_year",
        }
    )

    latitude_col = first_existing_column(gdf, ["latitude", "lat"])
    longitude_col = first_existing_column(gdf, ["longitude", "lon"])
    emissions_col = first_existing_column(
        gdf,
        [
            "emissions_kt_co2e_per_year",
            "Total GHG emissions",
            "total_ghg_emissions",
            "co2",
        ],
    )

    if latitude_col is None or longitude_col is None:
        raise ValueError(
            "CO2 source is missing latitude/longitude fields after normalization."
        )
    if emissions_col is None:
        raise ValueError(
            "CO2 source is missing a GHG emissions field after normalization."
        )

    gdf["latitude"] = pd.to_numeric(gdf[latitude_col], errors="coerce")
    gdf["longitude"] = pd.to_numeric(gdf[longitude_col], errors="coerce")
    gdf["emissions_kt_co2e_per_year"] = pd.to_numeric(
        gdf[emissions_col],
        errors="coerce",
    ).fillna(0)

    if "facility_id" not in gdf.columns:
        gdf["facility_id"] = [f"GHG2024_{i:05d}" for i in range(len(gdf))]

    gdf = gdf.dropna(subset=["latitude", "longitude"]).copy()

    if "geometry" not in gdf.columns or gdf.geometry.isna().all():
        gdf = gpd.GeoDataFrame(
            gdf,
            geometry=gpd.points_from_xy(gdf["longitude"], gdf["latitude"]),
            crs="EPSG:4326",
        )

    print(f"CO2 source file: {path.name}")
    print(f"CO2 source rows loaded: {len(gdf):,}")

    return gdf


def first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def validate_required_paths(config: SchemaConfig) -> None:
    required_paths = {
        "raw_basemap": RAW_BASEMAP_PATH,
        "raw_schema": RAW_SCHEMA_PATH,
        "baseline_sqlite": BASELINE_SQLITE_PATH,
        "sites": SITES_PATH,
        "demand": DEMAND_PATH,
        "co2_source": discover_co2_source_path(),
        "transport_techs": TRANSPORT_TECHS_PATH,
        "generation_efficiency": GEN_EFFICIENCIES_PATH,
        "techs": TECHNOLOGIES_PATH,
        "commodities": COMMODITIES_PATH,
        "processed_basemap": config.basemap_path,
        "graph_nodes": config.graph_node_path,
        "graph_edges": config.graph_edge_path,
        "road_edge_connections": config.road_edge_connections_path,
        "road_edges_gpkg": config.road_edges_gpkg_path,
        "road_region_overlay": config.road_region_overlay_path,
    }

    missing_paths = {
        name: path
        for name, path in required_paths.items()
        if not path.exists()
    }

    if missing_paths:
        for name, path in missing_paths.items():
            print(f"Missing {name}: {path}")
        raise FileNotFoundError("One or more required input files are missing.")


def sort_region_ids(region_series: pd.Series) -> pd.Series:
    return region_series.str.extract(r"R(\d+)")[0].astype(int)


# =============================================================================
# Loading and canonical graph/link construction
# =============================================================================

def load_inputs(config: SchemaConfig) -> LoadedInputs:
    print("\nLoading selected geospatial and CANOE inputs...")

    inputs = LoadedInputs(
        basemap=gpd.read_file(config.basemap_path),
        graph_nodes=gpd.read_file(config.graph_node_path),
        graph_edges=pd.read_csv(config.graph_edge_path),
        road_edge_connections=pd.read_csv(config.road_edge_connections_path),
        road_edges_gdf=gpd.read_file(config.road_edges_gpkg_path),
        db=db_mgmt.sqlite_to_dfs(BASELINE_SQLITE_PATH),
        sites_raw=pd.read_csv(SITES_PATH),
        demand_raw=pd.read_csv(DEMAND_PATH),
        co2_raw=load_co2_facilities(discover_co2_source_path()),
        transport_techs_raw=pd.read_csv(TRANSPORT_TECHS_PATH),
        gen_efficiencies_raw=pd.read_csv(GEN_EFFICIENCIES_PATH),
        technologies_raw=pd.read_csv(TECHNOLOGIES_PATH),
        commodities_raw=pd.read_csv(COMMODITIES_PATH),
    )

    print(f"Basemap regions: {len(inputs.basemap):,}")
    print(f"Graph nodes: {len(inputs.graph_nodes):,}")
    print(f"Graph edges: {len(inputs.graph_edges):,}")
    print(f"Road edge connections: {len(inputs.road_edge_connections):,}")
    print(f"Road edge geometries: {len(inputs.road_edges_gdf):,}")
    print(f"Baseline database tables: {len(inputs.db):,}")
    print(f"Raw CO2 facilities: {len(inputs.co2_raw):,}")

    return inputs


def build_canonical_links(
    graph_nodes: gpd.GeoDataFrame,
    graph_edges: pd.DataFrame,
    road_edge_connections: pd.DataFrame,
    config: SchemaConfig,
) -> CanonicalLinks:
    region_table = (
        graph_nodes[["region"]]
        .drop_duplicates()
        .sort_values("region", key=sort_region_ids)
        .reset_index(drop=True)
    )
    region_table["notes"] = f"{config.basemap_stem} CANOE geospatial graph node"

    road_links = road_edge_connections.loc[
        road_edge_connections["has_road_connection"]
    ].copy()

    road_links = road_links[
        [
            "edge_region",
            "region_from",
            "region_to",
            "direction",
            "connection_method",
            "distance_km",
            "lon_from",
            "lat_from",
            "lon_to",
            "lat_to",
        ]
    ].copy()

    road_links = (
        road_links
        .drop_duplicates(subset=["edge_region"])
        .reset_index(drop=True)
    )
    road_links["canoe_region"] = road_links["edge_region"]

    pipeline_links = graph_edges.copy()
    pipeline_links["canoe_region"] = pipeline_links["edge_region"]

    canonical = CanonicalLinks(
        region_table=region_table,
        pipeline_links=pipeline_links,
        road_links=road_links,
        valid_node_regions=set(region_table["region"]),
        valid_pipeline_edge_regions=set(pipeline_links["canoe_region"]),
        valid_road_edge_regions=set(road_links["canoe_region"]),
    )

    validate_canonical_links(canonical, graph_nodes)

    print("\nCanonical topology:")
    print(f"Node regions: {len(region_table):,}")
    print(f"Candidate pipeline/transmission links: {len(pipeline_links):,}")
    print(f"Road links ({config.connection_method}): {len(road_links):,}")

    return canonical


def validate_canonical_links(
    canonical: CanonicalLinks,
    graph_nodes: gpd.GeoDataFrame,
) -> None:
    region_table = canonical.region_table
    pipeline_links = canonical.pipeline_links
    road_links = canonical.road_links

    assert "R-999" not in set(region_table["region"])
    assert len(region_table) == region_table["region"].nunique()
    assert len(region_table) == len(graph_nodes)
    assert pipeline_links["canoe_region"].nunique() == len(pipeline_links)
    assert road_links["canoe_region"].nunique() == len(road_links)
    assert set(pipeline_links["region_from"]).issubset(canonical.valid_node_regions)
    assert set(pipeline_links["region_to"]).issubset(canonical.valid_node_regions)
    assert set(road_links["region_from"]).issubset(canonical.valid_node_regions)
    assert set(road_links["region_to"]).issubset(canonical.valid_node_regions)
    assert pipeline_links["distance_km"].notna().all()
    assert road_links["distance_km"].notna().all()
    assert (pipeline_links["distance_km"] > 0).all()
    assert (road_links["distance_km"] > 0).all()
    assert pipeline_links["canoe_region"].str.contains("-", regex=False).all()
    assert road_links["canoe_region"].str.contains("-", regex=False).all()


# =============================================================================
# Technology and point snapping helpers
# =============================================================================

def build_tech_specs(transport_techs_raw: pd.DataFrame) -> TechSpecs:
    required_transport_cols = {
        "tech",
        "input_comm",
        "output_comm",
        "cost_per_km",
        "intercept_cost_per_km",
    }

    missing_transport_cols = required_transport_cols - set(transport_techs_raw.columns)

    if missing_transport_cols:
        raise ValueError(
            "transport_techs.csv is missing required columns: "
            f"{sorted(missing_transport_cols)}"
        )

    pipeline_tech_specs = transport_techs_raw.loc[
        transport_techs_raw["tech"].str.endswith("_PIPE")
    ].copy()

    truck_tech_specs = transport_techs_raw.loc[
        transport_techs_raw["tech"].str.endswith("_TRUCK")
    ].copy()

    transmission_tech_specs = transport_techs_raw.loc[
        transport_techs_raw["tech"] == "ELC_TRANS"
    ].copy()

    assert not pipeline_tech_specs.empty, "No *_PIPE rows found in transport_techs.csv."
    assert not truck_tech_specs.empty, "No *_TRUCK rows found in transport_techs.csv."
    assert not transmission_tech_specs.empty, "No ELC_TRANS row found in transport_techs.csv."

    specs = TechSpecs(
        pipeline_tech_specs=pipeline_tech_specs,
        truck_tech_specs=truck_tech_specs,
        transmission_tech_specs=transmission_tech_specs,
        pipe_techs=set(pipeline_tech_specs["tech"]),
        truck_techs=set(truck_tech_specs["tech"]),
        trans_techs=set(transmission_tech_specs["tech"]),
        transport_techs=(
            set(pipeline_tech_specs["tech"])
            | set(truck_tech_specs["tech"])
            | set(transmission_tech_specs["tech"])
        ),
    )

    print("\nTransport technologies:")
    print(f"Pipeline techs: {sorted(specs.pipe_techs)}")
    print(f"Truck techs: {sorted(specs.truck_techs)}")
    print(f"Transmission techs: {sorted(specs.trans_techs)}")

    return specs


def snap_points_to_graph_nodes(
    points: pd.DataFrame | gpd.GeoDataFrame,
    graph_nodes: gpd.GeoDataFrame,
    lon_col: str = "lon",
    lat_col: str = "lat",
) -> pd.DataFrame:
    points_df = (
        pd.DataFrame(points.drop(columns="geometry"))
        if isinstance(points, gpd.GeoDataFrame)
        else points.copy()
    )

    points_gdf = gpd.GeoDataFrame(
        points_df,
        geometry=gpd.points_from_xy(points_df[lon_col], points_df[lat_col]),
        crs="EPSG:4326",
    )

    nodes_gdf = graph_nodes[["region", "geometry"]].copy()

    snapped = gpd.sjoin(
        points_gdf,
        nodes_gdf,
        how="left",
        predicate="within",
    ).drop(columns="index_right")

    unmatched_mask = snapped["region"].isna()
    n_unmatched = int(unmatched_mask.sum())

    if n_unmatched > 0:
        unmatched_gdf = points_gdf.loc[unmatched_mask].copy()

        snapped_nearest = gpd.sjoin_nearest(
            unmatched_gdf.to_crs(STAT_CANADA_LAMPERT_CRS),
            nodes_gdf.to_crs(STAT_CANADA_LAMPERT_CRS),
            how="left",
            distance_col="snap_distance_m",
        ).to_crs("EPSG:4326")

        snapped_nearest = snapped_nearest.loc[
            ~snapped_nearest.index.duplicated(keep="first")
        ]

        snapped.loc[unmatched_mask, "region"] = snapped_nearest["region"].values

        print(
            f"Nearest fallback assigned {n_unmatched:,} points "
            f"(max {snapped_nearest['snap_distance_m'].max():,.0f} m, "
            f"mean {snapped_nearest['snap_distance_m'].mean():,.0f} m)."
        )

    return pd.DataFrame(snapped.drop(columns="geometry"))


def build_site_attributes(
    inputs: LoadedInputs,
    canonical: CanonicalLinks,
) -> SnappedInputs:
    print("\nSnapping demand, electricity, and CO2 inputs to selected graph nodes...")

    raw_points_non_co2 = pd.concat(
        [inputs.sites_raw, inputs.demand_raw],
        ignore_index=True,
    )

    numeric_cols = raw_points_non_co2.select_dtypes(include="number").columns
    raw_points_non_co2[numeric_cols] = raw_points_non_co2[numeric_cols].fillna(0)

    snapped_non_co2 = snap_points_to_graph_nodes(
        raw_points_non_co2,
        inputs.graph_nodes,
    )

    co2_facilities = inputs.co2_raw.copy()
    co2_facilities["co2"] = pd.to_numeric(
        co2_facilities["emissions_kt_co2e_per_year"],
        errors="coerce",
    ).fillna(0) * CO2_KT_TO_T_FACTOR

    co2_facilities = co2_facilities.loc[co2_facilities["co2"] > 0].copy()

    snapped_co2 = snap_points_to_graph_nodes(
        co2_facilities,
        inputs.graph_nodes,
        lon_col="longitude",
        lat_col="latitude",
    )

    co2_region = (
        snapped_co2
        .groupby("region", as_index=False)
        .agg(
            co2=("co2", "sum"),
            n_co2_facilities=("facility_id", "count"),
        )
    )

    site_attributes_non_co2 = (
        snapped_non_co2
        .groupby("region", as_index=False)
        .agg(
            LCOE=("LCOE", "mean"),
            max_elc=("max_elec", "sum"),
            demand=("demand", "sum"),
        )
    )

    site_attributes = (
        canonical.region_table[["region"]]
        .merge(site_attributes_non_co2, on="region", how="left")
        .merge(co2_region, on="region", how="left")
        .fillna(
            {
                "LCOE": 0,
                "max_elc": 0,
                "demand": 0,
                "co2": 0,
                "n_co2_facilities": 0,
            }
        )
    )

    site_attributes["co2_cost"] = 50

    print(f"Snapped region rows: {len(site_attributes):,}")
    print(f"Regions with demand: {(site_attributes['demand'] > 0).sum():,}")
    print(f"Regions with CO2: {(site_attributes['co2'] > 0).sum():,}")
    print(f"CO2 facilities mapped: {int(site_attributes['n_co2_facilities'].sum()):,}")
    print(f"Total CO2 mapped: {site_attributes['co2'].sum():,.2f} t CO2e/year")
    print(f"Regions with electricity potential: {(site_attributes['max_elc'] > 0).sum():,}")
    print(
        f"CO2 facilities dropped (zero/negative emissions): "
        f"{len(inputs.co2_raw) - len(co2_facilities):,}"
    )

    return SnappedInputs(
        site_attributes=site_attributes,
        co2_facilities=co2_facilities,
        co2_raw=inputs.co2_raw,
    )


# =============================================================================
# Node table rebuilds
# =============================================================================

def rebuild_demand_and_capacity(
    db_encoded: dict[str, pd.DataFrame],
    site_attributes: pd.DataFrame,
) -> None:
    demand_sites = site_attributes.loc[site_attributes["demand"] > 0].reset_index(drop=True)

    db_encoded["Demand"] = pd.DataFrame(
        {
            "region": demand_sites["region"],
            "period": 1,
            "commodity": "d_gsl",
            "demand": demand_sites["demand"],
            "units": None,
            "notes": "Demand snapped to selected geospatial graph node",
            "data_source": None,
            "dq_cred": None,
            "dq_geog": None,
            "dq_struc": None,
            "dq_tech": None,
            "dq_time": None,
            "data_id": DATA_ID,
        }
    )

    db_encoded["LimitCapacity"] = pd.concat(
        [
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "period": 1,
                    "tech_or_group": "CO2_CAP",
                    "operator": "le",
                    "capacity": site_attributes["co2"],
                    "units": "t",
                    "notes": "CO2 capacity snapped to selected geospatial graph node",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "period": 1,
                    "tech_or_group": "ELC_GEN",
                    "operator": "le",
                    "capacity": site_attributes["max_elc"],
                    "units": None,
                    "notes": "Electricity potential snapped to selected geospatial graph node",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
        ],
        ignore_index=True,
    )

    assert db_encoded["Demand"]["region"].nunique() == len(db_encoded["Demand"])
    assert db_encoded["Demand"]["demand"].gt(0).all()
    assert len(db_encoded["LimitCapacity"]) == 2 * len(site_attributes)

    print(f"Demand rows: {len(db_encoded['Demand']):,}")
    print(f"LimitCapacity rows: {len(db_encoded['LimitCapacity']):,}")


def rebuild_node_costs(
    db_encoded: dict[str, pd.DataFrame],
    site_attributes: pd.DataFrame,
) -> None:
    node_costvariable = pd.concat(
        [
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "period": 1,
                    "tech": "ELC_GEN",
                    "vintage": 1,
                    "cost": site_attributes["LCOE"],
                    "units": "M$/MWh",
                    "notes": "Electricity generation cost snapped to selected graph node",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "period": 1,
                    "tech": "CO2_CAP",
                    "vintage": 1,
                    "cost": site_attributes["co2_cost"],
                    "units": "M$/t",
                    "notes": "CO2 capture cost snapped to selected graph node",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "period": 1,
                    "tech": "GSL_BACKUP",
                    "vintage": 1,
                    "cost": 500000,
                    "units": "M$/MWh",
                    "notes": "Backup gasoline supply cost",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
        ],
        ignore_index=True,
    )

    db_encoded["CostVariable"] = db_encoded["CostVariable"].loc[
        ~db_encoded["CostVariable"]["tech"].isin(NODE_COSTVARIABLE_TECHS)
    ].copy()
    db_encoded["CostVariable"] = pd.concat(
        [db_encoded["CostVariable"], node_costvariable],
        ignore_index=True,
    )

    node_costinvest = pd.concat(
        [
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "tech": "ELC_GEN",
                    "vintage": 1,
                    "cost": 1000,
                    "units": None,
                    "notes": "Electricity generation fixed investment cost",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
            pd.DataFrame(
                {
                    "region": site_attributes["region"],
                    "tech": "CO2_CAP",
                    "vintage": 1,
                    "cost": 1000,
                    "units": None,
                    "notes": "CO2 capture fixed investment cost",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            ),
        ],
        ignore_index=True,
    )

    db_encoded["CostInvest"] = db_encoded["CostInvest"].loc[
        ~db_encoded["CostInvest"]["tech"].isin(NODE_COSTINVEST_TECHS)
    ].copy()
    db_encoded["CostInvest"] = pd.concat(
        [db_encoded["CostInvest"], node_costinvest],
        ignore_index=True,
    )

    print(f"Node CostVariable rows added: {len(node_costvariable):,}")
    print(f"Node CostInvest rows added: {len(node_costinvest):,}")


def build_input_split(
    regions: pd.Series,
    tech: str,
    input_comm: list[str],
    proportion: list[float],
    operator: str = "ge",
) -> pd.DataFrame:
    if len(input_comm) != len(proportion):
        raise ValueError("input_comm and proportion must have same length.")

    if not math.isclose(sum(proportion), 1.0, rel_tol=1e-6):
        raise ValueError(
            f"Proportions for {tech} sum to {sum(proportion):.10f}, expected 1.0."
        )

    rows = []
    for comm, prop in zip(input_comm, proportion):
        rows.append(
            pd.DataFrame(
                {
                    "region": regions,
                    "period": 1,
                    "input_comm": comm,
                    "tech": tech,
                    "operator": operator,
                    "proportion": prop,
                    "notes": None,
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            )
        )

    return pd.concat(rows, ignore_index=True)


def rebuild_input_splits(
    db_encoded: dict[str, pd.DataFrame],
    site_attributes: pd.DataFrame,
) -> None:
    node_regions = site_attributes["region"]

    gsl_input_split = build_input_split(
        node_regions,
        "GSL_PLANT",
        ["ch3oh", "h2"],
        [0.997782705, 0.002217295],
    )

    metoh_input_split = build_input_split(
        node_regions,
        "METOH_PLANT",
        ["co2", "h2", "elc"],
        [0.79230333899, 0.10865874363, 0.09903791737],
    )

    db_encoded["LimitTechInputSplitAnnual"] = pd.concat(
        [gsl_input_split, metoh_input_split],
        ignore_index=True,
    )

    print(f"LimitTechInputSplitAnnual rows: {len(db_encoded['LimitTechInputSplitAnnual']):,}")


def rebuild_node_efficiency(
    db_encoded: dict[str, pd.DataFrame],
    site_attributes: pd.DataFrame,
    gen_efficiencies_raw: pd.DataFrame,
) -> None:
    efficiency_rows = []

    for row in gen_efficiencies_raw.itertuples(index=False):
        if row.tech == "GSL_BACKUP":
            regions = site_attributes.loc[
                site_attributes["demand"] > 0,
                "region",
            ].reset_index(drop=True)
        else:
            regions = site_attributes["region"].reset_index(drop=True)

        efficiency_rows.append(
            pd.DataFrame(
                {
                    "region": regions,
                    "input_comm": row.input_comm,
                    "tech": row.tech,
                    "vintage": 1,
                    "output_comm": row.output_comm,
                    "efficiency": row.efficiency,
                    "notes": "Node-level efficiency rebuilt from snapped graph regions",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            )
        )

    node_efficiency = pd.concat(efficiency_rows, ignore_index=True)

    demand_regions = db_encoded["Demand"]["region"].drop_duplicates().reset_index(drop=True)

    gsl_demand_efficiency = pd.DataFrame(
        {
            "region": demand_regions,
            "input_comm": "gsl",
            "tech": "GSL_DEMAND",
            "vintage": 1,
            "output_comm": "d_gsl",
            "efficiency": 1.0,
            "notes": "Gasoline demand technology rebuilt from snapped demand regions",
            "data_source": None,
            "dq_cred": None,
            "dq_geog": None,
            "dq_struc": None,
            "dq_tech": None,
            "dq_time": None,
            "data_id": DATA_ID,
        }
    )

    node_efficiency = pd.concat(
        [node_efficiency, gsl_demand_efficiency],
        ignore_index=True,
    )

    node_efficiency_techs = set(node_efficiency["tech"])

    db_encoded["Efficiency"] = db_encoded["Efficiency"].loc[
        (~db_encoded["Efficiency"]["tech"].isin(node_efficiency_techs))
        & (~db_encoded["Efficiency"]["region"].astype(str).str.contains("-", regex=False))
    ].copy()

    db_encoded["Efficiency"] = pd.concat(
        [db_encoded["Efficiency"], node_efficiency],
        ignore_index=True,
    )

    assert set(db_encoded["Demand"]["region"]) == set(
        db_encoded["Efficiency"].loc[
            db_encoded["Efficiency"]["tech"] == "GSL_DEMAND",
            "region",
        ]
    )
    assert not db_encoded["Efficiency"]["region"].astype(str).str.contains("-", regex=False).any()

    print(f"Node Efficiency rows added: {len(node_efficiency):,}")


# =============================================================================
# ETLSegment and transport table rebuilds
# =============================================================================


def build_etl_curve(
    tech: str,
    resolution: int = ETL_RESOLUTION,
    spacing: str = ETL_SPACING,
) -> pd.DataFrame:
    """Build ETLSegment curve rows for one technology without topology.

    This mirrors the legacy ``model_rules.invest_costs`` curve generation, but
    intentionally does not assign regions. Region assignment is handled later
    using the selected geospatial graph nodes or graph edges.
    """
    if tech not in ETL_COST_PARAMETERS:
        raise ValueError(f"Missing ETL cost parameters for {tech}.")
    if resolution < 2:
        raise ValueError("ETL resolution must be at least 2.")

    params = ETL_COST_PARAMETERS[tech]
    a = float(params["a"])
    b = float(params["b"])
    upper_vol = float(params["upper_vol"])

    if upper_vol <= 0:
        raise ValueError(f"upper_vol must be positive for {tech}.")
    if b <= -1:
        raise ValueError(f"b must be greater than -1 for {tech}.")

    if spacing == "log":
        import numpy as np

        edges = upper_vol * (np.logspace(0, 1, resolution) - 1.0) / 9.0
        edges[0] = 0.0
        edges[-1] = upper_vol
    elif spacing == "linear":
        import numpy as np

        edges = np.linspace(0.0, upper_vol, resolution)
    else:
        raise ValueError("ETL spacing must be 'log' or 'linear'.")

    cap_lower = edges[:-1]
    cap_upper = edges[1:]

    def antiderivative(q: float) -> float:
        return (a / (b + 1.0)) * (q ** (b + 1.0))

    cost_lower = [antiderivative(q) for q in cap_lower]
    cost_upper = [antiderivative(q) for q in cap_upper]

    return pd.DataFrame(
        {
            "tech_or_group": tech,
            "segment": range(len(cap_lower)),
            "cap_lower": cap_lower,
            "cap_upper": cap_upper,
            "cost_lower": cost_lower,
            "cost_upper": cost_upper,
            "data_id": DATA_ID,
        }
    )


def assign_etl_curve_to_regions(
    regions: pd.Series,
    tech: str,
    distance_factor: pd.Series | None = None,
) -> pd.DataFrame:
    """Cross-join a topology-free ETL curve onto selected regions."""
    region_frame = pd.DataFrame({"region": regions.reset_index(drop=True), "key": 1})
    curve = build_etl_curve(tech).copy()
    curve["key"] = 1

    out = region_frame.merge(curve, on="key").drop(columns="key")

    if distance_factor is not None:
        factor_frame = pd.DataFrame(
            {
                "region": regions.reset_index(drop=True),
                "distance_factor": distance_factor.reset_index(drop=True),
            }
        )
        out = out.merge(factor_frame, on="region", how="left")
        if out["distance_factor"].isna().any():
            raise ValueError(f"Missing distance scaling factor for {tech} ETL rows.")
        out["cost_lower"] = out["cost_lower"] * out["distance_factor"]
        out["cost_upper"] = out["cost_upper"] * out["distance_factor"]
        out = out.drop(columns="distance_factor")

    return out[
        [
            "region",
            "tech_or_group",
            "segment",
            "cap_lower",
            "cap_upper",
            "cost_lower",
            "cost_upper",
            "data_id",
        ]
    ].copy()


def rebuild_etl_segments(
    db_encoded: dict[str, pd.DataFrame],
    site_attributes: pd.DataFrame,
    canonical: CanonicalLinks,
    specs: TechSpecs,
) -> None:
    """Rebuild all ETLSegment rows on the new geospatial topology.

    The legacy workflow generated ETLSegment rows after grouping points into an
    old grid and assigning old neighbor relationships. This function keeps the
    same cost-curve parameterization but discards the legacy topology entirely:

    * GSL_PLANT and METOH_PLANT curves are mapped to current graph node regions.
    * *_PIPE and ELC_TRANS curves are mapped to current canonical graph edges.
    * Truck technologies intentionally receive no ETLSegment rows because roads
      are represented as existing links with variable cost and zero investment.
    """
    plant_etl_rows = []
    for tech in sorted(PLANT_TECHS):
        plant_etl_rows.append(
            assign_etl_curve_to_regions(
                regions=site_attributes["region"],
                tech=tech,
            )
        )

    plant_etl_new = pd.concat(plant_etl_rows, ignore_index=True)

    reference_distance_km = canonical.pipeline_links["distance_km"].mean()
    if pd.isna(reference_distance_km) or reference_distance_km <= 0:
        raise ValueError("Cannot scale ETL costs because reference graph-edge distance is invalid.")

    print(f"Reference distance for ETLSegment cost scaling: {reference_distance_km:.2f} km")

    pipeline_etl_rows = []
    edge_tech_specs = pd.concat(
        [specs.pipeline_tech_specs, specs.transmission_tech_specs],
        ignore_index=True,
    )

    for tech in edge_tech_specs["tech"].drop_duplicates().sort_values():
        if tech not in ETL_COST_PARAMETERS:
            raise ValueError(f"Missing ETL cost parameters for transport technology: {tech}")

        edge_frame = canonical.pipeline_links[["canoe_region", "distance_km"]].copy()
        edge_frame = edge_frame.rename(columns={"canoe_region": "region"})
        distance_factor = edge_frame["distance_km"] / reference_distance_km

        pipeline_etl_rows.append(
            assign_etl_curve_to_regions(
                regions=edge_frame["region"],
                tech=tech,
                distance_factor=distance_factor,
            )
        )

    pipeline_etl_new = pd.concat(pipeline_etl_rows, ignore_index=True)

    if not pipeline_etl_new.empty:
        etl_cost_range = pipeline_etl_new.groupby("tech_or_group")["cost_upper"].agg(["min", "max"])
        assert (etl_cost_range["max"] > etl_cost_range["min"]).all(), (
            "Distance scaling did not produce cost variation across graph edges."
        )

    db_encoded["ETLSegment"] = pd.concat(
        [plant_etl_new, pipeline_etl_new],
        ignore_index=True,
    )

    assert db_encoded["ETLSegment"][["region", "tech_or_group", "segment"]].duplicated().sum() == 0

    etl_edge_regions = set(
        db_encoded["ETLSegment"].loc[
            db_encoded["ETLSegment"]["region"].astype(str).str.contains("-", regex=False),
            "region",
        ]
    )
    invalid_etl_edge_regions = sorted(etl_edge_regions - canonical.valid_pipeline_edge_regions)
    assert not invalid_etl_edge_regions, (
        f"ETLSegment contains invalid edge regions: {invalid_etl_edge_regions[:10]}"
    )

    truck_etl_rows = db_encoded["ETLSegment"].loc[
        db_encoded["ETLSegment"]["tech_or_group"].isin(specs.truck_techs)
    ]
    assert truck_etl_rows.empty, "Truck technologies should not receive ETLSegment rows."

    print(f"Plant ETLSegment rows: {len(plant_etl_new):,}")
    print(f"Pipeline/transmission ETLSegment rows: {len(pipeline_etl_new):,}")
    print(f"Encoded ETLSegment rows: {len(db_encoded['ETLSegment']):,}")

def rebuild_technology_table(
    db_encoded: dict[str, pd.DataFrame],
    technologies_raw: pd.DataFrame,
    specs: TechSpecs,
) -> None:
    technology = technologies_raw.copy()
    technology["sector"] = "industrial"
    technology["reserve"] = 0
    technology["curtail"] = 0
    technology["retire"] = 0
    technology["flex"] = 0
    technology["data_id"] = DATA_ID

    db_encoded["Technology"] = technology.copy()

    assert specs.truck_techs.issubset(set(db_encoded["Technology"]["tech"])), (
        "Truck technologies are missing from techs.csv."
    )
    assert specs.pipe_techs.issubset(set(db_encoded["Technology"]["tech"])), (
        "Pipeline technologies are missing from techs.csv."
    )

    print(f"Technology rows: {len(db_encoded['Technology']):,}")


def build_transport_efficiency(
    links: pd.DataFrame,
    tech_specs: pd.DataFrame,
    notes: str,
) -> pd.DataFrame:
    rows = []
    for tech in tech_specs.itertuples(index=False):
        rows.append(
            pd.DataFrame(
                {
                    "region": links["canoe_region"],
                    "input_comm": tech.input_comm,
                    "tech": tech.tech,
                    "vintage": 1,
                    "output_comm": tech.output_comm,
                    "efficiency": 1.0,
                    "notes": notes,
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def build_transport_costvariable(
    links: pd.DataFrame,
    tech_specs: pd.DataFrame,
    notes: str,
) -> pd.DataFrame:
    assert links["distance_km"].notna().all()
    assert (links["distance_km"] > 0).all()
    assert links["canoe_region"].nunique() == len(links)

    required_cols = {"tech", "cost_per_km", "intercept_cost_per_km"}
    missing_cols = required_cols - set(tech_specs.columns)
    if missing_cols:
        raise ValueError(f"tech_specs is missing required columns: {sorted(missing_cols)}")

    rows = []
    for tech in tech_specs.itertuples(index=False):
        cost_per_km = float(tech.cost_per_km)
        intercept_cost_per_km = float(tech.intercept_cost_per_km)

        rows.append(
            pd.DataFrame(
                {
                    "region": links["canoe_region"],
                    "period": 1,
                    "tech": tech.tech,
                    "vintage": 1,
                    "cost": intercept_cost_per_km + cost_per_km * links["distance_km"],
                    "units": "M$/unit",
                    "notes": notes,
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            )
        )

    out = pd.concat(rows, ignore_index=True)
    assert out[["region", "period", "tech", "vintage", "data_id"]].duplicated().sum() == 0
    return out


def rebuild_transport_tables(
    db_encoded: dict[str, pd.DataFrame],
    canonical: CanonicalLinks,
    specs: TechSpecs,
    connection_method: str,
) -> None:
    pipeline_efficiency = build_transport_efficiency(
        canonical.pipeline_links,
        specs.pipeline_tech_specs,
        "Candidate pipeline transport link on canonical graph edge",
    )
    truck_efficiency = build_transport_efficiency(
        canonical.road_links,
        specs.truck_tech_specs,
        f"Existing {connection_method} road-connected transport link",
    )
    transmission_efficiency = build_transport_efficiency(
        canonical.pipeline_links,
        specs.transmission_tech_specs,
        "Candidate electricity transmission link on canonical graph edge",
    )

    db_encoded["Efficiency"] = db_encoded["Efficiency"].loc[
        ~db_encoded["Efficiency"]["tech"].isin(specs.transport_techs)
    ].copy()
    db_encoded["Efficiency"] = pd.concat(
        [
            db_encoded["Efficiency"],
            pipeline_efficiency,
            truck_efficiency,
            transmission_efficiency,
        ],
        ignore_index=True,
    )

    pipeline_costvariable = build_transport_costvariable(
        canonical.pipeline_links,
        specs.pipeline_tech_specs,
        "Pipeline transport cost rebuilt from transport_techs.csv and selected graph-edge distance",
    )
    truck_costvariable = build_transport_costvariable(
        canonical.road_links,
        specs.truck_tech_specs,
        f"Truck transport cost rebuilt from transport_techs.csv and selected {connection_method} road-connected graph distance",
    )
    transmission_costvariable = build_transport_costvariable(
        canonical.pipeline_links,
        specs.transmission_tech_specs,
        "Electricity transmission cost rebuilt from transport_techs.csv and selected graph-edge distance",
    )

    non_edge_costvariable = db_encoded["CostVariable"].loc[
        ~db_encoded["CostVariable"]["region"].astype(str).str.contains("-", regex=False)
    ].copy()
    db_encoded["CostVariable"] = pd.concat(
        [
            non_edge_costvariable,
            pipeline_costvariable,
            truck_costvariable,
            transmission_costvariable,
        ],
        ignore_index=True,
    )

    truck_costinvest_rows = []
    for truck in specs.truck_tech_specs.itertuples(index=False):
        truck_costinvest_rows.append(
            pd.DataFrame(
                {
                    "region": canonical.road_links["canoe_region"],
                    "tech": truck.tech,
                    "vintage": 1,
                    "cost": 0.0,
                    "units": "M$/unit",
                    "notes": "Existing road transport link; no road construction investment encoded",
                    "data_source": None,
                    "dq_cred": None,
                    "dq_geog": None,
                    "dq_struc": None,
                    "dq_tech": None,
                    "dq_time": None,
                    "data_id": DATA_ID,
                }
            )
        )

    truck_costinvest = pd.concat(truck_costinvest_rows, ignore_index=True)
    db_encoded["CostInvest"] = db_encoded["CostInvest"].loc[
        ~db_encoded["CostInvest"]["tech"].isin(specs.truck_techs)
    ].copy()
    db_encoded["CostInvest"] = pd.concat(
        [db_encoded["CostInvest"], truck_costinvest],
        ignore_index=True,
    )

    assert set(pipeline_efficiency["region"]) == canonical.valid_pipeline_edge_regions
    assert set(truck_efficiency["region"]) == canonical.valid_road_edge_regions
    assert set(transmission_efficiency["region"]) == canonical.valid_pipeline_edge_regions
    assert set(pipeline_costvariable["region"]) == canonical.valid_pipeline_edge_regions
    assert set(truck_costvariable["region"]) == canonical.valid_road_edge_regions
    assert set(transmission_costvariable["region"]) == canonical.valid_pipeline_edge_regions
    assert set(truck_costinvest["region"]) == canonical.valid_road_edge_regions

    print(f"Pipeline Efficiency rows: {len(pipeline_efficiency):,}")
    print(f"Truck Efficiency rows: {len(truck_efficiency):,}")
    print(f"Transmission Efficiency rows: {len(transmission_efficiency):,}")
    print(f"Pipeline CostVariable rows: {len(pipeline_costvariable):,}")
    print(f"Truck CostVariable rows: {len(truck_costvariable):,}")
    print(f"Transmission CostVariable rows: {len(transmission_costvariable):,}")
    print(f"Truck CostInvest rows: {len(truck_costinvest):,}")



def rebuild_static_supporting_tables(
    db_encoded: dict[str, pd.DataFrame],
    commodities_raw: pd.DataFrame,
) -> None:
    """Populate small non-topology tables without using legacy grid generation."""
    db_encoded["Commodity"] = commodities_raw.copy()
    db_encoded["TechnologyType"] = pd.DataFrame(
        {
            "label": ["p", "t"],
            "description": ["production", "transport"],
        }
    )
    db_encoded["TimePeriod"] = pd.DataFrame(
        {
            "sequence": [1, 2],
            "period": [1, 2],
            "flag": ["f", "f"],
        }
    )
    db_encoded["SectorLabel"] = pd.DataFrame(
        {
            "sector": ["industrial"],
            "notes": ["industrial sector"],
        }
    )
    db_encoded["DataSet"] = pd.DataFrame(
        {
            "data_id": [DATA_ID],
            "label": ["Geospatial Renewable Gas Data"],
            "version": ["GEO001"],
            "description": ["Geospatial data for renewable gas model"],
            "status": ["active"],
            "author": ["Geospatial-CANOE workflow"],
            "date": ["2026-07-03"],
            "parent_id": [None],
            "changelog": ["Rebuilt on selected geospatial topology without legacy grouped-site topology."],
            "notes": [None],
        }
    )

    print("Static supporting tables rebuilt.")


# =============================================================================
# Final validation and export
# =============================================================================

def validate_encoded_region_coverage(
    db_encoded: dict[str, pd.DataFrame],
    canonical: CanonicalLinks,
    specs: TechSpecs,
) -> None:
    for table_name in ["Efficiency", "CostVariable", "CostInvest", "ETLSegment"]:
        table = db_encoded[table_name].copy()
        if "region" not in table.columns:
            continue

        region_values = table["region"].dropna().astype(str)
        node_values = region_values[~region_values.str.contains("-", regex=False)]
        edge_values = region_values[region_values.str.contains("-", regex=False)]

        invalid_node_regions = sorted(set(node_values) - canonical.valid_node_regions)
        valid_edge_regions = canonical.valid_pipeline_edge_regions | canonical.valid_road_edge_regions
        invalid_edge_regions = sorted(set(edge_values) - valid_edge_regions)

        print(
            f"{table_name}: {node_values.nunique():,} node regions, "
            f"{edge_values.nunique():,} edge regions, "
            f"{len(invalid_node_regions):,} invalid nodes, "
            f"{len(invalid_edge_regions):,} invalid edges"
        )

        assert not invalid_node_regions, (
            f"{table_name} has invalid node regions: {invalid_node_regions[:10]}"
        )
        assert not invalid_edge_regions, (
            f"{table_name} has invalid edge regions: {invalid_edge_regions[:10]}"
        )

    pipeline_etl_regions = set(
        db_encoded["ETLSegment"].loc[
            db_encoded["ETLSegment"]["tech_or_group"].isin(specs.pipe_techs),
            "region",
        ].astype(str)
    )
    pipeline_eff_regions = set(
        db_encoded["Efficiency"].loc[
            db_encoded["Efficiency"]["tech"].isin(specs.pipe_techs),
            "region",
        ].astype(str)
    )
    assert pipeline_etl_regions == pipeline_eff_regions, (
        "Pipeline ETLSegment region coverage does not match pipeline Efficiency region coverage."
    )

    truck_etl_regions = set(
        db_encoded["ETLSegment"].loc[
            db_encoded["ETLSegment"]["tech_or_group"].isin(specs.truck_techs),
            "region",
        ].astype(str)
    )
    assert not truck_etl_regions, "Truck technologies should have no ETLSegment rows."

    print("Transport region coverage validated.")


def clear_output_tables(db_encoded: dict[str, pd.DataFrame]) -> None:
    output_tables = [name for name in db_encoded if name.startswith("Output")]
    for table_name in output_tables:
        db_encoded[table_name] = db_encoded[table_name].iloc[0:0].copy()
    print(f"Cleared solver output tables: {len(output_tables):,}")


def export_sqlite(
    db_encoded: dict[str, pd.DataFrame],
    output_sqlite_path: Path,
) -> None:
    if output_sqlite_path.exists():
        output_sqlite_path.unlink()

    db_mgmt.convert_sql_to_sqlite(RAW_SCHEMA_PATH, output_sqlite_path)
    db_mgmt.update_sqlite(output_sqlite_path, db_encoded)

    print(f"Created SQLite: {output_sqlite_path}")


def verify_exported_sqlite(
    output_sqlite_path: Path,
    specs: TechSpecs,
    canonical: CanonicalLinks,
    snapped: SnappedInputs,
) -> None:
    db_test = db_mgmt.sqlite_to_dfs(output_sqlite_path)

    print("\nExported database table counts:")
    for table_name in [
        "Region",
        "Technology",
        "Demand",
        "LimitCapacity",
        "Efficiency",
        "CostVariable",
        "CostInvest",
        "ETLSegment",
    ]:
        print(f"{table_name}: {len(db_test[table_name]):,}")

    expected_truck_rows = len(canonical.road_links) * len(specs.truck_tech_specs)
    assert specs.truck_techs.issubset(set(db_test["Technology"]["tech"]))
    assert db_test["Efficiency"]["tech"].isin(specs.truck_techs).sum() == expected_truck_rows
    assert db_test["CostVariable"]["tech"].isin(specs.truck_techs).sum() == expected_truck_rows
    assert db_test["CostInvest"]["tech"].isin(specs.truck_techs).sum() == expected_truck_rows

    co2_cap_rows = db_test["LimitCapacity"].loc[
        db_test["LimitCapacity"]["tech_or_group"] == "CO2_CAP"
    ].copy()

    co2_cap_positive = co2_cap_rows.loc[co2_cap_rows["capacity"] > 0].copy()

    assert len(co2_cap_rows) == len(db_test["Region"])
    assert co2_cap_rows["capacity"].ge(0).all()
    assert co2_cap_rows["capacity"].sum() > 0

    print("\nCO2_CAP validation:")
    print(f"CO2_CAP rows: {len(co2_cap_rows):,}")
    print(f"CO2_CAP positive regions: {len(co2_cap_positive):,}")
    print(f"Total CO2_CAP capacity: {co2_cap_rows['capacity'].sum():,.2f} t CO2e/year")
    print(f"Spatially assignable CO2 rows: {len(snapped.co2_raw):,}")
    print(f"Dropped zero/negative CO2 rows: {len(snapped.co2_raw) - len(snapped.co2_facilities):,}")
    print(f"Facilities entering snap: {len(snapped.co2_facilities):,}")

    if "n_co2_facilities" in snapped.site_attributes.columns:
        print(f"Mapped CO2 facilities: {int(snapped.site_attributes['n_co2_facilities'].sum()):,}")
        print(
            "Regions with mapped CO2 facilities: "
            f"{(snapped.site_attributes['n_co2_facilities'] > 0).sum():,}"
        )

    print("\nExported SQLite validated.")


def summarize_final_database(db_encoded: dict[str, pd.DataFrame]) -> None:
    print("\nFinal encoded database summary:")
    for table_name in [
        "Region",
        "Technology",
        "Demand",
        "LimitCapacity",
        "Efficiency",
        "CostVariable",
        "CostInvest",
        "ETLSegment",
    ]:
        print(f"{table_name}: {len(db_encoded[table_name]):,}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    PROCESSED_SCHEMA.mkdir(parents=True, exist_ok=True)

    config = select_schema_configuration()

    print("\nSelected configuration:")
    print(f"Basemap: {config.basemap_stem}")
    print(f"Road connection method: {config.connection_method}")
    print(f"Output SQLite: {config.output_sqlite_path.name}")

    inputs = load_inputs(config)

    canonical = build_canonical_links(
        graph_nodes=inputs.graph_nodes,
        graph_edges=inputs.graph_edges,
        road_edge_connections=inputs.road_edge_connections,
        config=config,
    )

    specs = build_tech_specs(inputs.transport_techs_raw)

    db_encoded = {table_name: df.copy() for table_name, df in inputs.db.items()}
    db_encoded["Region"] = canonical.region_table.copy()
    rebuild_static_supporting_tables(db_encoded, inputs.commodities_raw)

    snapped = build_site_attributes(inputs, canonical)

    print("\nRebuilding node-level tables...")
    rebuild_demand_and_capacity(db_encoded, snapped.site_attributes)
    rebuild_node_costs(db_encoded, snapped.site_attributes)
    rebuild_input_splits(db_encoded, snapped.site_attributes)
    rebuild_node_efficiency(db_encoded, snapped.site_attributes, inputs.gen_efficiencies_raw)

    print("\nRebuilding ETLSegment and transport tables...")
    rebuild_etl_segments(
        db_encoded=db_encoded,
        site_attributes=snapped.site_attributes,
        canonical=canonical,
        specs=specs,
    )
    rebuild_technology_table(db_encoded, inputs.technologies_raw, specs)
    rebuild_transport_tables(
        db_encoded=db_encoded,
        canonical=canonical,
        specs=specs,
        connection_method=config.connection_method,
    )

    print("\nValidating encoded database...")
    validate_encoded_region_coverage(db_encoded, canonical, specs)
    clear_output_tables(db_encoded)
    summarize_final_database(db_encoded)

    print("\nExporting SQLite...")
    export_sqlite(db_encoded, config.output_sqlite_path)
    verify_exported_sqlite(config.output_sqlite_path, specs, canonical, snapped)

    print("\nStage complete.")
    print(f"Output database: {config.output_sqlite_path}")


if __name__ == "__main__":
    main()