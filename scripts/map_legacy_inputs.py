"""
map_legacy_inputs.py

Assign Canadian province or territory codes to the legacy latitude/longitude
site and demand tables. This is a one-time preprocessing step so routine schema
builds do not repeatedly dissolve or buffer Canada's detailed coastline.

Inputs
------
data_files/raw/basemaps/*.shp
data_files/sites_full.csv
data_files/demand.csv

Outputs
-------
data_files/processed/legacy_inputs/
    sites_full_with_province.csv
    demand_with_province.csv
    legacy_input_province_assignment_audit.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
import sys

import geopandas as gpd
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from project_config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)


DATA_FILES = PROJECT_ROOT / "data_files"
RAW_BASEMAPS = DATA_FILES / "raw" / "basemaps"
SITES_PATH = DATA_FILES / "sites_full.csv"
DEMAND_PATH = DATA_FILES / "demand.csv"
OUTPUT_DIR = DATA_FILES / "processed" / "legacy_inputs"

WGS84_CRS = "EPSG:4326"
METRIC_CRS = "EPSG:3347"
MAX_NEAREST_PROVINCE_DISTANCE_KM = 50.0

PROVINCE_NAME_TO_CODE = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Québec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}


def discover_boundary_path() -> Path:
    """Return the single raw province/territory boundary shapefile."""

    shapefiles = sorted(RAW_BASEMAPS.glob("*.shp"))
    if len(shapefiles) != 1:
        raise ValueError(
            f"Expected exactly one shapefile in {RAW_BASEMAPS}, found "
            f"{len(shapefiles)}: {[path.name for path in shapefiles]}"
        )
    return shapefiles[0]


def identify_province_name_column(
    provinces: gpd.GeoDataFrame,
) -> str:
    """Identify the English province-name field."""

    for column in ["PRENAME", "PRNAME", "province_name"]:
        if column in provinces.columns:
            return column

    raise ValueError(
        "Could not identify province-name column. Available columns: "
        f"{list(provinces.columns)}"
    )


def load_provinces(boundary_path: Path) -> gpd.GeoDataFrame:
    """Load province polygons with canonical two-letter codes."""

    provinces = gpd.read_file(boundary_path)
    if provinces.empty or provinces.crs is None:
        raise ValueError(
            f"Province boundary is empty or lacks a CRS: {boundary_path}"
        )

    name_column = identify_province_name_column(provinces)
    provinces = provinces.copy()
    provinces["province_name"] = (
        provinces[name_column].astype(str).str.strip()
    )
    provinces["province"] = provinces["province_name"].map(
        PROVINCE_NAME_TO_CODE
    )

    unmapped = sorted(
        provinces.loc[
            provinces["province"].isna(),
            "province_name",
        ].unique()
    )
    if unmapped:
        raise ValueError(
            f"Province names missing from mapping dictionary: {unmapped}"
        )

    return (
        provinces[["province", "province_name", "geometry"]]
        .to_crs(WGS84_CRS)
        .reset_index(drop=True)
    )


def validate_point_columns(
    data: pd.DataFrame,
    dataset_label: str,
) -> pd.DataFrame:
    """Validate and normalize legacy longitude and latitude columns."""

    required = {"lon", "lat"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(
            f"{dataset_label} is missing columns: {sorted(missing)}"
        )

    output = data.copy()
    output["lon"] = pd.to_numeric(output["lon"], errors="coerce")
    output["lat"] = pd.to_numeric(output["lat"], errors="coerce")

    invalid = output[["lon", "lat"]].isna().any(axis=1)
    if invalid.any():
        raise ValueError(
            f"{dataset_label} contains {int(invalid.sum()):,} invalid "
            "longitude/latitude row(s)."
        )

    return output


def assign_provinces(
    data: pd.DataFrame,
    provinces: gpd.GeoDataFrame,
    dataset_label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign province codes by polygon join with a bounded nearest fallback."""

    total_start = perf_counter()
    data = validate_point_columns(data, dataset_label)

    points = gpd.GeoDataFrame(
        data,
        geometry=gpd.points_from_xy(data["lon"], data["lat"]),
        crs=WGS84_CRS,
    )

    print(
        f"\n{dataset_label}: assigning {len(points):,} point(s) to "
        "province polygons...",
        flush=True,
    )

    joined = gpd.sjoin(
        points,
        provinces[["province", "province_name", "geometry"]],
        how="left",
        predicate="within",
    ).drop(columns="index_right")

    unmatched_mask = joined["province"].isna()
    n_unmatched = int(unmatched_mask.sum())

    print(
        f"{dataset_label}: direct polygon join assigned "
        f"{len(joined) - n_unmatched:,}; {n_unmatched:,} require nearest "
        "province fallback.",
        flush=True,
    )

    joined["province_assignment_method"] = "within"
    joined["province_distance_km"] = 0.0

    if n_unmatched:
        unmatched_metric = joined.loc[unmatched_mask].to_crs(METRIC_CRS)
        provinces_metric = provinces.to_crs(METRIC_CRS)

        nearest = gpd.sjoin_nearest(
            unmatched_metric.drop(
                columns=["province", "province_name"],
                errors="ignore",
            ),
            provinces_metric[["province", "province_name", "geometry"]],
            how="left",
            max_distance=MAX_NEAREST_PROVINCE_DISTANCE_KM * 1_000.0,
            distance_col="province_distance_m",
        )
        nearest = nearest.loc[
            ~nearest.index.duplicated(keep="first")
        ]

        resolved = nearest["province"].notna()
        resolved_index = nearest.index[resolved]

        joined.loc[resolved_index, "province"] = (
            nearest.loc[resolved_index, "province"]
        )
        joined.loc[resolved_index, "province_name"] = (
            nearest.loc[resolved_index, "province_name"]
        )
        joined.loc[
            resolved_index,
            "province_assignment_method",
        ] = "nearest"
        joined.loc[
            resolved_index,
            "province_distance_km",
        ] = (
            nearest.loc[resolved_index, "province_distance_m"] / 1_000.0
        )

    unresolved = joined["province"].isna()

    audit = pd.DataFrame(
        joined.loc[unresolved]
        .drop(columns="geometry", errors="ignore")
        .copy()
    )
    audit.insert(0, "dataset", dataset_label)

    mapped = pd.DataFrame(
        joined.loc[~unresolved]
        .drop(columns="geometry", errors="ignore")
        .copy()
    )

    print(
        f"{dataset_label}: mapped {len(mapped):,}; unresolved "
        f"{len(audit):,}; completed in "
        f"{perf_counter() - total_start:.1f} s.",
        flush=True,
    )

    return mapped, audit


