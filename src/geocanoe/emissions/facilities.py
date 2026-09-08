"""
build_emissions.py

Build standardized greenhouse-gas emissions input files for the Geospatial-CANOE
schema-building workflow.

This script converts the official 2024 Greenhouse Gas Emissions from Large
Facilities dataset into clean, traceable intermediate files used downstream by
build_schema.py.

Inputs:
    data_files/raw/emissions/co2_large_facilities_2024/
        Greenhouse gas emissions from large facilities - 2024.csv
        AirEmissions_GHG_2024.json

Outputs:
    data_files/processed/emissions/co2_large_facilities_2024/
        co2_large_facilities_2024_clean.csv
        co2_large_facilities_2024_clean.gpkg
        co2_large_facilities_2024_metadata.csv
        co2_large_facilities_2024_column_audit.csv
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd

from geocanoe.paths import find_project_root


# =============================================================================
# Dataset configuration
# =============================================================================

DATASET_YEAR = 2024
DATASET_NAME = "co2_large_facilities"
DATASET_PROVIDER = "Environment and Climate Change Canada (ECCC)"
DATASET_TITLE = "Greenhouse Gas Emissions from Large Facilities"
EMISSIONS_UNIT = "kt CO2e/year"
GEOMETRY_TYPE = "Facility point locations"
SOURCE_FORMATS = ["CSV", "GeoJSON"]


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = find_project_root()

RAW_DIR = PROJECT_ROOT / "data_files" / "raw" / "emissions" / f"{DATASET_NAME}_{DATASET_YEAR}"
PROCESSED_DIR = PROJECT_ROOT / "data_files" / "processed" / "emissions" / f"{DATASET_NAME}_{DATASET_YEAR}"

CO2_CSV_PATH = RAW_DIR / "Greenhouse gas emissions from large facilities - 2024.csv"
CO2_JSON_PATH = RAW_DIR / "AirEmissions_GHG_2024.json"

CO2_CLEAN_CSV = PROCESSED_DIR / f"{DATASET_NAME}_{DATASET_YEAR}_clean.csv"
CO2_CLEAN_GPKG = PROCESSED_DIR / f"{DATASET_NAME}_{DATASET_YEAR}_clean.gpkg"
CO2_METADATA_CSV = PROCESSED_DIR / f"{DATASET_NAME}_{DATASET_YEAR}_metadata.csv"
CO2_COLUMN_AUDIT_CSV = PROCESSED_DIR / f"{DATASET_NAME}_{DATASET_YEAR}_column_audit.csv"


# =============================================================================
# Canonical schema
# =============================================================================

CO2_COLUMN_MAP = {
    "Facility ID": "facility_id",
    "Facility name": "facility_name",
    "Company name": "company_name",
    "City": "city",
    "Address": "address",
    "Postal code": "postal_code",
    "Province": "province",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Total emissions": "emissions_kt_co2e_per_year",
    "Unit": "source_unit",
    "Year": "year",
    "Report year": "report_year",
    "Industry classification": "industry_classification",
    "Industry classification link": "industry_classification_link",
    "Facility information": "facility_information",
    "Facility details": "facility_details",
    "More information": "more_information",
}

NUMERIC_COLUMNS = [
    "latitude",
    "longitude",
    "emissions_kt_co2e_per_year",
    "year",
    "report_year",
]

CANADA_LAT_MIN = 40
CANADA_LAT_MAX = 85
CANADA_LON_MIN = -145
CANADA_LON_MAX = -45


# =============================================================================
# Helpers
# =============================================================================

def validate_source_files() -> None:
    """Verify that the required raw emissions input files exist.

    The emissions preprocessing workflow requires both the source CSV and
    GeoJSON files before any cleaning, auditing, or spatial processing can be
    performed. This function checks for those files and fails early if either
    source is missing.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If the required emissions CSV or GeoJSON file is missing.
    """
    missing = [path for path in [CO2_CSV_PATH, CO2_JSON_PATH] if not path.exists()]

    if missing:
        print("\nMissing required emissions input files:")
        for path in missing:
            print(f"  {path}")
        raise FileNotFoundError("One or more required emissions input files are missing.")

    print("\nInput emissions files found:")
    print(f"  CSV:     {CO2_CSV_PATH}")
    print(f"  GeoJSON: {CO2_JSON_PATH}")


def load_sources() -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Load the raw emissions CSV and GeoJSON source files.

    The CSV provides the tabular facility emissions records used for cleaning
    and standardization. The GeoJSON is loaded alongside it to confirm that the
    spatial source file is readable and to report basic source metadata such as
    row count and CRS.

    Returns
    -------
    tuple[pd.DataFrame, gpd.GeoDataFrame]
        Raw emissions CSV as a pandas DataFrame and raw emissions GeoJSON as a
        GeoPandas GeoDataFrame.
    """
    co2_csv = pd.read_csv(CO2_CSV_PATH)
    co2_geo = gpd.read_file(CO2_JSON_PATH)

    print("\nSource datasets loaded:")
    print(f"  CSV rows:     {len(co2_csv):,}")
    print(f"  CSV columns:  {len(co2_csv.columns):,}")
    print(f"  GeoJSON rows: {len(co2_geo):,}")
    print(f"  GeoJSON CRS:  {co2_geo.crs}")

    if len(co2_csv) != len(co2_geo):
        print("  WARNING: CSV and GeoJSON row counts differ.")

    return co2_csv, co2_geo


