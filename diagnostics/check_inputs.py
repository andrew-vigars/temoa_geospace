"""
Pre-solve input and schema-construction gate for Geospatial-CANOE.

This script audits the geospatial preprocessing artifacts and encoded
CANOE/TEMOA SQLite schema before a solve is run. It is intentionally
non-interactive: it prints a pass/fail report and exits with a deterministic
status code.

The script does not mutate model inputs or SQLite databases.

Checks
------
1. Required preprocessing files exist.
2. Basemap, graph, road-connectivity, and road-edge products are internally sane.
3. Raw point inputs can be snapped to graph regions within acceptable distances.
4. Encoded SQLite schema exists and contains required non-empty set/parameter tables.
5. Graph node regions match schema Region entries.
6. Region, Commodity, and Technology references in parameter tables are valid.
7. Primary-key-like rows are unique in key parameter tables.
8. Numeric sanity checks hold for demand, costs, efficiencies, and ETL bounds.
9. All declared checks executed; skipped checks are treated as gate failures.

Exit codes
----------
0
    All fatal checks passed. Warnings may still be present unless
    --fail-on-warning is used.

1
    One or more fatal checks failed.

2
    Script error or unreadable input.

Examples
--------
Run the pre-solve gate:

    python diagnostics/check_inputs.py --basemap canada_basemap_0.5deg_centroid --connection strong

Write CSV audit outputs:

    python diagnostics/check_inputs.py --basemap canada_basemap_0.5deg_centroid --connection strong --write-csv

Fail on warnings as well as errors:

    python diagnostics/check_inputs.py --basemap canada_basemap_0.5deg_centroid --connection strong --fail-on-warning
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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

EDGE_REGION_SEPARATOR = "-"


# =============================================================================
# Schema audit settings
# =============================================================================

SET_TABLES = {
    "Region": "region",
    "Commodity": "name",
    "Technology": "tech",
}

REQUIRED_SCHEMA_TABLES = [
    "Region",
    "Technology",
    "Commodity",
    "TimePeriod",
    "Demand",
    "LimitCapacity",
    "Efficiency",
    "CostVariable",
    "CostInvest",
    "ETLSegment",
]

# Table-driven schema description.
# Each table spec describes columns that reference Region, Commodity, and
# Technology set tables, plus primary-key-like columns for duplicate checks.
PARAM_TABLES = {
    "Demand": {
        "region": ["region"],
        "commodity": ["commodity"],
        "tech": [],
        "pk": ["region", "period", "commodity", "data_id"],
        "required": True,
    },
    "LimitCapacity": {
        "region": ["region"],
        "commodity": [],
        "tech": ["tech_or_group"],
        "pk": ["region", "period", "tech_or_group", "operator", "data_id"],
        "required": True,
    },
    "CostVariable": {
        "region": ["region"],
        "commodity": [],
        "tech": ["tech"],
        "pk": ["region", "period", "tech", "vintage", "data_id"],
        "required": True,
    },
    "CostInvest": {
        "region": ["region"],
        "commodity": [],
        "tech": ["tech"],
        "pk": ["region", "tech", "vintage", "data_id"],
        "required": True,
    },
    "Efficiency": {
        "region": ["region"],
        "commodity": ["input_comm", "output_comm"],
        "tech": ["tech"],
        "pk": ["region", "input_comm", "tech", "vintage", "output_comm", "data_id"],
        "required": True,
    },
    "LimitTechInputSplitAnnual": {
        "region": ["region"],
        "commodity": ["input_comm"],
        "tech": ["tech"],
        "pk": ["region", "period", "input_comm", "tech", "operator", "data_id"],
        "required": False,
    },
    "ETLSegment": {
        "region": ["region"],
        "commodity": [],
        "tech": ["tech_or_group"],
        "pk": ["region", "tech_or_group", "segment"],
        "required": True,
    },
}

NUMERIC_SCHEMA_CHECKS = [
    ("Demand", "demand", "non_negative", "ERROR"),
    ("Efficiency", "efficiency", "positive", "ERROR"),
    ("CostVariable", "cost", "non_negative", "ERROR"),
    ("CostInvest", "cost", "non_negative", "ERROR"),
    ("ETLSegment", "cap_lower", "non_negative", "ERROR"),
    ("ETLSegment", "cap_upper", "non_negative", "ERROR"),
    ("ETLSegment", "cost_lower", "non_negative", "ERROR"),
    ("ETLSegment", "cost_upper", "non_negative", "ERROR"),
]


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


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: str
    detail: str
    failures: pd.DataFrame | None = None
    ran: bool = True


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a non-interactive pre-solve input and schema gate for Geospatial-CANOE."
    )

    parser.add_argument(
        "--basemap",
        required=True,
        help=(
            "Basemap stem, for example "
            "canada_basemap_0.5deg_centroid or canada_basemap_1deg_intersects."
        ),
    )

    parser.add_argument(
        "--connection",
        required=True,
        choices=["weak", "strong"],
        help="Road connectivity method.",
    )

    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help=(
            "Optional explicit encoded SQLite schema path. If omitted, the script "
            "uses data_files/processed/schema/CANOE_geospatial_<basemap>_<connection>.sqlite."
        ),
    )

    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Write audit CSV outputs.",
    )

    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return exit code 1 if warnings are present.",
    )

    parser.add_argument(
        "--allow-offshore-co2-critical-snaps",
        action="store_true",
        help=(
            "Treat critical snap distances for CO2 facilities as WARNING instead "
            "of ERROR. Use only when those offshore facilities are excluded or "
            "handled elsewhere in preprocessing."
        ),
    )

    parser.add_argument(
        "--max-report-rows",
        type=int,
        default=20,
        help="Maximum number of failure rows to print per failed check.",
    )

    return parser.parse_args()


# =============================================================================
# Printing helpers
# =============================================================================

def print_banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_result(result: CheckResult, max_rows: int = 20) -> None:
    if not result.ran:
        status = "SKIP"
    else:
        status = "PASS" if result.passed else result.severity

    print(f"[{status}] {result.name}")
    print(f"       {result.detail}")

    if not result.passed and result.failures is not None and not result.failures.empty:
        print(result.failures.head(max_rows).to_string(index=False))


def safe_csv_name(name: str) -> str:
    return (
        name.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace(":", "")
        .replace("-", "_")
    )


# =============================================================================
# Config construction and path checks
# =============================================================================

def build_audit_config(
    basemap_stem: str,
    connection_method: str,
    schema_override: Path | None = None,
) -> AuditConfig:
    default_schema_path = (
        PROCESSED_SCHEMA
        / f"CANOE_geospatial_{basemap_stem}_{connection_method}.sqlite"
    )

    schema_path = schema_override if schema_override is not None else default_schema_path

    if not schema_path.is_absolute():
        schema_path = PROJECT_ROOT / schema_path

    audit_tag = (
        f"{datetime.today().strftime('%Y-%m-%d_%H%M%S')}"
        f"_{basemap_stem}_{connection_method}"
    )

    return AuditConfig(
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


def check_required_paths(config: AuditConfig) -> CheckResult:
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
        "schema": config.schema_path,
    }

    failures = pd.DataFrame(
        [
            {"input": name, "path": str(path)}
            for name, path in required.items()
            if not path.exists()
        ]
    )

    return CheckResult(
        name="Required input files exist",
        passed=failures.empty,
        severity="ERROR",
        detail=f"missing_inputs={len(failures)}",
        failures=failures if not failures.empty else None,
    )


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
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}

    return {
        "count": int(values.count()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def is_edge_region(region: object) -> bool:
    return isinstance(region, str) and EDGE_REGION_SEPARATOR in region


def split_edge_region(region: str) -> tuple[str, str]:
    left, right = region.split(EDGE_REGION_SEPARATOR, maxsplit=1)
    return left, right


# =============================================================================
# SQLite utilities
# =============================================================================

def sqlite_table_names(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;",
            conn,
        )["name"].tolist()


def read_sqlite_table(db_path: Path, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}";', conn)


def read_schema_tables(db_path: Path) -> dict[str, pd.DataFrame]:
    return {
        table_name: read_sqlite_table(db_path, table_name)
        for table_name in sqlite_table_names(db_path)
    }


# =============================================================================
# Point preparation
# =============================================================================

def prepare_sites_points() -> pd.DataFrame:
    sites = pd.read_csv(SITES_PATH)

    lon_col = get_first_existing_column(sites, ["lon", "longitude", "Longitude"])
    lat_col = get_first_existing_column(sites, ["lat", "latitude", "Latitude"])
    attribute_col = get_first_existing_column(sites, ["max_elec", "max_elc"])

    if lon_col is None or lat_col is None:
        raise ValueError("sites_full.csv must contain lon/lat columns.")

    attribute_value = (
        safe_numeric(sites[attribute_col])
        if attribute_col is not None
        else pd.Series([pd.NA] * len(sites))
    )

    out = pd.DataFrame(
        {
            "source_type": "sites_full",
            "source_id": [f"sites_full_{i:06d}" for i in range(len(sites))],
            "lon": safe_numeric(sites[lon_col]),
            "lat": safe_numeric(sites[lat_col]),
            "attribute_name": "max_elec",
            "attribute_value": attribute_value,
        }
    )

    return out.dropna(subset=["lon", "lat"]).copy()


def prepare_demand_points() -> pd.DataFrame:
    demand = pd.read_csv(DEMAND_PATH)

    lon_col = get_first_existing_column(demand, ["lon", "longitude", "Longitude"])
    lat_col = get_first_existing_column(demand, ["lat", "latitude", "Latitude"])

    if lon_col is None or lat_col is None:
        raise ValueError("demand.csv must contain lon/lat columns.")

    attribute_value = (
        safe_numeric(demand["demand"])
        if "demand" in demand.columns
        else pd.Series([pd.NA] * len(demand))
    )

    out = pd.DataFrame(
        {
            "source_type": "demand",
            "source_id": [f"demand_{i:06d}" for i in range(len(demand))],
            "lon": safe_numeric(demand[lon_col]),
            "lat": safe_numeric(demand[lat_col]),
            "attribute_name": "demand",
            "attribute_value": attribute_value,
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


# =============================================================================
# Spatial snap audit
# =============================================================================

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

    if int(unmatched_mask.sum()) > 0:
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


def check_snap_distances(
    snap_audit: pd.DataFrame,
    allow_offshore_co2_critical_snaps: bool = False,
) -> list[CheckResult]:
    critical = snap_audit.loc[
        snap_audit["snap_distance_m"] > SNAP_CRITICAL_M
    ].sort_values("snap_distance_m", ascending=False).copy()

    audit = snap_audit.loc[
        snap_audit["snap_distance_m"] > SNAP_AUDIT_M
    ].sort_values("snap_distance_m", ascending=False).copy()

    warning = snap_audit.loc[
        snap_audit["snap_distance_m"] > SNAP_WARNING_M
    ].sort_values("snap_distance_m", ascending=False).copy()

    if allow_offshore_co2_critical_snaps:
        unresolved_critical = critical.loc[
            critical["source_type"] != "co2_facility"
        ].copy()
        offshore_co2_critical = critical.loc[
            critical["source_type"] == "co2_facility"
        ].copy()
    else:
        unresolved_critical = critical
        offshore_co2_critical = pd.DataFrame()

    results = [
        CheckResult(
            name="No unresolved critical snap distances above 250 km",
            passed=unresolved_critical.empty,
            severity="ERROR",
            detail=f"unresolved_critical_gt_250km={len(unresolved_critical)}",
            failures=unresolved_critical if not unresolved_critical.empty else None,
        )
    ]

    if allow_offshore_co2_critical_snaps:
        results.append(
            CheckResult(
                name="Offshore CO2 critical snap exceptions",
                passed=offshore_co2_critical.empty,
                severity="WARNING",
                detail=f"offshore_co2_critical_exceptions={len(offshore_co2_critical)}",
                failures=offshore_co2_critical if not offshore_co2_critical.empty else None,
            )
        )

    results.extend(
        [
            CheckResult(
                name="No audit snap distances above 100 km",
                passed=audit.empty,
                severity="WARNING",
                detail=f"audit_gt_100km={len(audit)}",
                failures=audit if not audit.empty else None,
            ),
            CheckResult(
                name="No snap distances above 25 km",
                passed=warning.empty,
                severity="WARNING",
                detail=f"warning_gt_25km={len(warning)}",
                failures=warning if not warning.empty else None,
            ),
        ]
    )

    return results


# =============================================================================
# Topology audit
# =============================================================================

def audit_topology(
    config: AuditConfig,
    basemap: gpd.GeoDataFrame,
    graph_nodes: gpd.GeoDataFrame,
    graph_edges: pd.DataFrame,
    road_connections: pd.DataFrame,
    road_edges: gpd.GeoDataFrame,
) -> pd.DataFrame:
    if "has_road_connection" in road_connections.columns:
        has_road = road_connections.loc[
            road_connections["has_road_connection"].astype(bool)
        ].copy()
    else:
        has_road = pd.DataFrame()

    rows = [
        {"metric": "basemap_regions", "value": len(basemap)},
        {
            "metric": "basemap_unique_regions",
            "value": basemap["region"].nunique() if "region" in basemap.columns else None,
        },
        {"metric": "graph_nodes", "value": len(graph_nodes)},
        {
            "metric": "graph_node_unique_regions",
            "value": graph_nodes["region"].nunique() if "region" in graph_nodes.columns else None,
        },
        {"metric": "graph_edges", "value": len(graph_edges)},
        {
            "metric": "graph_edge_unique_regions",
            "value": graph_edges["edge_region"].nunique() if "edge_region" in graph_edges.columns else None,
        },
        {"metric": "road_connection_rows", "value": len(road_connections)},
        {"metric": "road_connected_rows", "value": len(has_road)},
        {"metric": "road_edge_geometries", "value": len(road_edges)},
        {"metric": "selected_connection_method", "value": config.connection_method},
    ]

    if "distance_km" in graph_edges.columns:
        stats = summarize_numeric(graph_edges["distance_km"])
        for key, value in stats.items():
            rows.append({"metric": f"graph_edge_distance_{key}", "value": value})

    if not has_road.empty and "distance_km" in has_road.columns:
        stats = summarize_numeric(has_road["distance_km"])
        for key, value in stats.items():
            rows.append({"metric": f"road_edge_distance_{key}", "value": value})

    return pd.DataFrame(rows)


def get_metric(summary: pd.DataFrame, metric: str) -> object | None:
    row = summary.loc[summary["metric"] == metric, "value"]
    return None if row.empty else row.iloc[0]


def numeric_metric(summary: pd.DataFrame, metric: str, default: float = 0.0) -> float:
    value = get_metric(summary, metric)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check_topology_summary(topology_summary: pd.DataFrame) -> list[CheckResult]:
    graph_nodes = numeric_metric(topology_summary, "graph_nodes")
    graph_unique_regions = numeric_metric(topology_summary, "graph_node_unique_regions")
    graph_edges = numeric_metric(topology_summary, "graph_edges")
    road_connection_rows = numeric_metric(topology_summary, "road_connection_rows")
    road_connected_rows = numeric_metric(topology_summary, "road_connected_rows")
    road_edge_geometries = numeric_metric(topology_summary, "road_edge_geometries")

    return [
        CheckResult(
            name="Basemap has regions",
            passed=numeric_metric(topology_summary, "basemap_regions") > 0,
            severity="ERROR",
            detail=f"basemap_regions={numeric_metric(topology_summary, 'basemap_regions'):.0f}",
        ),
        CheckResult(
            name="Graph nodes are unique by region",
            passed=graph_nodes > 0 and graph_nodes == graph_unique_regions,
            severity="ERROR",
            detail=f"graph_nodes={graph_nodes:.0f}, unique_regions={graph_unique_regions:.0f}",
        ),
        CheckResult(
            name="Graph has edges",
            passed=graph_edges > 0,
            severity="ERROR",
            detail=f"graph_edges={graph_edges:.0f}",
        ),
        CheckResult(
            name="Road connectivity file has rows",
            passed=road_connection_rows > 0,
            severity="ERROR",
            detail=f"road_connection_rows={road_connection_rows:.0f}",
        ),
        CheckResult(
            name="Road-connected rows exist",
            passed=road_connected_rows > 0,
            severity="WARNING",
            detail=f"road_connected_rows={road_connected_rows:.0f}",
        ),
        CheckResult(
            name="Road edge geometries exist",
            passed=road_edge_geometries > 0,
            severity="WARNING",
            detail=f"road_edge_geometries={road_edge_geometries:.0f}",
        ),
    ]


# =============================================================================
# Schema audit and schema-construction checks
# =============================================================================

def audit_schema(
    config: AuditConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    table_names = sqlite_table_names(config.schema_path)
    tables = read_schema_tables(config.schema_path)

    count_rows = []
    duplicate_rows = []
    coverage_rows = []

    for table_name in table_names:
        table = tables[table_name]

        count_rows.append(
            {
                "table": table_name,
                "rows": len(table),
                "columns": len(table.columns),
            }
        )

        if table_name in PARAM_TABLES:
            pk = PARAM_TABLES[table_name]["pk"]
            keys = [col for col in pk if col in table.columns]
            missing_keys = [col for col in pk if col not in table.columns]
            duplicated = int(table.duplicated(subset=keys).sum()) if keys else None

            duplicate_rows.append(
                {
                    "table": table_name,
                    "key_columns": ",".join(keys),
                    "missing_key_columns": ",".join(missing_keys),
                    "duplicate_rows": duplicated,
                }
            )

        if "region" in table.columns:
            region_values = table["region"].dropna().astype(str)
            edge_mask = region_values.str.contains(EDGE_REGION_SEPARATOR, regex=False)

            coverage_rows.append(
                {
                    "table": table_name,
                    "node_regions": int((~edge_mask).sum()),
                    "edge_regions": int(edge_mask.sum()),
                    "unique_node_regions": region_values.loc[~edge_mask].nunique(),
                    "unique_edge_regions": region_values.loc[edge_mask].nunique(),
                }
            )

    return (
        pd.DataFrame(count_rows),
        pd.DataFrame(duplicate_rows),
        pd.DataFrame(coverage_rows),
        tables,
    )


def check_schema_tables(table_counts: pd.DataFrame) -> list[CheckResult]:
    counts = dict(zip(table_counts["table"], table_counts["rows"]))

    missing_tables = [table for table in REQUIRED_SCHEMA_TABLES if table not in counts]
    empty_tables = [
        table
        for table in REQUIRED_SCHEMA_TABLES
        if table in counts and int(counts[table]) == 0
    ]

    return [
        CheckResult(
            name="Required schema tables exist",
            passed=len(missing_tables) == 0,
            severity="ERROR",
            detail=f"missing_tables={missing_tables}",
            failures=pd.DataFrame({"missing_table": missing_tables}) if missing_tables else None,
        ),
        CheckResult(
            name="Required schema tables are non-empty",
            passed=len(empty_tables) == 0,
            severity="ERROR",
            detail=f"empty_tables={empty_tables}",
            failures=pd.DataFrame({"empty_table": empty_tables}) if empty_tables else None,
        ),
    ]


def build_reference_sets(
    tables: dict[str, pd.DataFrame],
) -> tuple[dict[str, set[str]], list[CheckResult]]:
    reference_sets: dict[str, set[str]] = {}
    results: list[CheckResult] = []

    for table_name, key_col in SET_TABLES.items():
        if table_name not in tables:
            reference_sets[table_name] = set()
            results.append(
                CheckResult(
                    name=f"Set table exists: {table_name}",
                    passed=False,
                    severity="ERROR",
                    detail=f"{table_name} missing from schema.",
                    ran=False,
                )
            )
            continue

        table = tables[table_name]

        if key_col not in table.columns:
            reference_sets[table_name] = set()
            results.append(
                CheckResult(
                    name=f"Set table key column exists: {table_name}.{key_col}",
                    passed=False,
                    severity="ERROR",
                    detail=f"{table_name}.{key_col} missing.",
                    ran=False,
                )
            )
            continue

        values = set(table[key_col].dropna().astype(str))
        reference_sets[table_name] = values

        results.append(
            CheckResult(
                name=f"Set table is populated: {table_name}",
                passed=len(values) > 0,
                severity="ERROR",
                detail=f"members={len(values):,}",
            )
        )

    return reference_sets, results


def resolve_region_references(values: pd.Series, valid_regions: set[str]) -> pd.DataFrame:
    records = []

    for region in values.dropna().astype(str).unique():
        if region in valid_regions:
            continue

        if is_edge_region(region):
            try:
                region_from, region_to = split_edge_region(region)
            except ValueError:
                records.append({"value": region, "reason": "edge region could not be parsed"})
                continue

            if region_from in valid_regions and region_to in valid_regions:
                continue

            records.append(
                {
                    "value": region,
                    "region_from": region_from,
                    "region_to": region_to,
                    "reason": "edge endpoint missing from Region",
                }
            )
            continue

        records.append({"value": region, "reason": "node region missing from Region"})

    return pd.DataFrame(records)


def resolve_set_membership(
    values: pd.Series,
    valid_values: set[str],
    kind: str,
) -> pd.DataFrame:
    observed = set(values.dropna().astype(str).unique())
    missing = sorted(observed - valid_values)

    return pd.DataFrame(
        {
            "value": missing,
            "reason": [f"{kind} not in defining set"] * len(missing),
        }
    )


def check_schema_referential_integrity(
    tables: dict[str, pd.DataFrame],
    reference_sets: dict[str, set[str]],
) -> list[CheckResult]:
    results: list[CheckResult] = []

    valid_regions = reference_sets.get("Region", set())
    valid_commodities = reference_sets.get("Commodity", set())
    valid_techs = reference_sets.get("Technology", set())

    for table_name, spec in PARAM_TABLES.items():
        required = bool(spec.get("required", True))

        if table_name not in tables:
            results.append(
                CheckResult(
                    name=f"Referential integrity: {table_name}",
                    passed=not required,
                    severity="ERROR" if required else "WARNING",
                    detail="table missing" if required else "optional table absent",
                    ran=not required,
                )
            )
            continue

        table = tables[table_name]
        bad_frames = []

        for col in spec["region"]:
            if col not in table.columns:
                bad_frames.append(
                    pd.DataFrame(
                        [{"table": table_name, "column": col, "value": None, "reason": "reference column missing"}]
                    )
                )
                continue
            bad = resolve_region_references(table[col], valid_regions)
            if not bad.empty:
                bad_frames.append(bad.assign(table=table_name, column=col))

        for col in spec["commodity"]:
            if col not in table.columns:
                bad_frames.append(
                    pd.DataFrame(
                        [{"table": table_name, "column": col, "value": None, "reason": "reference column missing"}]
                    )
                )
                continue
            bad = resolve_set_membership(table[col], valid_commodities, "commodity")
            if not bad.empty:
                bad_frames.append(bad.assign(table=table_name, column=col))

        for col in spec["tech"]:
            if col not in table.columns:
                bad_frames.append(
                    pd.DataFrame(
                        [{"table": table_name, "column": col, "value": None, "reason": "reference column missing"}]
                    )
                )
                continue
            bad = resolve_set_membership(table[col], valid_techs, "technology")
            if not bad.empty:
                bad_frames.append(bad.assign(table=table_name, column=col))

        failures = pd.concat(bad_frames, ignore_index=True) if bad_frames else pd.DataFrame()

        results.append(
            CheckResult(
                name=f"Referential integrity: {table_name}",
                passed=failures.empty,
                severity="ERROR",
                detail=f"dangling_references={len(failures)}",
                failures=failures if not failures.empty else None,
            )
        )

    return results


def check_schema_primary_keys(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results: list[CheckResult] = []

    for table_name, spec in PARAM_TABLES.items():
        required = bool(spec.get("required", True))

        if table_name not in tables:
            results.append(
                CheckResult(
                    name=f"Primary-key uniqueness: {table_name}",
                    passed=not required,
                    severity="ERROR" if required else "WARNING",
                    detail="table missing" if required else "optional table absent",
                    ran=not required,
                )
            )
            continue

        table = tables[table_name]
        pk = spec["pk"]
        missing_cols = [col for col in pk if col not in table.columns]

        if missing_cols:
            results.append(
                CheckResult(
                    name=f"Primary-key uniqueness: {table_name}",
                    passed=False,
                    severity="ERROR",
                    detail=f"missing_pk_columns={missing_cols}",
                    failures=pd.DataFrame({"missing_pk_column": missing_cols}),
                    ran=False,
                )
            )
            continue

        failures = table.loc[table.duplicated(subset=pk, keep=False)].copy()

        results.append(
            CheckResult(
                name=f"Primary-key uniqueness: {table_name}",
                passed=failures.empty,
                severity="ERROR",
                detail=f"duplicate_rows={len(failures)}",
                failures=failures if not failures.empty else None,
            )
        )

    return results


def check_graph_node_region_mapping(
    graph_nodes: gpd.GeoDataFrame,
    tables: dict[str, pd.DataFrame],
) -> CheckResult:
    if "Region" not in tables or "region" not in tables["Region"].columns:
        return CheckResult(
            name="Graph node regions match schema Region",
            passed=False,
            severity="ERROR",
            detail="Region table or Region.region column missing.",
            ran=False,
        )

    if "region" not in graph_nodes.columns:
        return CheckResult(
            name="Graph node regions match schema Region",
            passed=False,
            severity="ERROR",
            detail="graph_nodes missing region column.",
            ran=False,
        )

    graph_region_set = set(graph_nodes["region"].dropna().astype(str))
    schema_region_set = set(tables["Region"]["region"].dropna().astype(str))
    schema_node_set = {region for region in schema_region_set if not is_edge_region(region)}

    graph_missing_from_schema = sorted(graph_region_set - schema_node_set)
    schema_missing_from_graph = sorted(schema_node_set - graph_region_set)

    failures = pd.DataFrame(
        [
            {"region": region, "reason": "graph node missing from schema Region"}
            for region in graph_missing_from_schema
        ]
        + [
            {"region": region, "reason": "schema Region node missing from graph nodes"}
            for region in schema_missing_from_graph
        ]
    )

    return CheckResult(
        name="Graph node regions match schema Region",
        passed=failures.empty,
        severity="ERROR",
        detail=(
            f"graph_missing_from_schema={len(graph_missing_from_schema)}, "
            f"schema_missing_from_graph={len(schema_missing_from_graph)}"
        ),
        failures=failures if not failures.empty else None,
    )


def check_numeric_schema_values(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []

    for table_name, column, rule, severity in NUMERIC_SCHEMA_CHECKS:
        if table_name not in tables:
            results.append(
                CheckResult(
                    name=f"{table_name}.{column} check executed",
                    passed=False,
                    severity="ERROR",
                    detail=f"{table_name} table missing",
                    ran=False,
                )
            )
            continue

        table = tables[table_name]

        if column not in table.columns:
            results.append(
                CheckResult(
                    name=f"{table_name}.{column} exists",
                    passed=False,
                    severity="ERROR",
                    detail=f"missing column {column}",
                    ran=False,
                )
            )
            continue

        values = pd.to_numeric(table[column], errors="coerce")

        if rule == "positive":
            failures = table.loc[values <= 0].copy()
            label = "positive"
        else:
            failures = table.loc[values < 0].copy()
            label = "non-negative"

        results.append(
            CheckResult(
                name=f"{table_name}.{column} is {label}",
                passed=failures.empty,
                severity=severity,
                detail=f"failing_rows={len(failures)}",
                failures=failures if not failures.empty else None,
            )
        )

    return results


def check_etl_segment_monotonicity(tables: dict[str, pd.DataFrame]) -> CheckResult:
    if "ETLSegment" not in tables:
        return CheckResult(
            name="ETLSegment capacity and cost bounds are monotonic",
            passed=False,
            severity="ERROR",
            detail="ETLSegment table missing.",
            ran=False,
        )

    etl = tables["ETLSegment"].copy()
    required = ["region", "tech_or_group", "segment", "cap_lower", "cap_upper", "cost_lower", "cost_upper"]
    missing = [col for col in required if col not in etl.columns]

    if missing:
        return CheckResult(
            name="ETLSegment capacity and cost bounds are monotonic",
            passed=False,
            severity="ERROR",
            detail=f"missing_columns={missing}",
            failures=pd.DataFrame({"missing_column": missing}),
            ran=False,
        )

    for col in ["cap_lower", "cap_upper", "cost_lower", "cost_upper"]:
        etl[col] = pd.to_numeric(etl[col], errors="coerce")

    failures = etl.loc[
        (etl["cap_upper"] < etl["cap_lower"])
        | (etl["cost_upper"] < etl["cost_lower"])
    ].copy()

    return CheckResult(
        name="ETLSegment capacity and cost bounds are monotonic",
        passed=failures.empty,
        severity="ERROR",
        detail=f"failing_rows={len(failures)}",
        failures=failures if not failures.empty else None,
    )


def check_gate_coverage(results: list[CheckResult]) -> CheckResult:
    unran = [result.name for result in results if not result.ran]

    return CheckResult(
        name="Coverage: all declared checks executed",
        passed=len(unran) == 0,
        severity="ERROR",
        detail="all checks ran" if not unran else f"not_run={len(unran)}",
        failures=pd.DataFrame({"check_not_run": unran}) if unran else None,
    )


# =============================================================================
# CSV outputs
# =============================================================================

def write_csv_outputs(
    config: AuditConfig,
    topology_summary: pd.DataFrame,
    snap_audit: pd.DataFrame,
    snap_summary: pd.DataFrame,
    long_snaps: pd.DataFrame,
    table_counts: pd.DataFrame,
    duplicate_summary: pd.DataFrame,
    region_coverage: pd.DataFrame,
    results: list[CheckResult],
) -> None:
    config.audit_dir.mkdir(parents=True, exist_ok=True)

    topology_summary.to_csv(config.audit_dir / "topology_summary.csv", index=False)
    snap_audit.to_csv(config.audit_dir / "snap_audit_all_points.csv", index=False)
    snap_summary.to_csv(config.audit_dir / "snap_summary_by_source.csv", index=False)
    long_snaps.to_csv(config.audit_dir / "snap_audit_long_distance_gt_25km.csv", index=False)

    table_counts.to_csv(config.audit_dir / "schema_table_counts.csv", index=False)
    duplicate_summary.to_csv(config.audit_dir / "schema_duplicate_summary.csv", index=False)
    region_coverage.to_csv(config.audit_dir / "schema_region_coverage.csv", index=False)

    verdict_rows = [
        {
            "name": result.name,
            "passed": result.passed,
            "severity": result.severity,
            "detail": result.detail,
            "ran": result.ran,
        }
        for result in results
    ]
    pd.DataFrame(verdict_rows).to_csv(config.audit_dir / "gate_verdicts.csv", index=False)

    for result in results:
        if result.failures is not None and not result.failures.empty:
            result.failures.to_csv(
                config.audit_dir / f"{safe_csv_name(result.name)}_failures.csv",
                index=False,
            )


# =============================================================================
# Main workflow
# =============================================================================

def main() -> int:
    args = parse_args()

    print_banner("Geospatial-CANOE pre-solve input and schema-construction gate")
    print(f"Project root: {PROJECT_ROOT}")

    config = build_audit_config(
        basemap_stem=args.basemap,
        connection_method=args.connection,
        schema_override=args.schema,
    )

    print("\nSelected gate configuration:")
    print(f"Basemap: {config.basemap_stem}")
    print(f"Road connection method: {config.connection_method}")
    print(f"Schema path: {config.schema_path}")
    print(f"Audit output directory: {config.audit_dir}")

    results: list[CheckResult] = []

    required_paths_result = check_required_paths(config)
    results.append(required_paths_result)

    print_banner("Required input files")
    print_result(required_paths_result, max_rows=args.max_report_rows)

    if not required_paths_result.passed:
        results.append(check_gate_coverage(results))
        print_banner("Pre-solve input gate result")
        print("FAILED: one or more required input files are missing.")
        return 1

    try:
        print_banner("Loading spatial inputs")

        basemap = gpd.read_file(config.basemap_path)
        graph_nodes = gpd.read_file(config.graph_node_path)
        graph_edges = pd.read_csv(config.graph_edge_path)
        road_connections = pd.read_csv(config.road_edge_connections_path)
        road_edges = gpd.read_file(config.road_edges_gpkg_path)

        topology_summary = audit_topology(
            config=config,
            basemap=basemap,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            road_connections=road_connections,
            road_edges=road_edges,
        )

        print("Spatial inputs loaded.")

        print_banner("Preparing point inputs")

        sites_points = prepare_sites_points()
        demand_points = prepare_demand_points()
        co2_points = prepare_co2_points()

        all_points = pd.concat([sites_points, demand_points, co2_points], ignore_index=True)

        print(f"sites_full points: {len(sites_points):,}")
        print(f"demand points: {len(demand_points):,}")
        print(f"positive CO2 facility points: {len(co2_points):,}")
        print(f"all audit points: {len(all_points):,}")

        print_banner("Running spatial snap audit")

        snap_audit = snap_points_to_nodes_for_audit(all_points, graph_nodes)
        snap_summary = summarize_snap_audit(snap_audit)
        long_snaps = snap_audit.loc[snap_audit["snap_distance_m"] > SNAP_WARNING_M].copy()
        long_snaps = long_snaps.sort_values("snap_distance_m", ascending=False)

        print("Snap audit complete.")

        print_banner("Auditing encoded SQLite schema")

        table_counts, duplicate_summary, region_coverage, schema_tables = audit_schema(config)

        print("Schema audit complete.")

    except Exception as exc:  # noqa: BLE001
        print_banner("Input gate error")
        print("Could not complete input and schema gate.")
        print(f"Error: {exc}")
        return 2

    results.extend(check_topology_summary(topology_summary))
    results.extend(
        check_snap_distances(
            snap_audit=snap_audit,
            allow_offshore_co2_critical_snaps=args.allow_offshore_co2_critical_snaps,
        )
    )

    results.extend(check_schema_tables(table_counts))

    reference_sets, set_results = build_reference_sets(schema_tables)
    results.extend(set_results)

    results.extend(
        check_schema_referential_integrity(
            tables=schema_tables,
            reference_sets=reference_sets,
        )
    )

    results.extend(check_schema_primary_keys(schema_tables))
    results.append(check_graph_node_region_mapping(graph_nodes=graph_nodes, tables=schema_tables))
    results.extend(check_numeric_schema_values(schema_tables))
    results.append(check_etl_segment_monotonicity(schema_tables))
    results.append(check_gate_coverage(results))

    print_banner("Pre-solve gate checks")

    any_error = False
    any_warning = False

    for result in results:
        print_result(result, max_rows=args.max_report_rows)

        if result.ran and not result.passed and result.severity == "ERROR":
            any_error = True

        if result.ran and not result.passed and result.severity == "WARNING":
            any_warning = True

        if not result.ran:
            any_error = True

    print_banner("Snap summary")
    print(snap_summary.to_string(index=False))
    print(f"\nLong-distance snap rows (>25 km): {len(long_snaps):,}")

    print_banner("Key schema table counts")
    key_table_counts = table_counts.loc[table_counts["table"].isin(REQUIRED_SCHEMA_TABLES)].copy()
    print(key_table_counts.to_string(index=False))

    if args.write_csv:
        write_csv_outputs(
            config=config,
            topology_summary=topology_summary,
            snap_audit=snap_audit,
            snap_summary=snap_summary,
            long_snaps=long_snaps,
            table_counts=table_counts,
            duplicate_summary=duplicate_summary,
            region_coverage=region_coverage,
            results=results,
        )

        print_banner("CSV reports")
        print(f"Audit outputs written to: {config.audit_dir}")

    print_banner("Pre-solve input and schema-construction gate result")

    if any_error:
        print("FAILED: one or more required input/schema checks failed.")
        return 1

    if any_warning:
        if args.fail_on_warning:
            print("FAILED: warnings present and --fail-on-warning was used.")
            return 1

        print("PASSED WITH WARNINGS: no fatal input/schema checks failed.")
        return 0

    print("PASSED: all input/schema checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