def run_legacy_input_mapping(
    config: GeospatialBuildConfig,
) -> tuple[Path, Path, Path]:
    """Map both legacy tables and export reusable processed files."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    boundary_path = discover_boundary_path()
    provinces = load_provinces(boundary_path)

    print(f"\nBoundary source: {boundary_path}")
    print(
        "Configured study area: "
        + ", ".join(config.study_area.provinces)
    )

    sites = pd.read_csv(SITES_PATH)
    demand = pd.read_csv(DEMAND_PATH)

    sites_mapped, sites_audit = assign_provinces(
        sites,
        provinces,
        "sites_full",
    )
    demand_mapped, demand_audit = assign_provinces(
        demand,
        provinces,
        "demand",
    )

    sites_output = OUTPUT_DIR / "sites_full_with_province.csv"
    demand_output = OUTPUT_DIR / "demand_with_province.csv"
    audit_output = (
        OUTPUT_DIR / "legacy_input_province_assignment_audit.csv"
    )

    sites_mapped.to_csv(sites_output, index=False)
    demand_mapped.to_csv(demand_output, index=False)

    audit = pd.concat(
        [sites_audit, demand_audit],
        ignore_index=True,
        sort=False,
    )
    audit.to_csv(audit_output, index=False)

    print("\nLegacy input mapping complete.")
    print(f"Sites output:  {sites_output}")
    print(f"Demand output: {demand_output}")
    print(f"Audit output:  {audit_output}")
    print(
        "Province assignment counts:\n"
        + pd.concat(
            [
                sites_mapped.assign(dataset="sites_full"),
                demand_mapped.assign(dataset="demand"),
            ],
            ignore_index=True,
            sort=False,
        )
        .groupby(["dataset", "province"])
        .size()
        .rename("rows")
        .to_string()
    )

    return sites_output, demand_output, audit_output


def parse_args() -> argparse.Namespace:
    """Parse the shared build profile path."""

    parser = argparse.ArgumentParser(
        description=(
            "Assign province codes to legacy site and demand point inputs."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a geospatial preprocessing TOML profile.",
    )
    return parser.parse_args()


def main() -> None:
    """Run legacy province assignment."""

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_legacy_input_mapping(config)


if __name__ == "__main__":
    main()
