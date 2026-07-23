
"""
build_basemaps.py

Stage 1 of the Geospatial-CANOE workflow.

Build regular basemap grids for a configurable Canadian study area using one
of two configurable coordinate systems:

1. Geographic latitude/longitude grids in EPSG:4326, with resolution in degrees.
2. Projected equal-distance grids in EPSG:3347, with resolution in kilometres.

The study area is defined by selecting provincial and territorial abbreviations
in the configuration section. The selected polygons are dissolved into one
boundary, and grid cells are retained when their centroids fall within that
boundary. This script does not build adjacency, transport connectivity, or
CANOE/TEMOA SQL tables.

Inputs
------
data_files/raw/basemaps/*.shp

Outputs
-------
data_files/processed/basemaps/
    {study_area}_boundary_wgs84.gpkg
    {study_area}_boundary_epsg3347.gpkg
    {study_area}_basemap_{resolution}deg_centroid.gpkg
    {study_area}_basemap_{resolution}km_centroid.gpkg
    basemap_summary.csv

Configuration is currently defined in this script. It can later be moved into a
project-level graph-build configuration file without changing the core build
functions.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import Point, box
from tqdm.auto import tqdm


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_BASEMAP = PROJECT_ROOT / "data_files" / "raw" / "basemaps"
PROCESSED_BASEMAP = PROJECT_ROOT / "data_files" / "processed" / "basemaps"


# =============================================================================
# Basemap configuration
# =============================================================================

WGS84_CRS = "EPSG:4326"
STAT_CANADA_LAMBERT_CRS = "EPSG:3347"

# Statistics Canada province and territory identifiers.
PROVINCE_NAME_TO_CODE = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}

ALL_PROVINCE_CODES = list(PROVINCE_NAME_TO_CODE.values())

# Select the provinces and territories that form one combined study area.
# Comment out any jurisdictions that should not be included.
#
# Examples:
#   Ontario only:          ["ON"]
#   Ontario and Quebec:    ["ON", "QC"]
#   Atlantic Canada:       ["NL", "PE", "NS", "NB"]
#   Alberta and BC:        ["AB", "BC"]
#   Full Canada:           leave all entries enabled
SELECTED_PROVINCES = [
    "NL",
    "PE",
    "NS",
    "NB",
    "QC",
    "ON",
    "MB",
    "SK",
    "AB",
    "BC",
    #"YT",
    #"NT",
    #"NU",
]

# Optional explicit filename label. Leave as None to generate one automatically:
#   all provinces -> "canada"
#   one province  -> "on"
#   several       -> "on_qc", "ab_bc", etc.
STUDY_AREA_LABEL: str | None = "province_only"

# Select which grid family or families to build.
# Valid values: "geographic", "projected".
GRID_TYPES_TO_BUILD = [
    "geographic",
    "projected",
]

# Geographic grid resolutions are expressed in decimal degrees.
GEOGRAPHIC_RESOLUTIONS_DEG = [
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
]

# Projected grid resolutions are expressed in kilometres. Internally, grid
# construction uses metres because EPSG:3347 has metre coordinate units.
PROJECTED_RESOLUTIONS_KM = [
    10,
    25,
    50,
    75,
    100,
]

KEEP_METHOD = "centroid"
COORD_PRECISION = 6

GRID_CONFIG = {
    "geographic": {
        "crs": WGS84_CRS,
        "resolution_unit": "degree",
        "filename_unit": "deg",
        "resolutions": GEOGRAPHIC_RESOLUTIONS_DEG,
        "native_scale": 1.0,
        "native_unit": "degree",
    },
    "projected": {
        "crs": STAT_CANADA_LAMBERT_CRS,
        "resolution_unit": "km",
        "filename_unit": "km",
        "resolutions": PROJECTED_RESOLUTIONS_KM,
        "native_scale": 1000.0,
        "native_unit": "m",
    },
}


# =============================================================================
# Boundary helpers
# =============================================================================


def find_boundary_shapefile(raw_basemap_dir: Path) -> Path:
    """Return the single boundary shapefile in the raw basemap directory."""

    shapefiles = sorted(raw_basemap_dir.glob("*.shp"))

    if len(shapefiles) != 1:
        raise ValueError(
            f"Expected exactly one boundary shapefile in {raw_basemap_dir}, "
            f"found {len(shapefiles)}: {[path.name for path in shapefiles]}"
        )

    return shapefiles[0]


def identify_province_name_column(
    provinces: gpd.GeoDataFrame,
) -> str:
    """Return the English province-name column from the boundary file."""

    preferred_columns = [
        "PRENAME",
        "PRNAME",
        "province_name",
    ]

    for column in preferred_columns:
        if column in provinces.columns:
            return column

    raise ValueError(
        "Could not identify the English province-name column. "
        f"Available columns: {list(provinces.columns)}"
    )


def load_province_boundaries(
    boundary_path: Path,
) -> gpd.GeoDataFrame:
    """Load provincial boundaries and add canonical two-letter codes."""

    print(f"Loading boundary file: {boundary_path.name}")

    provinces = gpd.read_file(boundary_path)

    if provinces.empty:
        raise ValueError(f"Boundary file is empty: {boundary_path}")

    if provinces.crs is None:
        raise ValueError(f"Boundary CRS is undefined: {boundary_path}")

    province_name_column = identify_province_name_column(provinces)

    provinces = provinces.copy()

    provinces["province_name"] = (
        provinces[province_name_column]
        .astype(str)
        .str.strip()
    )

    provinces["province_code"] = provinces["province_name"].map(
        PROVINCE_NAME_TO_CODE
    )

    unmapped_names = sorted(
        provinces.loc[
            provinces["province_code"].isna(),
            "province_name",
        ].unique()
    )

    if unmapped_names:
        raise ValueError(
            "Boundary file contains province names that are not mapped: "
            f"{unmapped_names}"
        )

    print("\nLoaded province and territory boundaries:")

    print(
        provinces[
            [
                "province_name",
                "province_code",
            ]
        ].to_string(index=False)
    )

    return (
        provinces[
            [
                "province_name",
                "province_code",
                "geometry",
            ]
        ]
        .to_crs(WGS84_CRS)
        .reset_index(drop=True)
    )


def validate_study_area_configuration() -> None:
    """Validate selected province and territory abbreviations."""

    if not SELECTED_PROVINCES:
        raise ValueError(
            "SELECTED_PROVINCES must contain at least one province "
            "or territory code."
        )

    normalized_codes = [
        str(code).upper().strip()
        for code in SELECTED_PROVINCES
    ]

    if len(normalized_codes) != len(set(normalized_codes)):
        raise ValueError(
            "SELECTED_PROVINCES contains duplicate codes: "
            f"{normalized_codes}"
        )

    unknown_codes = sorted(
        set(normalized_codes) - set(ALL_PROVINCE_CODES)
    )

    if unknown_codes:
        raise ValueError(
            f"Unknown province/territory codes: {unknown_codes}. "
            f"Valid codes are: {ALL_PROVINCE_CODES}"
        )


def resolve_study_area_label() -> str:
    """Return a deterministic filename-safe study-area label."""

    if STUDY_AREA_LABEL is not None:
        label = STUDY_AREA_LABEL.strip().lower().replace("-", "_").replace(" ", "_")

        if not label:
            raise ValueError(
                "STUDY_AREA_LABEL cannot be blank when explicitly supplied."
            )

        return label

    selected = {
        str(code).upper().strip()
        for code in SELECTED_PROVINCES
    }

    if selected == set(ALL_PROVINCE_CODES):
        return "canada"

    ordered_codes = [
        code.lower()
        for code in ALL_PROVINCE_CODES
        if code in selected
    ]

    return "_".join(ordered_codes)


def build_study_area_boundary(
    provinces: gpd.GeoDataFrame,
    selected_provinces: list[str],
    study_area_label: str,
) -> gpd.GeoDataFrame:
    """Select and dissolve provinces into one combined study-area boundary."""

    selected_codes = [
        str(code).upper().strip()
        for code in selected_provinces
    ]

    selected = provinces[
        provinces["province_code"].isin(selected_codes)
    ].copy()

    found_codes = set(selected["province_code"])
    missing_codes = sorted(set(selected_codes) - found_codes)

    if missing_codes:
        raise ValueError(
            "Configured provinces were not found in the boundary file: "
            f"{missing_codes}"
        )

    print(
        "Selected study-area provinces/territories: "
        + ", ".join(selected_codes)
    )
    print("Dissolving selected boundaries...")

    study_area_geometry = selected.geometry.union_all()

    return gpd.GeoDataFrame(
        {
            "study_area": [study_area_label],
            "province_codes": [",".join(selected_codes)],
            "province_count": [len(selected_codes)],
        },
        geometry=[study_area_geometry],
        crs=WGS84_CRS,
    )


# =============================================================================
# Grid helpers
# =============================================================================


def validate_grid_configuration() -> None:
    """Validate the in-script grid configuration before building outputs."""

    unknown_grid_types = sorted(set(GRID_TYPES_TO_BUILD) - set(GRID_CONFIG))

    if unknown_grid_types:
        raise ValueError(
            f"Unknown grid type(s): {unknown_grid_types}. "
            f"Valid values are: {sorted(GRID_CONFIG)}"
        )

    if not GRID_TYPES_TO_BUILD:
        raise ValueError("GRID_TYPES_TO_BUILD must contain at least one grid type.")

    for grid_type in GRID_TYPES_TO_BUILD:
        resolutions = GRID_CONFIG[grid_type]["resolutions"]

        if not resolutions:
            raise ValueError(f"No resolutions configured for {grid_type} grids.")

        if any(float(resolution) <= 0 for resolution in resolutions):
            raise ValueError(
                f"All {grid_type} grid resolutions must be positive: {resolutions}"
            )

        if len(resolutions) != len(set(resolutions)):
            raise ValueError(
                f"Duplicate {grid_type} grid resolutions found: {resolutions}"
            )


def align_bounds_to_grid(
    bounds: tuple[float, float, float, float],
    cell_size_native: float,
) -> tuple[float, float, float, float]:
    """Expand bounds outward to exact multiples of the native cell size."""

    min_x, min_y, max_x, max_y = bounds

    aligned_min_x = np.floor(min_x / cell_size_native) * cell_size_native
    aligned_min_y = np.floor(min_y / cell_size_native) * cell_size_native
    aligned_max_x = np.ceil(max_x / cell_size_native) * cell_size_native
    aligned_max_y = np.ceil(max_y / cell_size_native) * cell_size_native

    return (
        float(aligned_min_x),
        float(aligned_min_y),
        float(aligned_max_x),
        float(aligned_max_y),
    )


def build_candidate_grid(
    boundary_gdf: gpd.GeoDataFrame,
    grid_type: str,
    resolution: float,
) -> gpd.GeoDataFrame:
    """Build all candidate square cells covering the boundary extent."""

    config = GRID_CONFIG[grid_type]
    grid_crs = str(config["crs"])
    cell_size_native = float(resolution) * float(config["native_scale"])

    boundary_native = boundary_gdf.to_crs(grid_crs)
    aligned_bounds = align_bounds_to_grid(
        boundary_native.total_bounds,
        cell_size_native,
    )
    min_x, min_y, max_x, max_y = aligned_bounds

    x_values = np.arange(min_x, max_x, cell_size_native)
    y_values = np.arange(min_y, max_y, cell_size_native)
    total_cells = len(x_values) * len(y_values)

    display_symbol = "°" if grid_type == "geographic" else " km"

    print(
        f"\nBuilding {resolution:g}{display_symbol} {grid_type} grid "
        f"using {KEEP_METHOD!r} retention..."
    )
    print(f"Grid CRS: {grid_crs}")
    native_unit = str(config["native_unit"])
    if grid_type == "projected":
        print(
            f"Native cell size: {cell_size_native:,.0f} {native_unit} "
            f"({resolution:g} km)"
        )
    else:
        print(
            f"Native cell size: {cell_size_native:g} {native_unit}"
        )
    print(f"Candidate cells: {total_cells:,}")

    grid_cells: list[dict] = []

    progress = tqdm(
        total=total_cells,
        desc=(
            f"{resolution:g}{config['filename_unit']} "
            f"{KEEP_METHOD}"
        ),
        unit="cells",
    )

    for x_min in x_values:
        for y_min in y_values:
            x_max = x_min + cell_size_native
            y_max = y_min + cell_size_native
            x_center = x_min + cell_size_native / 2
            y_center = y_min + cell_size_native / 2

            grid_cells.append(
                {
                    "x_min": round(float(x_min), COORD_PRECISION),
                    "x_max": round(float(x_max), COORD_PRECISION),
                    "y_min": round(float(y_min), COORD_PRECISION),
                    "y_max": round(float(y_max), COORD_PRECISION),
                    "centroid_x": round(float(x_center), COORD_PRECISION),
                    "centroid_y": round(float(y_center), COORD_PRECISION),
                    "geometry": box(x_min, y_min, x_max, y_max),
                    "centroid_geometry": Point(x_center, y_center),
                }
            )

            progress.update(1)

    progress.close()

    return gpd.GeoDataFrame(
        grid_cells,
        geometry="geometry",
        crs=grid_crs,
    )


def retain_centroid_cells(
    candidate_grid: gpd.GeoDataFrame,
    boundary_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep cells whose native-CRS centroids fall within the study area."""

    if candidate_grid.crs is None:
        raise ValueError("Candidate grid CRS is undefined.")

    boundary_native = boundary_gdf.to_crs(candidate_grid.crs)

    centroid_gdf = gpd.GeoDataFrame(
        candidate_grid.drop(columns=["geometry", "centroid_geometry"]),
        geometry=gpd.GeoSeries(
            candidate_grid["centroid_geometry"],
            crs=candidate_grid.crs,
        ),
        crs=candidate_grid.crs,
    )

    keep_index = gpd.sjoin(
        centroid_gdf,
        boundary_native[["geometry"]],
        how="inner",
        predicate="within",
    ).index.unique()

    retained = candidate_grid.loc[keep_index].copy()
    retained = retained.drop(columns="centroid_geometry")

    return retained


