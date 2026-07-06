# =============================================================================
# audit_inputs.py
#
# Pre-solve input audit for the Geospatial-CANOE preprocessing workflow.
#
# This script does not mutate model inputs or SQLite databases. It checks the
# selected basemap, graph, road-connectivity product, cleaned emissions file,
# raw node-attribute inputs, and optionally the encoded SQLite schema.
#
# Outputs are written to:
#   data_files/processed/audits/input_audit/<AUDIT_TAG>/
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

import geopandas as gpd
import pandas as pd


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = PROJECT_ROOT / "data_files"

PROCESSED_BASEMAPS = DATA_FILES / "processed" / "basemaps"
PROCESSED_GRAPH = DATA_FILES / "processed" / "graph"
PROCESSED_ROAD_CONNECTIVITY = DATA_FILES / "processed" / "road_connectivity"
PROCESSED_SCHEMA = DATA_FILES / "processed" / "schema"
PROCESSED_AUDITS = DATA_FILES / "processed" / "audits" / "input_audit"

SITES_PATH = DATA_FILES / "sites_full.csv"
DEMAND_PATH = DATA_FILES / "demand.csv"
TRANSPORT_TECHS_PATH = DATA_FILES / "transport_techs.csv"
TECHNOLOGIES_PATH = DATA_FILES / "techs.csv"

PROCESSED_EMISSIONS_DIR = (
    DATA_FILES
    / "processed"
    / "emissions"
    / "co2_large_facilities_2024"
)
CO2_CLEAN_GPKG_PATH = PROCESSED_EMISSIONS_DIR / "co2_large_facilities_2024_clean.gpkg"

STAT_CANADA_LAMBERT_CRS = "EPSG:3347"
CO2_KT_TO_T_FACTOR = 1000.0

SNAP_WARNING_M = 25_000
SNAP_AUDIT_M = 100_000
SNAP_CRITICAL_M = 250_000


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class AuditConfig:
    basemap_stem: str
    connection_method: str
    basemap_path: Path
    graph_node_path: Path
    graph_edge_path: Path
    road_edge_connections_path: Path
    road_edges_gpkg_path: Path
    schema_path: Path
    audit_dir: Path


# =============================================================================
# Selection helpers
# =============================================================================

def print_banner(title: str) -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)


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
    return [
        path.stem
        for path in sorted(PROCESSED_BASEMAPS.glob("canada_basemap_*deg_*.gpkg"))
    ]


def build_audit_config() -> AuditConfig:
    basemap_stem = select_from_options(discover_basemap_stems(), "basemap")
    connection_method = select_from_options(["weak", "strong"], "road connection method")

    schema_path = (
        PROCESSED_SCHEMA
        / f"CANOE_geospatial_{basemap_stem}_{connection_method}.sqlite"
    )

    audit_tag = (
        f"{datetime.today().strftime('%Y-%m-%d_%H%M%S')}"
        f"_{basemap_stem}_{connection_method}"
    )

    config = AuditConfig(
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
        schema_path=schema_path,
        audit_dir=PROCESSED_AUDITS / audit_tag,
    )

    validate_required_paths(config)
    config.audit_dir.mkdir(parents=True, exist_ok=True)
    return config


def validate_required_paths(config: AuditConfig) -> None:
    required = {
        "basemap": config.basemap_path,
        "graph_nodes": config.graph_node_path,
        "graph_edges": config.graph_edge_path,
        "road_edge_connections": config.road_edge_connections_path,
        "road_edges_gpkg": config.road_edges_gpkg_path,
        "sites_full": SITES_PATH,
        "demand": DEMAND_PATH,
        "clean_emissions_gpkg": CO2_CLEAN_GPKG_PATH,
        "transport_techs": TRANSPORT_TECHS_PATH,
        "techs": TECHNOLOGIES_PATH,
    }

    missing = {name: path for name, path in required.items() if not path.exists()}
    if missing:
        print("\nMissing required audit inputs:")
        for name, path in missing.items():
            print(f"  {name}: {path}")
        raise FileNotFoundError("One or more required audit inputs are missing.")


# =============================================================================
# Generic utilities
# =============================================================================

def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def get_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def summarize_numeric(series: pd.Series) -> dict[str, float | int | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": int(values.count()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def sqlite_table_names(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        table_names = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;",
            conn,
        )["name"].tolist()
    return table_names


def read_sqlite_table(db_path: Path, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}";', conn)


# =============================================================================
# Spatial snapping audit
# =============================================================================

