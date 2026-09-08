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

import geopandas as gpd
import pandas as pd

from geocanoe.config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)
from geocanoe.paths import find_project_root


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = find_project_root()


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
    """Discover the raw province and territory boundary shapefile.

    The raw basemap directory must contain exactly one shapefile. The discovered
    path is returned for use by the legacy-input province-assignment workflow.

    Returns
    -------
    Path
        Path to the single shapefile in ``RAW_BASEMAPS``.

    Raises
    ------
    ValueError
        If the raw basemap directory contains zero or multiple shapefiles.
    """

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
    """Identify the English province-name column in a boundary dataset.

    The recognized source fields are checked in priority order so the function can
    support both Statistics Canada naming conventions and an already standardized
    ``province_name`` column.

    Parameters
    ----------
    provinces : gpd.GeoDataFrame
        Province and territory boundary data containing a recognized English
        province-name field.

    Returns
    -------
    str
        Name of the first recognized province-name column.

    Raises
    ------
    ValueError
        If none of the recognized province-name columns are present.
    """

    for column in ["PRENAME", "PRNAME", "province_name"]:
        if column in provinces.columns:
            return column

    raise ValueError(
        "Could not identify province-name column. Available columns: "
        f"{list(provinces.columns)}"
    )


def load_provinces(boundary_path: Path) -> gpd.GeoDataFrame:
    """Load and standardize province and territory boundary polygons.

    The boundary file is loaded as a GeoDataFrame, validated for non-empty
    geometry and a defined coordinate reference system, and assigned canonical
    two-letter province or territory codes using ``PROVINCE_NAME_TO_CODE``.
    The standardized result is reprojected to ``WGS84_CRS``.

    Parameters
    ----------
    boundary_path : Path
        Path to the province and territory boundary file.

    Returns
    -------
    gpd.GeoDataFrame
        Province and territory polygons containing canonical ``province`` codes,
        standardized ``province_name`` values, and geometry in ``WGS84_CRS``.

    Raises
    ------
    ValueError
        If the boundary dataset is empty, has no defined CRS, lacks a recognized
        province-name column, or contains province names that are absent from
        ``PROVINCE_NAME_TO_CODE``.
    """

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
    """Validate and normalize legacy point-coordinate columns.

    The input table must contain ``lon`` and ``lat`` columns. Their values are
    converted to numeric form in a copy of the input DataFrame, with values that
    cannot be converted treated as missing.

    Parameters
    ----------
    data : pd.DataFrame
        Legacy point dataset containing longitude and latitude columns.
    dataset_label : str
        Human-readable dataset name used in validation error messages.

    Returns
    -------
    pd.DataFrame
        Copy of the input table with numeric ``lon`` and ``lat`` columns.

    Raises
    ------
    ValueError
        If either required coordinate column is missing or any coordinate value
        is missing or non-numeric after conversion.
    """

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
    """Assign province codes to legacy point records.

    Point coordinates are first validated and converted to a WGS84 GeoDataFrame.
    Each point is assigned to the province polygon that spatially contains it.
    Points not captured by the direct polygon join are reprojected to
    ``METRIC_CRS`` and assigned to the nearest province within
    ``MAX_NEAREST_PROVINCE_DISTANCE_KM``.

    The returned mapped table records whether each assignment was made by the
    direct polygon join or the nearest-province fallback, along with the fallback
    distance. Records that remain unresolved are returned separately for auditing.

    Parameters
    ----------
    data : pd.DataFrame
        Legacy point dataset containing ``lon`` and ``lat`` columns.
    provinces : gpd.GeoDataFrame
        Province and territory polygons containing ``province``,
        ``province_name``, and geometry columns.
    dataset_label : str
        Human-readable dataset name used in progress messages and the audit table.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        A mapped point table containing resolved province assignments and an audit
        table containing records that could not be assigned within the maximum
        nearest-province distance.

    Raises
    ------
    ValueError
        If the input point coordinates fail validation.
    """

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
    """Map legacy site and demand records to provinces and export the results.

    The raw province and territory boundary file is discovered and standardized,
    then the legacy site and demand tables are loaded and assigned province codes
    using ``assign_provinces``. Resolved records are written to reusable processed
    CSV files, while unresolved records from both datasets are combined into a
    single audit table.

    The supplied build configuration is used to report the intended study-area
    provinces, but province assignment is performed against the complete raw
    province and territory boundary dataset.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the configured study-area
        provinces.

    Returns
    -------
    tuple[Path, Path, Path]
        Paths to the processed site table, processed demand table, and combined
        unresolved-assignment audit CSV.

    Raises
    ------
    FileNotFoundError
        If a required boundary, site, or demand input file is missing.
    ValueError
        If boundary discovery, boundary standardization, coordinate validation, or
        province assignment fails.
    """

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
    """Parse the command-line argument for the shared build profile.

    The command-line interface requires a path to the TOML configuration shared
    across the Geospatial-CANOE preprocessing stages.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments containing the required ``config`` path.
    """

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
    """Load the shared build profile and run legacy province assignment.

    Command-line arguments are parsed to obtain the TOML build-profile path. The
    profile is then loaded, validated, printed to the console, and passed to the
    legacy site-and-demand province-mapping workflow.

    Returns
    -------
    None
    """

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_legacy_input_mapping(config)


if __name__ == "__main__":
    main()