def add_coordinate_metadata(
    grid: gpd.GeoDataFrame,
    boundary_gdf: gpd.GeoDataFrame,
    grid_type: str,
    resolution: float,
) -> gpd.GeoDataFrame:
    """Add canonical IDs, native coordinates, and WGS84 display coordinates."""

    config = GRID_CONFIG[grid_type]
    grid_crs = str(config["crs"])

    centroid_native = gpd.GeoDataFrame(
        grid[["centroid_x", "centroid_y"]].copy(),
        geometry=gpd.points_from_xy(
            grid["centroid_x"],
            grid["centroid_y"],
            crs=grid_crs,
        ),
        crs=grid_crs,
    )
    centroid_wgs84 = centroid_native.to_crs(WGS84_CRS)

    grid = grid.copy()
    grid["lon"] = centroid_wgs84.geometry.x.round(COORD_PRECISION)
    grid["lat"] = centroid_wgs84.geometry.y.round(COORD_PRECISION)

    # Sort in the native coordinate system so IDs remain deterministic for a
    # given grid configuration.
    grid = grid.sort_values(
        ["centroid_y", "centroid_x"],
    ).reset_index(drop=True)

    grid["region"] = [f"R{i}" for i in grid.index]
    grid["site_id"] = grid["region"]
    grid["study_area"] = str(boundary_gdf["study_area"].iloc[0])
    grid["province_codes"] = str(boundary_gdf["province_codes"].iloc[0])
    grid["grid_type"] = grid_type
    grid["grid_crs"] = grid_crs
    grid["resolution"] = float(resolution)
    grid["resolution_unit"] = str(config["resolution_unit"])
    grid["cell_size_native"] = (
        float(resolution) * float(config["native_scale"])
    )
    grid["keep_method"] = KEEP_METHOD

    # Compatibility fields ease the staged downstream refactor. New code should
    # use the generic resolution and centroid_x/centroid_y fields.
    grid["resolution_deg"] = (
        float(resolution) if grid_type == "geographic" else np.nan
    )
    grid["resolution_km"] = (
        float(resolution) if grid_type == "projected" else np.nan
    )

    ordered_columns = [
        "region",
        "site_id",
        "study_area",
        "province_codes",
        "grid_type",
        "grid_crs",
        "resolution",
        "resolution_unit",
        "resolution_deg",
        "resolution_km",
        "cell_size_native",
        "keep_method",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "centroid_x",
        "centroid_y",
        "lon",
        "lat",
        "geometry",
    ]

    return grid[ordered_columns]


