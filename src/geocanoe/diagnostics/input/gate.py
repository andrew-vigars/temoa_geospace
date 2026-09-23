"""
Pre-solve input and schema-construction gate for Geospatial-CANOE.

This script audits the geospatial preprocessing artifacts and encoded
CANOE/TEMOA SQLite schema before a solve is run. By default it interactively
selects a silver build profile and one of that profile's processed basemaps.

The script does not mutate model inputs or SQLite databases.

Checks
------
1. Required preprocessing files exist.
2. Basemap, graph, road-connectivity, and road-edge products are internally sane.
3. Site and CO2 point inputs can be snapped within acceptable distances.
4. Silver regional gasoline demand exactly covers the selected graph regions.
5. Encoded SQLite schema exists and contains required non-empty set/parameter tables.
6. Graph node regions match schema Region entries.
7. Region, Commodity, and Technology references in parameter tables are valid.
8. Primary-key-like rows are unique in key parameter tables.
9. Numeric sanity checks hold for demand, costs, efficiencies, and ETL bounds.
10. All declared checks executed; skipped checks are treated as gate failures.

Exit codes
----------
0
    All error-level checks passed. Warnings may still be present.

1
    One or more fatal checks failed.

2
    Script error or unreadable input.

Examples
--------
Run the pre-solve gate interactively:

    python diagnostics/check.py inputs

Run it non-interactively:

    python diagnostics/check.py inputs --config config/build_profiles/sample_build_profile.toml --basemap provinces_only_basemap_25km_centroid
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict, cast

import geopandas as gpd
import pandas as pd

from geocanoe.config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    load_model_config,
)
from geocanoe.diagnostics.models import DiagnosticResult
from geocanoe.diagnostics.input.numeric import (
    DEFAULT_NUMERIC_RULES,
    check_numeric_columns,
)
from geocanoe.diagnostics.input.readiness import check_technology_readiness
from geocanoe.diagnostics.input.units import check_table_units
from geocanoe.diagnostics.renderers import render_console_result
from geocanoe.diagnostics.selection import select_numbered
from geocanoe.paths import find_project_root
from geocanoe.regions import PROVINCE_NAME_TO_CODE
from geocanoe.schema.artifacts import (
    resolve_gasoline_demand_artifact_path,
    resolve_schema_artifact_paths,
)
from geocanoe.schema.build import (
    build_schema_fingerprint,
    validate_gasoline_demand_region_coverage,
)


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = find_project_root()
DATA_FILES = PROJECT_ROOT / "data_files"
REGISTRY_DIR = PROJECT_ROOT / "registry"

PROCESSED_BASEMAPS = DATA_FILES / "processed" / "basemaps"
PROCESSED_GRAPH = DATA_FILES / "processed" / "graph"
PROCESSED_ROAD_CONNECTIVITY = DATA_FILES / "processed" / "road_connectivity"
PROCESSED_SCHEMA = DATA_FILES / "processed" / "schema"
PROCESSED_AUDITS = DATA_FILES / "processed" / "audits" / "input_audit"
BUILD_PROFILES = PROJECT_ROOT / "config" / "build_profiles"
MODEL_CONFIG_PATH = REGISTRY_DIR / "model.toml"
BASELINE_SCENARIO_PATH = REGISTRY_DIR / "scenarios" / "baseline.toml"

PROCESSED_LEGACY_INPUTS = DATA_FILES / "processed" / "legacy_inputs"
SITES_PATH = PROCESSED_LEGACY_INPUTS / "sites_full_with_province.csv"
TRANSPORT_TECHS_PATH = REGISTRY_DIR / "transport_techs.csv"
TECHNOLOGIES_PATH = REGISTRY_DIR / "techs.csv"

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
class ParameterTableSpec(TypedDict):
    """Reference and uniqueness columns for one schema parameter table."""

    region: list[str]
    commodity: list[str]
    tech: list[str]
    pk: list[str]
    required: bool


PARAM_TABLES: dict[str, ParameterTableSpec] = {
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

# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class AuditConfig:
    """Resolved files and selections for one pre-solve diagnostic run."""

    basemap_stem: str
    road_layer: str
    connection_method: str
    basemap_path: Path
    graph_node_path: Path
    graph_edge_path: Path
    road_edge_connections_path: Path
    road_edges_gpkg_path: Path
    gasoline_demand_path: Path
    schema_path: Path
    audit_dir: Path


CheckResult = DiagnosticResult


# =============================================================================
# CLI
# =============================================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the optional silver profile and basemap selection."""

    parser = argparse.ArgumentParser(
        prog="check.py inputs",
        description="Validate all inputs associated with one silver build profile.",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Silver build profile. If omitted, choose from config/build_profiles."
        ),
    )
    parser.add_argument(
        "--basemap",
        help="Processed basemap stem. If omitted, choose one for the profile.",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=BASELINE_SCENARIO_PATH,
        help="Model scenario overlay used to identify the Gold database.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        help="Explicit Gold SQLite path, overriding derived artifact identity.",
    )

    return parser.parse_args(argv)