def build_column_audit(co2_csv: pd.DataFrame) -> pd.DataFrame:
    """Build an audit table linking source columns to standardized names.

    This function checks the raw emissions CSV against the expected column map,
    fails if any required source columns are missing, and records how each
    source column is standardized for the cleaned dataset. Additional source
    columns are allowed and preserved in the audit table with a blank
    standardized-column value.

    Parameters
    ----------
    co2_csv : pd.DataFrame
        Raw emissions CSV loaded from the source file.

    Returns
    -------
    pd.DataFrame
        Column audit table containing the original source column names,
        standardized column names where applicable, and source data types.

    Raises
    ------
    ValueError
        If one or more expected source columns are missing from the CSV.
    """
    expected_columns = set(CO2_COLUMN_MAP)
    actual_columns = set(co2_csv.columns)

    missing_columns = sorted(expected_columns - actual_columns)
    extra_columns = sorted(actual_columns - expected_columns)

    if missing_columns:
        raise ValueError(f"Missing expected CO2 CSV columns: {missing_columns}")

    if extra_columns:
        print(f"\nAdditional source columns detected and preserved: {extra_columns}")
    else:
        print("\nAll source columns accounted for.")

    return pd.DataFrame(
        {
            "original_column": co2_csv.columns,
            "standardized_column": [CO2_COLUMN_MAP.get(col, "") for col in co2_csv.columns],
            "dtype": co2_csv.dtypes.astype(str).values,
        }
    )


def standardize_emissions_table(co2_csv: pd.DataFrame) -> pd.DataFrame:
    """Standardize the raw emissions table for downstream processing.

    This function applies the canonical emissions column names, coerces known
    numeric fields to numeric types, and adds an ``is_spatially_assignable``
    flag. A facility is treated as spatially assignable when its latitude and
    longitude fall within broad Canada coordinate bounds and are not zero.

    Parameters
    ----------
    co2_csv : pd.DataFrame
        Raw emissions CSV loaded from the source file.

    Returns
    -------
    pd.DataFrame
        Standardized emissions table with renamed columns, numeric fields
        converted where possible, and a spatial assignment flag.
    """
    co2 = co2_csv.rename(columns=CO2_COLUMN_MAP).copy()

    for col in NUMERIC_COLUMNS:
        co2[col] = pd.to_numeric(co2[col], errors="coerce")

    co2["is_spatially_assignable"] = (
        co2["latitude"].between(CANADA_LAT_MIN, CANADA_LAT_MAX)
        & co2["longitude"].between(CANADA_LON_MIN, CANADA_LON_MAX)
        & ~((co2["latitude"] == 0) | (co2["longitude"] == 0))
    )

    return co2