def build_grid(
    boundary_gdf: gpd.GeoDataFrame,
    grid_type: str,
    resolution: float,
) -> gpd.GeoDataFrame:
    """Build one centroid-retained geographic or projected study-area grid."""

    if grid_type not in GRID_CONFIG:
        raise ValueError(
            f"Unknown grid type {grid_type!r}. Valid values: {sorted(GRID_CONFIG)}"
        )

    candidate_grid = build_candidate_grid(
        boundary_gdf=boundary_gdf,
        grid_type=grid_type,
        resolution=resolution,
    )

    study_area_label = str(boundary_gdf["study_area"].iloc[0])
    print(f"Applying {study_area_label} centroid filter...")

    retained_grid = retain_centroid_cells(
        candidate_grid=candidate_grid,
        boundary_gdf=boundary_gdf,
    )

    retained_grid = add_coordinate_metadata(
        grid=retained_grid,
        boundary_gdf=boundary_gdf,
        grid_type=grid_type,
        resolution=resolution,
    )

    print(
        f"Completed {grid_type} grid at {resolution:g} "
        f"{GRID_CONFIG[grid_type]['resolution_unit']}: "
        f"{len(retained_grid):,} retained cells."
    )

    return retained_grid


# =============================================================================
# Output helpers
# =============================================================================