def prepare_sites_points() -> pd.DataFrame:
    sites = pd.read_csv(SITES_PATH)
    lon_col = get_first_existing_column(sites, ["lon", "longitude", "Longitude"])
    lat_col = get_first_existing_column(sites, ["lat", "latitude", "Latitude"])
    if lon_col is None or lat_col is None:
        raise ValueError("sites_full.csv must contain lon/lat columns.")

    out = pd.DataFrame(
        {
            "source_type": "sites_full",
            "source_id": [f"sites_full_{i:06d}" for i in range(len(sites))],
            "lon": safe_numeric(sites[lon_col]),
            "lat": safe_numeric(sites[lat_col]),
            "attribute_name": "max_elec",
            "attribute_value": safe_numeric(
                sites[get_first_existing_column(sites, ["max_elec", "max_elc"])]
            ) if get_first_existing_column(sites, ["max_elec", "max_elc"]) else None,
        }
    )
    return out.dropna(subset=["lon", "lat"]).copy()


def prepare_demand_points() -> pd.DataFrame:
    demand = pd.read_csv(DEMAND_PATH)
    lon_col = get_first_existing_column(demand, ["lon", "longitude", "Longitude"])
    lat_col = get_first_existing_column(demand, ["lat", "latitude", "Latitude"])
    if lon_col is None or lat_col is None:
        raise ValueError("demand.csv must contain lon/lat columns.")

    out = pd.DataFrame(
        {
            "source_type": "demand",
            "source_id": [f"demand_{i:06d}" for i in range(len(demand))],
            "lon": safe_numeric(demand[lon_col]),
            "lat": safe_numeric(demand[lat_col]),
            "attribute_name": "demand",
            "attribute_value": safe_numeric(demand["demand"]) if "demand" in demand.columns else None,
        }
    )
    return out.dropna(subset=["lon", "lat"]).copy()


def prepare_co2_points() -> pd.DataFrame:
    co2 = gpd.read_file(CO2_CLEAN_GPKG_PATH)
    lon_col = get_first_existing_column(co2, ["longitude", "lon", "Longitude"])
    lat_col = get_first_existing_column(co2, ["latitude", "lat", "Latitude"])
    emissions_col = get_first_existing_column(
        co2,
        ["emissions_kt_co2e_per_year", "co2", "Total emissions"],
    )
    id_col = get_first_existing_column(co2, ["facility_id", "Facility ID"])

    if lon_col is None or lat_col is None:
        raise ValueError("Clean CO2 GPKG must contain longitude/latitude columns.")
    if emissions_col is None:
        raise ValueError("Clean CO2 GPKG must contain an emissions column.")

    source_id = (
        co2[id_col].astype(str)
        if id_col is not None
        else pd.Series([f"co2_{i:06d}" for i in range(len(co2))])
    )

    out = pd.DataFrame(
        {
            "source_type": "co2_facility",
            "source_id": source_id,
            "lon": safe_numeric(co2[lon_col]),
            "lat": safe_numeric(co2[lat_col]),
            "attribute_name": "emissions_t_co2e_per_year",
            "attribute_value": safe_numeric(co2[emissions_col]) * CO2_KT_TO_T_FACTOR,
        }
    )
    out = out.dropna(subset=["lon", "lat"]).copy()
    out = out.loc[out["attribute_value"].fillna(0) > 0].copy()
    return out


def snap_points_to_nodes_for_audit(
    points: pd.DataFrame,
    graph_nodes: gpd.GeoDataFrame,
) -> pd.DataFrame:
    points = points.reset_index(drop=True).copy()
    points["point_id"] = range(len(points))

    points_gdf = gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points["lon"], points["lat"]),
        crs="EPSG:4326",
    )

    nodes_gdf = graph_nodes[["region", "geometry"]].copy()
    if nodes_gdf.crs is None:
        nodes_gdf = nodes_gdf.set_crs("EPSG:4326")

    within = gpd.sjoin(
        points_gdf,
        nodes_gdf,
        how="left",
        predicate="within",
    ).drop(columns="index_right")

    within["matched_by"] = "within"
    within["snap_distance_m"] = 0.0

    unmatched_mask = within["region"].isna()
    n_unmatched = int(unmatched_mask.sum())

    if n_unmatched > 0:
        unmatched = points_gdf.loc[unmatched_mask].copy()
        nearest = gpd.sjoin_nearest(
            unmatched.to_crs(STAT_CANADA_LAMBERT_CRS),
            nodes_gdf.to_crs(STAT_CANADA_LAMBERT_CRS),
            how="left",
            distance_col="snap_distance_m",
        ).to_crs("EPSG:4326")

        nearest = nearest.loc[~nearest.index.duplicated(keep="first")].copy()
        within.loc[unmatched_mask, "region"] = nearest["region"].values
        within.loc[unmatched_mask, "matched_by"] = "nearest"
        within.loc[unmatched_mask, "snap_distance_m"] = nearest["snap_distance_m"].values

    audit = pd.DataFrame(within.drop(columns="geometry"))
    audit["flag_warning_gt_25km"] = audit["snap_distance_m"] > SNAP_WARNING_M
    audit["flag_audit_gt_100km"] = audit["snap_distance_m"] > SNAP_AUDIT_M
    audit["flag_critical_gt_250km"] = audit["snap_distance_m"] > SNAP_CRITICAL_M

    return audit[
        [
            "source_type",
            "source_id",
            "lon",
            "lat",
            "attribute_name",
            "attribute_value",
            "region",
            "matched_by",
            "snap_distance_m",
            "flag_warning_gt_25km",
            "flag_audit_gt_100km",
            "flag_critical_gt_250km",
        ]
    ].copy()