def validate_standardized_table(co2: pd.DataFrame) -> None:
    """Validate and summarize the standardized emissions table.

    This function reports basic data-quality diagnostics for the cleaned
    emissions table, including missing identifiers, missing coordinates,
    missing emissions values, duplicate facility IDs, zero or negative
    emissions, source units, reported data years, and spatial assignability.

    Rows with coordinates outside the broad Canada coordinate bounds, or with
    zero latitude or longitude, are summarized as invalid or non-spatial. The
    function currently treats negative emissions as a hard validation error.

    Parameters
    ----------
    co2 : pd.DataFrame
        Standardized emissions table produced by
        ``standardize_emissions_table``.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If one or more facilities report negative emissions.
    """
    checks = {
        "missing_facility_id": int(co2["facility_id"].isna().sum()),
        "missing_facility_name": int(co2["facility_name"].isna().sum()),
        "missing_latitude": int(co2["latitude"].isna().sum()),
        "missing_longitude": int(co2["longitude"].isna().sum()),
        "missing_emissions": int(co2["emissions_kt_co2e_per_year"].isna().sum()),
        "negative_emissions": int((co2["emissions_kt_co2e_per_year"] < 0).sum()),
        "zero_emissions": int((co2["emissions_kt_co2e_per_year"] == 0).sum()),
        "duplicate_facility_id": int(co2["facility_id"].duplicated().sum()),
        "spatially_assignable": int(co2["is_spatially_assignable"].sum()),
        "non_spatial": int((~co2["is_spatially_assignable"]).sum()),
    }

    print("\nValidation summary:")
    for name, value in checks.items():
        print(f"  {name}: {value:,}")

    print("\nReported source units:")
    print(co2["source_unit"].value_counts(dropna=False).to_string())

    print("\nReported data years:")
    print(co2["year"].value_counts(dropna=False).sort_index().to_string())

    invalid_coordinates = co2.loc[
        (co2["latitude"] == 0)
        | (co2["longitude"] == 0)
        | ~co2["latitude"].between(CANADA_LAT_MIN, CANADA_LAT_MAX)
        | ~co2["longitude"].between(CANADA_LON_MIN, CANADA_LON_MAX)
    ].copy()

    print(f"\nRows with invalid/non-Canada coordinates: {len(invalid_coordinates):,}")
    print(
        "Invalid/non-spatial emissions: "
        f"{invalid_coordinates['emissions_kt_co2e_per_year'].sum():,.2f} kt CO2e/year"
    )

    if checks["negative_emissions"] > 0:
        raise ValueError("Negative facility emissions detected.")


def build_spatial_emissions(co2: pd.DataFrame) -> gpd.GeoDataFrame:
    """Build a spatial emissions layer from assignable facility records.

    This function filters the standardized emissions table to facilities with
    valid, spatially assignable coordinates and converts those records into
    point geometries using longitude and latitude. The resulting GeoDataFrame
    is used for spatial export and downstream assignment to model regions.

    Parameters
    ----------
    co2 : pd.DataFrame
        Standardized emissions table containing longitude, latitude, emissions,
        and the ``is_spatially_assignable`` flag.

    Returns
    -------
    gpd.GeoDataFrame
        Spatially assignable facility emissions records with point geometries
        in WGS84.
    """
    co2_spatial = co2.loc[co2["is_spatially_assignable"]].copy()

    co2_spatial = gpd.GeoDataFrame(
        co2_spatial,
        geometry=gpd.points_from_xy(
            co2_spatial["longitude"],
            co2_spatial["latitude"],
        ),
        crs="EPSG:4326",
    )

    print("\nSpatial emissions dataset:")
    print(f"  Facilities:           {len(co2_spatial):,}")
    print(
        "  Total emissions:      "
        f"{co2_spatial['emissions_kt_co2e_per_year'].sum():,.2f} kt CO2e/year"
    )
    print(f"  CRS:                  {co2_spatial.crs}")

    return co2_spatial


