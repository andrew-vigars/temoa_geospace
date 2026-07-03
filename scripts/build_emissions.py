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

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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
# Main
# =============================================================================

def main() -> None:
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

    export_outputs(co2, co2_spatial, column_audit, metadata)

    print("\nStage complete.")


if __name__ == "__main__":
    main()