def resolve_profile_selection(
    args: argparse.Namespace,
) -> tuple[Path, GeospatialBuildConfig, str]:
    """Resolve a build profile and its schema-producing artifact selections."""

    if args.config is None:
        profiles = sorted(BUILD_PROFILES.glob("*.toml"))
        config_path = select_numbered(
            profiles,
            "silver configuration",
            display=lambda path: path.stem,
        )
    else:
        config_path = args.config
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path

    build_config = load_geospatial_build_config(config_path)
    if args.basemap is not None:
        basemap_stem = args.basemap
    elif not build_config.schema.interactive_basemap_selection:
        if build_config.schema.basemap_stem is None:
            raise ValueError(f"No basemap is configured in {config_path}")
        basemap_stem = build_config.schema.basemap_stem
    else:
        pattern = (
            f"{build_config.study_area.label}_basemap_*_"
            f"{build_config.basemaps.keep_method}.gpkg"
        )
        basemap_stems = sorted(
            path.stem
            for path in PROCESSED_BASEMAPS.glob(pattern)
            if "_boundary_" not in path.name
        )
        basemap_stem = select_numbered(basemap_stems, "processed basemap")

    return config_path.resolve(), build_config, basemap_stem


# =============================================================================
# Printing helpers
# =============================================================================

def print_banner(title: str) -> None:
    """Print a consistently formatted console section heading."""

    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_result(result: CheckResult, max_rows: int = 20) -> None:
    """Print one diagnostic result with bounded row-level evidence."""

    print(render_console_result(result, max_rows=max_rows))


def safe_csv_name(name: str) -> str:
    """Normalize a diagnostic name for use as a CSV filename."""

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
    road_layer: str,
    connection_method: str,
    build_id: str | None = None,
    scenario_id: str | None = None,
    fingerprint: str | None = None,
    schema_override: Path | None = None,
) -> AuditConfig:
    """Resolve canonical artifacts and the optional schema override."""

    artifacts = resolve_schema_artifact_paths(
        project_root=PROJECT_ROOT,
        basemap_stem=basemap_stem,
        road_layer=road_layer,
        connection_method=connection_method,
        build_id=build_id,
        scenario_id=scenario_id,
        fingerprint=fingerprint,
    )

    default_schema_path = artifacts.schema

    schema_path = schema_override if schema_override is not None else default_schema_path

    if not schema_path.is_absolute():
        schema_path = PROJECT_ROOT / schema_path

    if build_id is None:
        raise ValueError("Input diagnostics require a Silver build ID.")
    gasoline_demand_path = resolve_gasoline_demand_artifact_path(
        PROJECT_ROOT,
        build_id,
        basemap_stem,
    )

    audit_tag = (
        f"{datetime.today().strftime('%Y-%m-%d_%H%M%S')}"
        f"_{basemap_stem}_{connection_method}"
    )

    return AuditConfig(
        basemap_stem=basemap_stem,
        road_layer=road_layer,
        connection_method=connection_method,
        basemap_path=artifacts.basemap,
        graph_node_path=artifacts.graph_nodes,
        graph_edge_path=artifacts.graph_edges,
        road_edge_connections_path=artifacts.road_edge_connections,
        road_edges_gpkg_path=artifacts.road_edges,
        gasoline_demand_path=gasoline_demand_path,
        schema_path=schema_path,
        audit_dir=PROCESSED_AUDITS / audit_tag,
    )


