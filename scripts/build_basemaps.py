
"""
build_basemaps.py

Stage 1 of the Geospatial-CANOE workflow.

Build Canada-wide latitude/longitude basemap grids for spatial aggregation.
Current grid approximations rely on centroid or intersection methods to retain cells within the Canada boundary.
Resolutions below 1° take longer to build and may require more memory.
Centroid based retention is faster for higher resolutions, but may exclude some cells that intersect the Canada boundary.
Centroid based retention can miss near-border cells but provides better island logic for arctic islands and Hudson Bay.
Intersection based retention retains all cells that intersect the Canada boundary.
Intersection based retention generally over estimates the number of cells and causes arctic islands to connect.
This script does not build adjacency, transport connectivity, or CANOE SQL tables.

Inputs:
    data_files/raw/basemaps/*.shp

Outputs:
    data_files/processed/basemaps/
        canada_boundary_wgs84.gpkg
        canada_basemap_{resolution}deg_{keep_method}.gpkg
        canada_basemap_summary.csv
"""

from pathlib import Path
import shutil

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import Point, box
from tqdm.auto import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_BASEMAP = PROJECT_ROOT / "data_files" / "raw" / "basemaps"
PROCESSED_BASEMAP = PROJECT_ROOT / "data_files" / "processed" / "basemaps"

WGS84_CRS = "EPSG:4326"

BASEMAP_RESOLUTIONS = [
    0.5,
    1.0,
    2.0,
    3.0,
    4.0,
    5.0,
]

KEEP_METHODS = [
    "centroid",
    "intersects",
]


def find_boundary_shapefile(raw_basemap_dir: Path) -> Path:
    """Return the single boundary shapefile in the raw basemap directory.

    The basemap build assumes that the raw boundary input directory contains
    exactly one ``.shp`` file. This guard prevents accidentally building the
    national boundary from the wrong file or from an ambiguous set of boundary
    files.

    Parameters
    ----------
    raw_basemap_dir : Path
        Directory containing the raw Canada boundary shapefile.

    Returns
    -------
    Path
        Path to the only shapefile found in ``raw_basemap_dir``.

    Raises
    ------
    ValueError
        If the directory contains zero shapefiles or more than one shapefile.
    """
    shapefiles = sorted(raw_basemap_dir.glob("*.shp"))

    if len(shapefiles) != 1:
        raise ValueError(
            f"Expected exactly one boundary shapefile in {raw_basemap_dir}, "
            f"found {len(shapefiles)}: {[path.name for path in shapefiles]}"
        )

    return shapefiles[0]


def build_canada_boundary(boundary_path: Path) -> gpd.GeoDataFrame:
    """Build a dissolved Canada boundary from a raw province/territory file.

    The input boundary file is expected to contain multiple provincial and/or
    territorial geometries. This function loads those geometries, projects them
    to the workflow CRS, and dissolves them into a single national geometry used
    for later basemap grid filtering.

    Parameters
    ----------
    boundary_path : Path
        Path to the raw boundary shapefile.

    Returns
    -------
    gpd.GeoDataFrame
        One-row GeoDataFrame containing the dissolved Canada geometry in WGS84.

    Raises
    ------
    ValueError
        If the boundary file contains no records.
    """
    print(f"Loading boundary file: {boundary_path.name}")

    provinces = gpd.read_file(boundary_path)

    if provinces.empty:
        raise ValueError(f"Boundary file is empty: {boundary_path}")

    provinces_wgs84 = provinces.to_crs(WGS84_CRS)

    print("Dissolving province/territory boundaries...")

    canada_geometry = provinces_wgs84.geometry.union_all()

    return gpd.GeoDataFrame(
        {"name": ["Canada"]},
        geometry=[canada_geometry],
        crs=WGS84_CRS,
    )