def summarize_snap_audit(snap_audit: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source_type, group in snap_audit.groupby("source_type"):
        fallback = group.loc[group["matched_by"] == "nearest"].copy()
        stats = summarize_numeric(fallback["snap_distance_m"])
        rows.append(
            {
                "source_type": source_type,
                "total_points": len(group),
                "within_polygon": int((group["matched_by"] == "within").sum()),
                "nearest_fallback": int((group["matched_by"] == "nearest").sum()),
                "fallback_share": (
                    float((group["matched_by"] == "nearest").mean())
                    if len(group) > 0
                    else None
                ),
                "fallback_mean_m": stats["mean"],
                "fallback_median_m": stats["median"],
                "fallback_p95_m": stats["p95"],
                "fallback_max_m": stats["max"],
                "warning_gt_25km": int(group["flag_warning_gt_25km"].sum()),
                "audit_gt_100km": int(group["flag_audit_gt_100km"].sum()),
                "critical_gt_250km": int(group["flag_critical_gt_250km"].sum()),
            }
        )
    return pd.DataFrame(rows)


# =============================================================================
# Topology and schema audits
# =============================================================================

def audit_topology(
    config: AuditConfig,
    basemap: gpd.GeoDataFrame,
    graph_nodes: gpd.GeoDataFrame,
    graph_edges: pd.DataFrame,
    road_connections: pd.DataFrame,
    road_edges: gpd.GeoDataFrame,
) -> pd.DataFrame:
    has_road = road_connections.loc[
        road_connections.get("has_road_connection", False).astype(bool)
    ].copy()

    rows = [
        {"metric": "basemap_regions", "value": len(basemap)},
        {"metric": "graph_nodes", "value": len(graph_nodes)},
        {"metric": "graph_node_unique_regions", "value": graph_nodes["region"].nunique()},
        {"metric": "graph_edges", "value": len(graph_edges)},
        {"metric": "graph_edge_unique_regions", "value": graph_edges["edge_region"].nunique() if "edge_region" in graph_edges else None},
        {"metric": "road_connection_rows", "value": len(road_connections)},
        {"metric": "road_connected_rows", "value": len(has_road)},
        {"metric": "road_edge_geometries", "value": len(road_edges)},
        {"metric": "selected_connection_method", "value": config.connection_method},
    ]

    if "distance_km" in graph_edges.columns:
        stats = summarize_numeric(graph_edges["distance_km"])
        for key, value in stats.items():
            rows.append({"metric": f"graph_edge_distance_{key}", "value": value})

    if "distance_km" in has_road.columns:
        stats = summarize_numeric(has_road["distance_km"])
        for key, value in stats.items():
            rows.append({"metric": f"road_edge_distance_{key}", "value": value})

    return pd.DataFrame(rows)


def audit_schema(config: AuditConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not config.schema_path.exists():
        empty = pd.DataFrame()
        print(f"\nSchema not found, skipping SQLite audit: {config.schema_path}")
        return empty, empty, empty

    table_names = sqlite_table_names(config.schema_path)
    count_rows = []
    duplicate_rows = []
    coverage_rows = []

    primary_like_keys = {
        "Region": ["region"],
        "Technology": ["tech", "data_id"],
        "Demand": ["region", "period", "commodity", "data_id"],
        "LimitCapacity": ["region", "period", "tech_or_group", "operator", "data_id"],
        "Efficiency": ["region", "input_comm", "tech", "vintage", "output_comm", "data_id"],
        "CostVariable": ["region", "period", "tech", "vintage", "data_id"],
        "CostInvest": ["region", "tech", "vintage", "data_id"],
        "ETLSegment": ["region", "tech_or_group", "segment"],
    }

    for table_name in table_names:
        table = read_sqlite_table(config.schema_path, table_name)
        count_rows.append({"table": table_name, "rows": len(table), "columns": len(table.columns)})

        if table_name in primary_like_keys:
            keys = [col for col in primary_like_keys[table_name] if col in table.columns]
            duplicated = int(table.duplicated(subset=keys).sum()) if keys else None
            duplicate_rows.append(
                {
                    "table": table_name,
                    "key_columns": ",".join(keys),
                    "duplicate_rows": duplicated,
                }
            )

        if "region" in table.columns:
            region_values = table["region"].dropna().astype(str)
            coverage_rows.append(
                {
                    "table": table_name,
                    "node_regions": int((~region_values.str.contains("-", regex=False)).sum()),
                    "edge_regions": int(region_values.str.contains("-", regex=False).sum()),
                    "unique_node_regions": region_values.loc[~region_values.str.contains("-", regex=False)].nunique(),
                    "unique_edge_regions": region_values.loc[region_values.str.contains("-", regex=False)].nunique(),
                }
            )

    return (
        pd.DataFrame(count_rows),
        pd.DataFrame(duplicate_rows),
        pd.DataFrame(coverage_rows),
    )


# =============================================================================
# Main workflow
# =============================================================================

def main() -> None:
    print_banner("Geospatial-CANOE input audit")
    print(f"Project root: {PROJECT_ROOT}")

    config = build_audit_config()

    print("\nSelected audit configuration:")
    print(f"Basemap: {config.basemap_stem}")
    print(f"Road connection method: {config.connection_method}")
    print(f"Schema path: {config.schema_path}")
    print(f"Audit output directory: {config.audit_dir}")

    print("\nLoading spatial inputs...")
    basemap = gpd.read_file(config.basemap_path)
    graph_nodes = gpd.read_file(config.graph_node_path)
    graph_edges = pd.read_csv(config.graph_edge_path)
    road_connections = pd.read_csv(config.road_edge_connections_path)
    road_edges = gpd.read_file(config.road_edges_gpkg_path)

    topology_summary = audit_topology(
        config,
        basemap,
        graph_nodes,
        graph_edges,
        road_connections,
        road_edges,
    )
    topology_summary.to_csv(config.audit_dir / "topology_summary.csv", index=False)

    print("\nPreparing point inputs for snap audit...")
    sites_points = prepare_sites_points()
    demand_points = prepare_demand_points()
    co2_points = prepare_co2_points()

    all_points = pd.concat(
        [sites_points, demand_points, co2_points],
        ignore_index=True,
    )

    print(f"sites_full points: {len(sites_points):,}")
    print(f"demand points: {len(demand_points):,}")
    print(f"positive CO2 facility points: {len(co2_points):,}")
    print(f"all audit points: {len(all_points):,}")

    print("\nRunning spatial snap audit...")
    snap_audit = snap_points_to_nodes_for_audit(all_points, graph_nodes)
    snap_summary = summarize_snap_audit(snap_audit)

    snap_audit.to_csv(config.audit_dir / "snap_audit_all_points.csv", index=False)
    snap_summary.to_csv(config.audit_dir / "snap_summary_by_source.csv", index=False)

    long_snaps = snap_audit.loc[snap_audit["snap_distance_m"] > SNAP_WARNING_M].copy()
    long_snaps = long_snaps.sort_values("snap_distance_m", ascending=False)
    long_snaps.to_csv(config.audit_dir / "snap_audit_long_distance_gt_25km.csv", index=False)

    print("\nSnap summary:")
    print(snap_summary.to_string(index=False))
    print(f"\nLong-distance snap rows (>25 km): {len(long_snaps):,}")

    print("\nAuditing encoded SQLite schema, if available...")
    table_counts, duplicate_summary, region_coverage = audit_schema(config)

    if not table_counts.empty:
        table_counts.to_csv(config.audit_dir / "schema_table_counts.csv", index=False)
        duplicate_summary.to_csv(config.audit_dir / "schema_duplicate_summary.csv", index=False)
        region_coverage.to_csv(config.audit_dir / "schema_region_coverage.csv", index=False)

        print("\nKey schema table counts:")
        key_tables = table_counts.loc[
            table_counts["table"].isin(
                [
                    "Region",
                    "Technology",
                    "Demand",
                    "LimitCapacity",
                    "Efficiency",
                    "CostVariable",
                    "CostInvest",
                    "ETLSegment",
                ]
            )
        ]
        print(key_tables.to_string(index=False))

        duplicate_flags = duplicate_summary.loc[duplicate_summary["duplicate_rows"].fillna(0) > 0]
        if duplicate_flags.empty:
            print("\nNo duplicate primary-like rows detected in audited schema tables.")
        else:
            print("\nWARNING: duplicate primary-like rows detected:")
            print(duplicate_flags.to_string(index=False))

    print("\nAudit outputs written to:")
    print(config.audit_dir)
    print("\nStage complete.")


if __name__ == "__main__":
    main()