def check_required_paths(config: AuditConfig) -> CheckResult:
    """Check that every required preprocessing and schema artifact exists."""

    required = {
        "basemap": config.basemap_path,
        "graph_nodes": config.graph_node_path,
        "graph_edges": config.graph_edge_path,
        "road_edge_connections": config.road_edge_connections_path,
        "road_edges_gpkg": config.road_edges_gpkg_path,
        "sites_full": SITES_PATH,
        "gasoline_demand": config.gasoline_demand_path,
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
    """Coerce a series to numeric values, representing invalid values as NaN."""

    return pd.to_numeric(series, errors="coerce")


def get_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first candidate column present in a table."""

    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def summarize_numeric(series: pd.Series) -> dict[str, float | int | None]:
    """Return count and distribution statistics for numeric values."""

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
    """Return whether a value uses the edge pseudo-region convention."""

    return isinstance(region, str) and EDGE_REGION_SEPARATOR in region


def split_edge_region(region: str) -> tuple[str, str]:
    """Split and validate an edge pseudo-region into two endpoint regions."""

    left, right = region.split(EDGE_REGION_SEPARATOR, maxsplit=1)
    return left, right


# =============================================================================
# SQLite utilities
# =============================================================================

def sqlite_table_names(db_path: Path) -> list[str]:
    """List SQLite table names in deterministic order."""

    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;",
            conn,
        )["name"].tolist()


def read_sqlite_table(db_path: Path, table_name: str) -> pd.DataFrame:
    """Read one complete SQLite table into a DataFrame."""

    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}";', conn)


def read_schema_tables(db_path: Path) -> dict[str, pd.DataFrame]:
    """Read every table from an encoded schema database."""

    return {
        table_name: read_sqlite_table(db_path, table_name)
        for table_name in sqlite_table_names(db_path)
    }


# =============================================================================
# Point preparation
# =============================================================================

def filter_profile_provinces(
    table: pd.DataFrame,
    provinces: tuple[str, ...],
    source_name: str,
) -> pd.DataFrame:
    """Keep only rows belonging to the selected build profile's study area."""

    if "province" not in table.columns:
        raise ValueError(f"{source_name} must contain a province column.")
    selected = {province.upper() for province in provinces}
    name_to_code = {
        province_name.upper(): province_code
        for province_name, province_code in PROVINCE_NAME_TO_CODE.items()
    }
    raw_provinces = table["province"].astype(str).str.strip().str.upper()
    normalized = raw_provinces.map(name_to_code).fillna(raw_provinces)
    return table.loc[normalized.isin(selected)].copy()


def prepare_sites_points(provinces: tuple[str, ...]) -> pd.DataFrame:
    """Load site coordinates into the common point-audit representation."""

    sites = filter_profile_provinces(
        pd.read_csv(SITES_PATH),
        provinces,
        "sites input",
    )

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


def prepare_co2_points(provinces: tuple[str, ...]) -> pd.DataFrame:
    """Load positive-emission facilities into the point-audit representation."""

    co2 = filter_profile_provinces(
        gpd.read_file(CO2_CLEAN_GPKG_PATH),
        provinces,
        "CO2 facilities input",
    )

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


def check_gasoline_demand_conservation(
    gasoline_regions: pd.DataFrame,
    schema_tables: dict[str, pd.DataFrame],
) -> CheckResult:
    """Check that Gold encodes positive Silver regional demand unchanged."""

    silver = gasoline_regions.loc[
        pd.to_numeric(gasoline_regions["demand"], errors="coerce") > 0,
        ["region", "demand"],
    ].copy()
    gold = schema_tables.get("Demand", pd.DataFrame()).copy()
    if "commodity" in gold.columns:
        gold = gold.loc[gold["commodity"].eq("d_gsl")].copy()

    silver_regions = set(silver["region"].astype(str))
    gold_regions = (
        set(gold["region"].astype(str))
        if "region" in gold.columns
        else set()
    )
    silver_total = float(pd.to_numeric(silver["demand"], errors="coerce").sum())
    gold_total = (
        float(pd.to_numeric(gold["demand"], errors="coerce").sum())
        if "demand" in gold.columns
        else 0.0
    )
    passed = (
        silver_regions == gold_regions
        and abs(silver_total - gold_total) <= 1e-6
    )
    failures = None
    if not passed:
        failures = pd.DataFrame(
            [
                {
                    "silver_total_t_per_year": silver_total,
                    "gold_total_t_per_year": gold_total,
                    "missing_gold_regions": ",".join(
                        sorted(silver_regions - gold_regions)
                    ),
                    "extra_gold_regions": ",".join(
                        sorted(gold_regions - silver_regions)
                    ),
                }
            ]
        )
    return CheckResult(
        name="Gold gasoline demand conserves Silver regional demand",
        passed=passed,
        severity="ERROR",
        detail=(
            f"silver_total={silver_total:.6f}; "
            f"gold_total={gold_total:.6f}"
        ),
        failures=failures,
    )


# =============================================================================
# Spatial snap audit
# =============================================================================

def snap_points_to_nodes_for_audit(
    points: pd.DataFrame,
    graph_nodes: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Assign points to containing or nearest graph regions for audit purposes."""

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

    nodes_for_within = nodes_gdf.to_crs("EPSG:4326")

    within = gpd.sjoin(
        points_gdf,
        nodes_for_within,
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
    """Summarize spatial matching and distance thresholds by point source."""

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
    """Evaluate warning, audit, and critical point-snap thresholds."""

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
    """Summarize basemap, graph, and road-connectivity topology metrics."""

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
    """Return one named topology metric, or None when it is absent."""

    row = summary.loc[summary["metric"] == metric, "value"]
    return None if row.empty else row.iloc[0]


def numeric_metric(summary: pd.DataFrame, metric: str, default: float = 0.0) -> float:
    """Return one topology metric as a float with a safe default."""

    value = get_metric(summary, metric)
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def check_topology_summary(topology_summary: pd.DataFrame) -> list[CheckResult]:
    """Convert topology metrics into model-input diagnostic results."""

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
    """Inventory schema tables, duplicate keys, and region coverage."""

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
    """Check that required schema tables exist and contain rows."""

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
    """Build defining Region, Commodity, and Technology membership sets."""

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
    """Return invalid node and edge-region references with failure reasons."""

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
    """Return values absent from a defining model set."""

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
    """Validate parameter references against defining model sets."""

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
    """Check table-specific composite keys for duplicate parameter rows."""

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
    """Check exact agreement between graph nodes and schema node regions."""

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
    """Validate required numeric schema columns using strict coercion rules."""

    return check_numeric_columns(tables, DEFAULT_NUMERIC_RULES)


def check_etl_segment_monotonicity(tables: dict[str, pd.DataFrame]) -> CheckResult:
    """Check that ETL capacity and cost upper bounds exceed lower bounds."""

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
    """Fail the gate when any declared diagnostic did not execute."""

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
    technology_readiness: pd.DataFrame,
    unit_inventory: pd.DataFrame,
    results: list[CheckResult],
) -> None:
    """Write detailed pre-solve audit tables and row-level evidence."""

    config.audit_dir.mkdir(parents=True, exist_ok=True)

    topology_summary.to_csv(config.audit_dir / "topology_summary.csv", index=False)
    snap_audit.to_csv(config.audit_dir / "snap_audit_all_points.csv", index=False)
    snap_summary.to_csv(config.audit_dir / "snap_summary_by_source.csv", index=False)
    long_snaps.to_csv(config.audit_dir / "snap_audit_long_distance_gt_25km.csv", index=False)

    table_counts.to_csv(config.audit_dir / "schema_table_counts.csv", index=False)
    duplicate_summary.to_csv(config.audit_dir / "schema_duplicate_summary.csv", index=False)
    region_coverage.to_csv(config.audit_dir / "schema_region_coverage.csv", index=False)
    technology_readiness.to_csv(
        config.audit_dir / "technology_readiness.csv",
        index=False,
    )
    unit_inventory.to_csv(config.audit_dir / "unit_inventory.csv", index=False)

    write_result_outputs(config, results)


def write_result_outputs(
    config: AuditConfig,
    results: list[CheckResult],
) -> None:
    """Write verdicts and available row-level evidence for diagnostic results."""

    config.audit_dir.mkdir(parents=True, exist_ok=True)
    verdict_rows = [
        {
            "check_id": result.check_id,
            "name": result.name,
            "stage": result.stage,
            "passed": result.passed,
            "severity": result.severity,
            "detail": result.detail,
            "ran": result.ran,
            "remediation": result.remediation,
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

def main(argv: list[str] | None = None) -> int:
    """Run the complete pre-solve diagnostic gate and return its exit code."""

    args = parse_args(argv)

    try:
        profile_path, build_config, basemap_stem = resolve_profile_selection(
            args
        )
        model_config = load_model_config(MODEL_CONFIG_PATH, args.scenario)
        fingerprint = build_schema_fingerprint(
            build_config,
            model_config,
            basemap_stem,
        )
        road_layer = build_config.road_connectivity.road_layer
        connection_method = build_config.schema.road_connection_method
        provinces = build_config.study_area.provinces
        config = build_audit_config(
            basemap_stem=basemap_stem,
            road_layer=road_layer,
            connection_method=connection_method,
            build_id=build_config.build_id,
            scenario_id=model_config.scenario.scenario_id,
            fingerprint=fingerprint,
            schema_override=args.schema,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        print_banner("Input diagnostic selection error")
        print(exc)
        return 2

    print_banner("Geospatial-CANOE input diagnostics")
    print(f"Project root: {PROJECT_ROOT}")

    print("\nSelected gate configuration:")
    print(f"Silver profile: {profile_path}")
    print(f"Basemap: {config.basemap_stem}")
    print(f"Road layer: {config.road_layer}")
    print(f"Road connection method: {config.connection_method}")
    print(f"Study area provinces: {', '.join(provinces)}")
    print(f"Schema path: {config.schema_path}")
    print(f"Audit output directory: {config.audit_dir}")

    results: list[CheckResult] = []

    required_paths_result = check_required_paths(config)
    results.append(required_paths_result)

    print_banner("Required input files")
    print_result(required_paths_result)

    if not required_paths_result.passed:
        results.append(check_gate_coverage(results))
        write_result_outputs(config, results)
        print(f"\nCSV reports written to: {config.audit_dir}")
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
        gasoline_regions = gpd.read_file(
            config.gasoline_demand_path,
            layer="regional_gasoline_demand",
        )
        validate_gasoline_demand_region_coverage(
            gasoline_regions,
            graph_nodes,
        )

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

        sites_points = prepare_sites_points(provinces)
        co2_points = prepare_co2_points(provinces)

        all_points = pd.concat([sites_points, co2_points], ignore_index=True)

        print(f"sites_full points: {len(sites_points):,}")
        print(f"regional gasoline-demand rows: {len(gasoline_regions):,}")
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

    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
        print_banner("Input gate error")
        print("Could not complete input and schema gate.")
        print(f"Error: {exc}")
        return 2

    results.extend(check_topology_summary(topology_summary))
    results.extend(
        check_snap_distances(
            snap_audit=snap_audit,
            allow_offshore_co2_critical_snaps=False,
        )
    )

    results.extend(check_schema_tables(table_counts))
    results.append(
        check_gasoline_demand_conservation(
            gasoline_regions,
            schema_tables,
        )
    )

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
    technology_readiness, readiness_results = check_technology_readiness(schema_tables)
    results.extend(readiness_results)
    unit_inventory, unit_results = check_table_units(
        schema_tables,
        strict=False,
    )
    results.extend(unit_results)
    results.extend(check_numeric_schema_values(schema_tables))
    results.append(check_etl_segment_monotonicity(schema_tables))
    results.append(check_gate_coverage(results))

    print_banner("Pre-solve gate checks")

    any_error = False
    any_warning = False

    for result in results:
        print_result(result)

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

    print_banner("Technology readiness")
    print(technology_readiness.to_string(index=False))

    print_banner("Unit inventory")
    print(unit_inventory.to_string(index=False))

    write_csv_outputs(
        config=config,
        topology_summary=topology_summary,
        snap_audit=snap_audit,
        snap_summary=snap_summary,
        long_snaps=long_snaps,
        table_counts=table_counts,
        duplicate_summary=duplicate_summary,
        region_coverage=region_coverage,
        technology_readiness=technology_readiness,
        unit_inventory=unit_inventory,
        results=results,
    )

    print_banner("CSV reports")
    print(f"Audit outputs written to: {config.audit_dir}")

    print_banner("Pre-solve input and schema-construction gate result")

    if any_error:
        print("FAILED: one or more required input/schema checks failed.")
        return 1

    if any_warning:
        print("PASSED WITH WARNINGS: no fatal input/schema checks failed.")
        return 0

    print("PASSED: all input/schema checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