def build_latlon_grid(
    boundary_geometry,
    boundary_gdf: gpd.GeoDataFrame,
    resolution_deg: float,
    keep_method: str,
) -> gpd.GeoDataFrame:
    """Build a latitude/longitude grid clipped to the Canada boundary.

    Candidate grid cells are first generated across the bounding box of the
    dissolved Canada geometry. The cells are then filtered using one of two
    retention methods:

    - ``centroid`` keeps cells whose centroid falls within the Canada boundary.
    - ``intersects`` keeps cells whose polygon intersects the Canada boundary.

    The centroid method is more conservative and can exclude near-border cells.
    The intersects method is more inclusive and can retain partial boundary
    cells. The retained cells are sorted spatially and assigned deterministic
    ``region`` and ``site_id`` values for downstream CANOE/TEMOA encoding.

    Parameters
    ----------
    boundary_geometry
        Dissolved Canada boundary geometry used to define the candidate grid
        extent.
    boundary_gdf : gpd.GeoDataFrame
        One-row GeoDataFrame containing the Canada boundary used for spatial
        filtering.
    resolution_deg : float
        Grid-cell size in decimal degrees.
    keep_method : str
        Boundary retention method. Must be either ``"centroid"`` or
        ``"intersects"``.

    Returns
    -------
    gpd.GeoDataFrame
        Retained basemap grid cells with region IDs, site IDs, resolution
        metadata, retention-method metadata, and polygon geometries in WGS84.

    Raises
    ------
    ValueError
        If ``keep_method`` is not ``"centroid"`` or ``"intersects"``.
    """

    if keep_method not in {"centroid", "intersects"}:
        raise ValueError("keep_method must be 'centroid' or 'intersects'.")

    min_lon, min_lat, max_lon, max_lat = boundary_geometry.bounds

    lon_values = np.arange(np.floor(min_lon), np.ceil(max_lon), resolution_deg)
    lat_values = np.arange(np.floor(min_lat), np.ceil(max_lat), resolution_deg)

    total_cells = len(lon_values) * len(lat_values)

    print(f"\nBuilding {resolution_deg:g}° grid using {keep_method!r} retention...")
    print(f"Candidate cells: {total_cells:,}")

    grid_cells = []

    progress = tqdm(
        total=total_cells,
        desc=f"{resolution_deg:g}deg {keep_method}",
        unit="cells",
    )

    for lon_min in lon_values:
        for lat_min in lat_values:
            lon_center = round(lon_min + resolution_deg / 2, 6)
            lat_center = round(lat_min + resolution_deg / 2, 6)

            grid_cells.append(
                {
                    "lon_min": round(lon_min, 6),
                    "lon_max": round(lon_min + resolution_deg, 6),
                    "lat_min": round(lat_min, 6),
                    "lat_max": round(lat_min + resolution_deg, 6),
                    "lon": lon_center,
                    "lat": lat_center,
                    "geometry": box(
                        lon_min,
                        lat_min,
                        lon_min + resolution_deg,
                        lat_min + resolution_deg,
                    ),
                    "centroid_geometry": Point(lon_center, lat_center),
                }
            )

            progress.update(1)

    progress.close()

    grid = gpd.GeoDataFrame(
        grid_cells,
        geometry="geometry",
        crs=WGS84_CRS,
    )

    print("Applying Canada boundary filter...")

    if keep_method == "centroid":
        centroid_gdf = gpd.GeoDataFrame(
            grid.drop(columns="geometry"),
            geometry=gpd.GeoSeries(grid["centroid_geometry"], crs=WGS84_CRS),
            crs=WGS84_CRS,
        )

        keep_index = gpd.sjoin(
            centroid_gdf,
            boundary_gdf,
            how="inner",
            predicate="within",
        ).index.unique()

    else:
        keep_index = gpd.sjoin(
            grid,
            boundary_gdf,
            how="inner",
            predicate="intersects",
        ).index.unique()

    grid = grid.loc[keep_index].copy()
    grid = grid.drop(columns="centroid_geometry")

    grid = grid.sort_values(["lat", "lon"]).reset_index(drop=True)

    grid["region"] = [f"R{i}" for i in grid.index]
    grid["site_id"] = grid["region"]
    grid["resolution_deg"] = resolution_deg
    grid["keep_method"] = keep_method

    print(
        f"Completed {resolution_deg:g}° {keep_method}: "
        f"{len(grid):,} retained cells."
    )

    return grid


def save_basemap_preview(
    basemap: gpd.GeoDataFrame,
    canada_boundary: gpd.GeoDataFrame,
    png_path: Path,
    resolution_deg: float,
    keep_method: str,
) -> None:
    """Save a PNG preview of a generated Canada basemap.

    The preview overlays the retained grid cells with the dissolved Canada
    boundary so the spatial coverage of a given resolution and retention method
    can be checked visually.

    Parameters
    ----------
    basemap : gpd.GeoDataFrame
        Retained basemap grid cells to plot.
    canada_boundary : gpd.GeoDataFrame
        Dissolved Canada boundary used as the visual reference outline.
    png_path : Path
        Output path for the saved PNG preview.
    resolution_deg : float
        Grid-cell size in decimal degrees, used in the plot title.
    keep_method : str
        Boundary retention method, used in the plot title.

    Returns
    -------
    None
    """
    fig, ax = plt.subplots(figsize=(8, 8))

    basemap.plot(
        ax=ax,
        facecolor="lightgray",
        edgecolor="black",
        linewidth=0.15,
    )

    canada_boundary.boundary.plot(
        ax=ax,
        color="red",
        linewidth=0.6,
    )

    ax.set_title(
        f"Canada basemap: {resolution_deg:g}° {keep_method}\n"
        f"{len(basemap):,} retained cells"
    )

    ax.set_axis_off()

    plt.savefig(
        png_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)