def basemap_key(grid_type: str, resolution: float) -> str:
    """Return the canonical filename key for one grid configuration."""

    filename_unit = GRID_CONFIG[grid_type]["filename_unit"]
    return f"{resolution:g}{filename_unit}_{KEEP_METHOD}"


def save_basemap_preview(
    basemap: gpd.GeoDataFrame,
    study_area_boundary: gpd.GeoDataFrame,
    png_path: Path,
    grid_type: str,
    resolution: float,
) -> None:
    """Save a preview using boundary geometry reprojected to the grid CRS."""

    if basemap.crs is None:
        raise ValueError("Basemap CRS is undefined.")

    boundary_native = study_area_boundary.to_crs(basemap.crs)
    unit = GRID_CONFIG[grid_type]["resolution_unit"]
    symbol = "°" if grid_type == "geographic" else " km"

    fig, ax = plt.subplots(figsize=(8, 8))

    basemap.plot(
        ax=ax,
        facecolor="lightgray",
        edgecolor="black",
        linewidth=0.15,
    )

    boundary_native.boundary.plot(
        ax=ax,
        color="red",
        linewidth=0.6,
    )

    ax.set_title(
        f"{str(study_area_boundary['study_area'].iloc[0])} "
        f"{grid_type} basemap: {resolution:g}{symbol} {KEEP_METHOD}\n"
        f"{len(basemap):,} retained cells | {basemap.crs.to_string()}"
    )
    ax.set_axis_off()

    plt.savefig(
        png_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)