def build_metadata(co2: pd.DataFrame, co2_spatial: gpd.GeoDataFrame) -> pd.DataFrame:
    """Build metadata for the processed emissions dataset.

    This function creates a compact metadata table describing the source
    dataset, source files, emissions units, geometry type, facility counts, and
    emissions totals. It also records the split between spatially assignable
    facilities and non-spatial facilities so downstream users can audit how much
    of the source inventory is available for geospatial assignment.

    Parameters
    ----------
    co2 : pd.DataFrame
        Standardized emissions table containing all source facility records.
    co2_spatial : gpd.GeoDataFrame
        Spatial emissions layer containing only facilities with assignable
        coordinates.

    Returns
    -------
    pd.DataFrame
        Two-column metadata table with ``field`` and ``value`` columns.
    """
    return pd.DataFrame(
        {
            "field": [
                "dataset_title",
                "provider",
                "dataset_year",
                "emissions_unit",
                "geometry_type",
                "source_formats",
                "source_csv",
                "source_geojson",
                "total_source_facilities",
                "spatial_facilities",
                "non_spatial_facilities",
                "source_emissions_kt_co2e",
                "spatial_emissions_kt_co2e",
                "non_spatial_emissions_kt_co2e",
            ],
            "value": [
                DATASET_TITLE,
                DATASET_PROVIDER,
                DATASET_YEAR,
                EMISSIONS_UNIT,
                GEOMETRY_TYPE,
                ", ".join(SOURCE_FORMATS),
                CO2_CSV_PATH.name,
                CO2_JSON_PATH.name,
                len(co2),
                len(co2_spatial),
                int((~co2["is_spatially_assignable"]).sum()),
                co2["emissions_kt_co2e_per_year"].sum(),
                co2_spatial["emissions_kt_co2e_per_year"].sum(),
                co2.loc[
                    ~co2["is_spatially_assignable"],
                    "emissions_kt_co2e_per_year",
                ].sum(),
            ],
        }
    )


def export_outputs(
    co2: pd.DataFrame,
    co2_spatial: gpd.GeoDataFrame,
    column_audit: pd.DataFrame,
    metadata: pd.DataFrame,
) -> None:
    """Export cleaned emissions tables and spatial emissions outputs.

    This function writes the standardized emissions table, column audit table,
    metadata table, and spatial emissions GeoPackage to the processed emissions
    directory. If a previous clean GeoPackage exists, it is removed before the
    new spatial layer is written.

    Parameters
    ----------
    co2 : pd.DataFrame
        Standardized emissions table containing all source facility records.
    co2_spatial : gpd.GeoDataFrame
        Spatially assignable emissions records with point geometries.
    column_audit : pd.DataFrame
        Audit table mapping original source columns to standardized names.
    metadata : pd.DataFrame
        Metadata table describing source provenance, facility counts, and
        emissions totals.

    Returns
    -------
    None
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    co2.to_csv(CO2_CLEAN_CSV, index=False)
    column_audit.to_csv(CO2_COLUMN_AUDIT_CSV, index=False)
    metadata.to_csv(CO2_METADATA_CSV, index=False)

    if CO2_CLEAN_GPKG.exists():
        CO2_CLEAN_GPKG.unlink()

    co2_spatial.to_file(
        CO2_CLEAN_GPKG,
        layer=f"{DATASET_NAME}_{DATASET_YEAR}",
        driver="GPKG",
    )

    print("\nExport complete:")
    print(f"  Clean CSV:     {CO2_CLEAN_CSV}")
    print(f"  Clean GPKG:    {CO2_CLEAN_GPKG}")
    print(f"  Metadata CSV:  {CO2_METADATA_CSV}")
    print(f"  Column audit:  {CO2_COLUMN_AUDIT_CSV}")


# =============================================================================
# Main workflow
# =============================================================================
def run_emissions_build() -> None:
    """Run the emissions preprocessing workflow."""

    print("=" * 78)
    print("Geospatial-CANOE emissions preprocessing")
    print("=" * 78)

    validate_source_files()
    co2_csv, _co2_geo = load_sources()

    column_audit = build_column_audit(co2_csv)
    co2 = standardize_emissions_table(co2_csv)

    validate_standardized_table(co2)
    co2_spatial = build_spatial_emissions(co2)
    metadata = build_metadata(co2, co2_spatial)

    export_outputs(
        co2,
        co2_spatial,
        column_audit,
        metadata,
    )

    print("\nStage complete.")


def main() -> None:
    """Run the emissions preprocessing workflow.

    This entry point validates the required source files, loads the raw
    emissions inputs, builds a column audit, standardizes the emissions table,
    validates the cleaned records, creates the spatial emissions layer, builds
    metadata, and exports all processed emissions outputs.

    Returns
    -------
    None
    """
    run_emissions_build()



if __name__ == "__main__":
    main()