def build_all_basemaps(
    canada_boundary: gpd.GeoDataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    """Build and export all configured Canada basemap variants.

    For each configured grid resolution and boundary retention method, this
    function builds a basemap grid, exports the retained cells to a GeoPackage,
    saves a PNG preview, and records summary metadata. Existing preview files
    are cleared before new previews are generated.

    Parameters
    ----------
    canada_boundary : gpd.GeoDataFrame
        One-row GeoDataFrame containing the dissolved Canada boundary in WGS84.
    output_dir : Path
        Directory where basemap GeoPackages, previews, and summary metadata are
        written.

    Returns
    -------
    pd.DataFrame
        Summary table with one row per basemap variant, including resolution,
        retention method, number of retained regions, spatial extent, output
        filename, and preview filename.
    """
    preview_dir = output_dir / "preview"

    if preview_dir.exists():
        shutil.rmtree(preview_dir)

    preview_dir.mkdir(parents=True)

    canada_geometry = canada_boundary.geometry.iloc[0]
    summary_rows = []

    for resolution_deg in BASEMAP_RESOLUTIONS:
        for keep_method in KEEP_METHODS:
            basemap = build_latlon_grid(
                boundary_geometry=canada_geometry,
                boundary_gdf=canada_boundary,
                resolution_deg=resolution_deg,
                keep_method=keep_method,
            )

            key = f"{resolution_deg:g}deg_{keep_method}"

            gpkg_path = output_dir / f"canada_basemap_{key}.gpkg"
            png_path = preview_dir / f"canada_basemap_{key}.png"

            basemap.to_file(
                gpkg_path,
                layer="regions",
                driver="GPKG",
            )

            save_basemap_preview(
                basemap=basemap,
                canada_boundary=canada_boundary,
                png_path=png_path,
                resolution_deg=resolution_deg,
                keep_method=keep_method,
            )

            print(f"Exported: {gpkg_path.name} ({len(basemap):,} regions)")
            print(f"Exported preview: {png_path.name}")

            summary_rows.append(
                {
                    "resolution_deg": resolution_deg,
                    "keep_method": keep_method,
                    "regions": len(basemap),
                    "lon_min": basemap["lon_min"].min(),
                    "lon_max": basemap["lon_max"].max(),
                    "lat_min": basemap["lat_min"].min(),
                    "lat_max": basemap["lat_max"].max(),
                    "output_file": gpkg_path.name,
                    "preview_file": png_path.name,
                }
            )

    return pd.DataFrame(summary_rows)


def main() -> None:
    """Run Stage 1 of the Geospatial-CANOE basemap workflow.

    This entry point prepares the processed basemap directory, loads and
    dissolves the raw Canada boundary shapefile, exports the WGS84 national
    boundary, builds all configured basemap variants, and writes a summary CSV
    describing the generated outputs.

    Returns
    -------
    None
    """
    PROCESSED_BASEMAP.mkdir(parents=True, exist_ok=True)

    boundary_path = find_boundary_shapefile(RAW_BASEMAP)
    canada_boundary = build_canada_boundary(boundary_path)

    boundary_output = PROCESSED_BASEMAP / "canada_boundary_wgs84.gpkg"

    canada_boundary.to_file(
        boundary_output,
        layer="canada_boundary",
        driver="GPKG",
    )

    print(f"Exported: {boundary_output.name}")

    basemap_summary = build_all_basemaps(
        canada_boundary=canada_boundary,
        output_dir=PROCESSED_BASEMAP,
    )

    summary_path = PROCESSED_BASEMAP / "canada_basemap_summary.csv"

    basemap_summary.to_csv(summary_path, index=False)

    print("\nStage 1 complete.")
    print(f"Exported summary: {summary_path}")


if __name__ == "__main__":
    main()