def summarize_basemap(
    basemap: gpd.GeoDataFrame,
    grid_type: str,
    resolution: float,
    gpkg_path: Path,
    png_path: Path,
) -> dict:
    """Return one summary record for an exported basemap."""

    min_x, min_y, max_x, max_y = basemap.total_bounds

    return {
        "study_area": str(basemap["study_area"].iloc[0]),
        "province_codes": str(basemap["province_codes"].iloc[0]),
        "grid_type": grid_type,
        "grid_crs": basemap.crs.to_string(),
        "resolution": resolution,
        "resolution_unit": GRID_CONFIG[grid_type]["resolution_unit"],
        "cell_size_native": float(basemap["cell_size_native"].iloc[0]),
        "keep_method": KEEP_METHOD,
        "regions": len(basemap),
        "native_x_min": min_x,
        "native_x_max": max_x,
        "native_y_min": min_y,
        "native_y_max": max_y,
        "lon_min": basemap["lon"].min(),
        "lon_max": basemap["lon"].max(),
        "lat_min": basemap["lat"].min(),
        "lat_max": basemap["lat"].max(),
        "output_file": gpkg_path.name,
        "preview_file": png_path.name,
    }


def build_all_basemaps(
    study_area_boundary: gpd.GeoDataFrame,
    study_area_label: str,
    output_dir: Path,
) -> pd.DataFrame:
    """Build and export all grid types and resolutions selected in config."""

    preview_dir = output_dir / "preview"

    if preview_dir.exists():
        shutil.rmtree(preview_dir)

    preview_dir.mkdir(parents=True)

    summary_rows: list[dict] = []

    for grid_type in GRID_TYPES_TO_BUILD:
        config = GRID_CONFIG[grid_type]

        for resolution in config["resolutions"]:
            basemap = build_grid(
                boundary_gdf=study_area_boundary,
                grid_type=grid_type,
                resolution=float(resolution),
            )

            key = basemap_key(grid_type, float(resolution))
            gpkg_path = output_dir / f"{study_area_label}_basemap_{key}.gpkg"
            png_path = preview_dir / f"{study_area_label}_basemap_{key}.png"

            basemap.to_file(
                gpkg_path,
                layer="regions",
                driver="GPKG",
            )

            save_basemap_preview(
                basemap=basemap,
                study_area_boundary=study_area_boundary,
                png_path=png_path,
                grid_type=grid_type,
                resolution=float(resolution),
            )

            print(f"Exported: {gpkg_path.name} ({len(basemap):,} regions)")
            print(f"Exported preview: {png_path.name}")

            summary_rows.append(
                summarize_basemap(
                    basemap=basemap,
                    grid_type=grid_type,
                    resolution=float(resolution),
                    gpkg_path=gpkg_path,
                    png_path=png_path,
                )
            )

    return pd.DataFrame(summary_rows)


# =============================================================================
# Main workflow
# =============================================================================


def main() -> None:
    """Run Stage 1 of the Geospatial-CANOE basemap workflow."""

    validate_study_area_configuration()
    validate_grid_configuration()
    PROCESSED_BASEMAP.mkdir(parents=True, exist_ok=True)

    study_area_label = resolve_study_area_label()
    boundary_path = find_boundary_shapefile(RAW_BASEMAP)
    provinces = load_province_boundaries(boundary_path)

    study_area_boundary_wgs84 = build_study_area_boundary(
        provinces=provinces,
        selected_provinces=SELECTED_PROVINCES,
        study_area_label=study_area_label,
    )
    study_area_boundary_epsg3347 = study_area_boundary_wgs84.to_crs(
        STAT_CANADA_LAMBERT_CRS
    )

    boundary_wgs84_output = (
        PROCESSED_BASEMAP
        / f"{study_area_label}_boundary_wgs84.gpkg"
    )
    boundary_epsg3347_output = (
        PROCESSED_BASEMAP
        / f"{study_area_label}_boundary_epsg3347.gpkg"
    )

    study_area_boundary_wgs84.to_file(
        boundary_wgs84_output,
        layer="study_area_boundary",
        driver="GPKG",
    )
    study_area_boundary_epsg3347.to_file(
        boundary_epsg3347_output,
        layer="study_area_boundary",
        driver="GPKG",
    )

    print(f"Study-area label: {study_area_label}")
    print(f"Exported: {boundary_wgs84_output.name}")
    print(f"Exported: {boundary_epsg3347_output.name}")

    basemap_summary = build_all_basemaps(
        study_area_boundary=study_area_boundary_wgs84,
        study_area_label=study_area_label,
        output_dir=PROCESSED_BASEMAP,
    )

    summary_path = PROCESSED_BASEMAP / "basemap_summary.csv"
    basemap_summary.to_csv(summary_path, index=False)

    print("\nStage 1 complete.")
    print(f"Exported summary: {summary_path}")


if __name__ == "__main__":
    main()
