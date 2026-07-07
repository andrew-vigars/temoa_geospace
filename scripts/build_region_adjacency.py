
"""
build_region_adjacency.py

Stage 2 of the Geospatial-CANOE workflow.

Build rook-adjacency topology from the Stage 1 Canada basemap grids.
Currently 4-rook movement is defined but not other k-neighbor movements
This script does not build road, rail, marine, pipeline, or CANOE SQL logic.
It only maps which model regions are cardinal neighbours.

Inputs:
    data_files/processed/basemaps/
        canada_basemap_{resolution}deg_{keep_method}.gpkg

Outputs:
    data_files/processed/adjacency/
        canada_basemap_{resolution}deg_{keep_method}_graph_nodes.gpkg
        canada_basemap_{resolution}deg_{keep_method}_graph_nodes.csv
        canada_basemap_{resolution}deg_{keep_method}_graph_edges.csv
        region_adjacency_graph_summary.csv

    data_files/processed/adjacency/preview/
        canada_basemap_{resolution}deg_{keep_method}_graph_preview.png
"""

from pathlib import Path
import shutil

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from geopy.distance import distance
from shapely.geometry import LineString


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROCESSED_BASEMAP = (
    PROJECT_ROOT
    / "data_files"
    / "processed"
    / "basemaps"
)

PROCESSED_GRAPH = (
    PROJECT_ROOT
    / "data_files"
    / "processed"
    / "graph"
)


# =============================================================================
# Settings
# =============================================================================

COORD_PRECISION = 6

NO_NEIGHBOR = "R-999"

NEIGHBOR_MAP = {
    "up_id": "up",
    "down_id": "down",
    "right_id": "right",
    "left_id": "left",
}


# =============================================================================
# Helpers
# =============================================================================

def find_basemap_files(basemap_dir: Path) -> list[Path]:
    """Find Stage 1 basemap grid GeoPackages for adjacency building.

    This function searches the processed basemap directory for generated Canada
    basemap grids and excludes the dissolved national boundary file. The
    returned files are the region-grid inputs used to build Stage 2 rook
    adjacency graphs.

    Parameters
    ----------
    basemap_dir : Path
        Directory containing processed Stage 1 basemap GeoPackages.

    Returns
    -------
    list[Path]
        Sorted list of basemap grid GeoPackage paths.

    Raises
    ------
    FileNotFoundError
        If no Stage 1 basemap grid GeoPackages are found.
    """

    basemap_files = sorted(
        basemap_dir.glob("canada_basemap_*deg_*.gpkg")
    )

    basemap_files = [
        path for path in basemap_files
        if path.name != "canada_boundary_wgs84.gpkg"
    ]

    if not basemap_files:
        raise FileNotFoundError(
            f"No basemap files found in {basemap_dir}"
        )

    print(f"Found {len(basemap_files):,} basemap files.")

    for path in basemap_files:
        print(f"  - {path.name}")

    return basemap_files


def validate_regions(
    regions: gpd.GeoDataFrame,
    basemap_path: Path,
) -> None:
    """Validate that a basemap is suitable for adjacency construction.

    This function checks that a Stage 1 basemap contains the required columns
    and structural invariants needed to build a rook-adjacency graph. Each
    region and site ID must be unique, each cell must have a unique centroid,
    and the file must represent a single grid resolution and retention method.

    Parameters
    ----------
    regions : gpd.GeoDataFrame
        Basemap regions loaded from a Stage 1 GeoPackage.
    basemap_path : Path
        Source basemap path, used for informative error messages.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If required columns are missing, region or site IDs are duplicated,
        centroids are duplicated, or the file contains mixed resolutions or
        mixed retention methods.
    """

    required_columns = [
        "region",
        "site_id",
        "lon",
        "lat",
        "resolution_deg",
        "keep_method",
        "geometry",
    ]

    missing_columns = [
        col for col in required_columns
        if col not in regions.columns
    ]

    if missing_columns:
        raise ValueError(
            f"{basemap_path.name} missing columns: {missing_columns}"
        )

    if not regions["region"].is_unique:
        raise ValueError(f"{basemap_path.name} has duplicate region IDs.")

    if not regions["site_id"].is_unique:
        raise ValueError(f"{basemap_path.name} has duplicate site IDs.")

    duplicated_coords = (
        regions[["lon", "lat"]]
        .drop_duplicates()
        .shape[0]
        != len(regions)
    )

    if duplicated_coords:
        raise ValueError(
            f"{basemap_path.name} has duplicate lon/lat centroids."
        )

    if len(regions["resolution_deg"].unique()) != 1:
        raise ValueError(
            f"{basemap_path.name} contains multiple resolutions."
        )

    if len(regions["keep_method"].unique()) != 1:
        raise ValueError(
            f"{basemap_path.name} contains multiple keep methods."
        )


def build_region_adjacency(
    basemap_path: Path,
    coord_precision: int = COORD_PRECISION,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Build rook-adjacency graph nodes and directed edges for one basemap.

    This function loads a Stage 1 basemap grid, validates the required region
    fields, and identifies cardinal neighbours using centroid coordinates and
    the grid resolution. For each region, it records the neighbouring region ID
    above, below, left, and right when such a neighbour exists. Missing
    neighbours are assigned the ``NO_NEIGHBOR`` sentinel value.

    The function also computes the number of neighbours per region, estimates
    centroid-to-centroid geodesic distances for each valid neighbour direction,
    and converts the neighbour relationships into a directed adjacency edge
    table.

    Parameters
    ----------
    basemap_path : Path
        Path to a Stage 1 basemap GeoPackage containing region grid cells.
    coord_precision : int, default COORD_PRECISION
        Decimal precision used when rounding longitude and latitude centroids
        for coordinate-based neighbour lookup.

    Returns
    -------
    tuple[gpd.GeoDataFrame, pd.DataFrame]
        A GeoDataFrame of graph nodes with neighbour IDs, neighbour counts, and
        directional distances, plus a DataFrame of directed adjacency edges.

    Raises
    ------
    ValueError
        If the loaded basemap fails structural validation in
        ``validate_regions``.
    """

    print("\n" + "=" * 80)
    print(f"Processing basemap: {basemap_path.name}")
    print("=" * 80)

    regions = gpd.read_file(basemap_path)

    print(f"Loaded regions: {len(regions):,}")
    print(f"CRS: {regions.crs}")

    validate_regions(
        regions=regions,
        basemap_path=basemap_path,
    )

    resolution = float(regions["resolution_deg"].iloc[0])
    keep_method = str(regions["keep_method"].iloc[0])

    print(f"Resolution: {resolution:g}°")
    print(f"Keep method: {keep_method}")

    regions["lon_key"] = regions["lon"].round(coord_precision)
    regions["lat_key"] = regions["lat"].round(coord_precision)

    coord_to_region = {
        (row.lon_key, row.lat_key): row.region
        for row in regions.itertuples(index=False)
    }

    regions["up_id"] = [
        coord_to_region.get(
            (
                round(row.lon_key, coord_precision),
                round(row.lat_key + resolution, coord_precision),
            ),
            NO_NEIGHBOR,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["down_id"] = [
        coord_to_region.get(
            (
                round(row.lon_key, coord_precision),
                round(row.lat_key - resolution, coord_precision),
            ),
            NO_NEIGHBOR,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["right_id"] = [
        coord_to_region.get(
            (
                round(row.lon_key + resolution, coord_precision),
                round(row.lat_key, coord_precision),
            ),
            NO_NEIGHBOR,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["left_id"] = [
        coord_to_region.get(
            (
                round(row.lon_key - resolution, coord_precision),
                round(row.lat_key, coord_precision),
            ),
            NO_NEIGHBOR,
        )
        for row in regions.itertuples(index=False)
    ]

    neighbor_cols = list(NEIGHBOR_MAP.keys())

    regions["n_neighbors"] = sum(
        (regions[col] != NO_NEIGHBOR).astype(int)
        for col in neighbor_cols
    )

    print("Neighbour count distribution:")
    print(regions["n_neighbors"].value_counts().sort_index())

    print(f"Average neighbours: {regions['n_neighbors'].mean():.2f}")
    print(f"Isolated regions: {(regions['n_neighbors'] == 0).sum():,}")

    region_lookup = (
        regions
        .set_index("region")[["lat", "lon"]]
        .to_dict("index")
    )

    for direction in ["up", "down", "right", "left"]:
        distance_col = f"{direction}_distance"
        neighbor_col = f"{direction}_id"

        regions[distance_col] = np.nan

        for row in regions.itertuples():
            neighbor = getattr(row, neighbor_col)

            if neighbor != NO_NEIGHBOR:
                coord1 = (row.lat, row.lon)

                coord2 = (
                    region_lookup[neighbor]["lat"],
                    region_lookup[neighbor]["lon"],
                )

                regions.at[row.Index, distance_col] = distance(
                    coord1,
                    coord2,
                ).km

    edge_rows = []

    for row in regions.itertuples(index=False):
        for neighbor_col, direction in NEIGHBOR_MAP.items():
            region_to = getattr(row, neighbor_col)

            if region_to != NO_NEIGHBOR:
                distance_col = f"{direction}_distance"

                edge_rows.append(
                    {
                        "edge_region": f"{row.region}-{region_to}",
                        "region_from": row.region,
                        "region_to": region_to,
                        "direction": direction,
                        "lon_from": row.lon,
                        "lat_from": row.lat,
                        "lon_to": region_lookup[region_to]["lon"],
                        "lat_to": region_lookup[region_to]["lat"],
                        "distance_km": getattr(row, distance_col),
                        "resolution_deg": row.resolution_deg,
                        "keep_method": row.keep_method,
                    }
                )

    adjacency_edges = pd.DataFrame(edge_rows)

    print(f"Directed adjacency edges: {len(adjacency_edges):,}")

    regions = regions.drop(
        columns=["lon_key", "lat_key"],
        errors="ignore",
    )

    return regions, adjacency_edges


def build_edge_geometries(
    adjacency_edges: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Convert adjacency edges into centroid-to-centroid line geometries.

    This function takes the directed adjacency edge table produced by
    ``build_region_adjacency`` and creates a WGS84 line geometry for each edge
    using the source and destination region centroids. The resulting
    GeoDataFrame is used for spatial export and graph preview plotting.

    Parameters
    ----------
    adjacency_edges : pd.DataFrame
        Directed adjacency edge table containing source and destination
        centroid coordinates.

    Returns
    -------
    gpd.GeoDataFrame
        Directed adjacency edges with ``LineString`` geometries in WGS84. If
        the input edge table is empty, an empty GeoDataFrame is returned.
    """

    if adjacency_edges.empty:
        return gpd.GeoDataFrame(
            adjacency_edges,
            geometry=[],
            crs="EPSG:4326",
        )

    edges = adjacency_edges.copy()

    edges["geometry"] = edges.apply(
        lambda row: LineString(
            [
                (row["lon_from"], row["lat_from"]),
                (row["lon_to"], row["lat_to"]),
            ]
        ),
        axis=1,
    )

    return gpd.GeoDataFrame(
        edges,
        geometry="geometry",
        crs="EPSG:4326",
    )


def save_adjacency_preview(
    regions_graph: gpd.GeoDataFrame,
    adjacency_edges: pd.DataFrame,
    png_path: Path,
) -> None:
    """Save a PNG preview of a region adjacency graph.

    The preview plots the basemap regions, centroid-to-centroid adjacency
    edges, and any isolated regions with no rook neighbours. This provides a
    visual check that the Stage 2 graph topology is consistent with the
    underlying basemap resolution and retention method.

    Parameters
    ----------
    regions_graph : gpd.GeoDataFrame
        Graph node layer containing basemap regions, neighbour IDs, and
        neighbour counts.
    adjacency_edges : pd.DataFrame
        Directed adjacency edge table produced by ``build_region_adjacency``.
    png_path : Path
        Output path for the saved PNG preview.

    Returns
    -------
    None
    """

    edge_gdf = build_edge_geometries(adjacency_edges)

    isolated_regions = regions_graph.loc[
        regions_graph["n_neighbors"] == 0
    ]

    resolution = regions_graph["resolution_deg"].iloc[0]
    keep_method = regions_graph["keep_method"].iloc[0]

    fig, ax = plt.subplots(figsize=(9, 9))

    regions_graph.plot(
        ax=ax,
        facecolor="none",
        edgecolor="lightgray",
        linewidth=0.2,
    )

    if not edge_gdf.empty:
        edge_gdf.plot(
            ax=ax,
            color="black",
            linewidth=0.25,
            alpha=0.35,
        )

    if not isolated_regions.empty:
        isolated_regions.plot(
            ax=ax,
            color="red",
        )

    ax.set_title(
        f"Region adjacency graph: {resolution:g}° {keep_method}\n"
        f"{len(regions_graph):,} nodes | "
        f"{len(adjacency_edges):,} directed edges | "
        f"{len(isolated_regions):,} isolated"
    )

    ax.set_axis_off()

    plt.savefig(
        png_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)


def build_all_adjacency_graphs(
    basemap_files: list[Path],
    output_dir: Path,
) -> pd.DataFrame:
    """Build and export adjacency graph products for all basemap variants.

    For each Stage 1 basemap file, this function builds the corresponding
    rook-adjacency graph, exports graph nodes and directed edges to tabular and
    spatial formats, saves a PNG preview, and records summary diagnostics. The
    preview directory is cleared before new previews are written.

    Parameters
    ----------
    basemap_files : list[Path]
        Stage 1 basemap GeoPackages to convert into adjacency graphs.
    output_dir : Path
        Directory where graph nodes, graph edges, previews, and summary outputs
        are written.

    Returns
    -------
    pd.DataFrame
        Summary table with one row per basemap graph, including source basemap,
        resolution, retention method, node count, directed edge count, average
        neighbour count, isolated-node count, and output filenames.
    """

    preview_dir = output_dir / "preview"

    if preview_dir.exists():
        shutil.rmtree(preview_dir)

    preview_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []

    for basemap_path in basemap_files:
        regions_graph, adjacency_edges = build_region_adjacency(
            basemap_path=basemap_path,
        )

        output_prefix = basemap_path.stem

        node_gpkg_path = output_dir / f"{output_prefix}_graph_nodes.gpkg"
        node_csv_path = output_dir / f"{output_prefix}_graph_nodes.csv"
        edge_csv_path = output_dir / f"{output_prefix}_graph_edges.csv"
        edge_gpkg_path = output_dir / f"{output_prefix}_graph_edges.gpkg"
        preview_path = preview_dir / f"{output_prefix}_graph_preview.png"

        regions_graph.to_file(
            node_gpkg_path,
            layer="nodes",
            driver="GPKG",
        )

        regions_graph.drop(columns="geometry").to_csv(
            node_csv_path,
            index=False,
        )

        adjacency_edges.to_csv(
            edge_csv_path,
            index=False,
        )

        edge_gdf = build_edge_geometries(adjacency_edges)

        edge_gdf.to_file(
            edge_gpkg_path,
            layer="edges",
            driver="GPKG",
        )

        save_adjacency_preview(
            regions_graph=regions_graph,
            adjacency_edges=adjacency_edges,
            png_path=preview_path,
        )

        summary_rows.append(
            {
                "basemap_file": basemap_path.name,
                "resolution_deg": regions_graph["resolution_deg"].iloc[0],
                "keep_method": regions_graph["keep_method"].iloc[0],
                "nodes": len(regions_graph),
                "directed_edges": len(adjacency_edges),
                "average_neighbors": regions_graph["n_neighbors"].mean(),
                "isolated_nodes": int(
                    (regions_graph["n_neighbors"] == 0).sum()
                ),
                "node_gpkg": node_gpkg_path.name,
                "node_csv": node_csv_path.name,
                "edge_csv": edge_csv_path.name,
                "edge_gpkg": edge_gpkg_path.name,
                "preview_file": preview_path.name,
            }
        )

        print(f"Exported: {node_gpkg_path.name}")
        print(f"Exported: {node_csv_path.name}")
        print(f"Exported: {edge_csv_path.name}")
        print(f"Exported: {edge_gpkg_path.name}")
        print(f"Exported preview: {preview_path.name}")

    return pd.DataFrame(summary_rows)


def main() -> None:
    """Run Stage 2 of the Geospatial-CANOE adjacency workflow.

    This entry point prepares the processed graph output directory, finds the
    Stage 1 basemap grid files, builds rook-adjacency graph products for each
    basemap variant, and writes a summary CSV describing the generated graph
    outputs.

    Returns
    -------
    None
    """
    PROCESSED_GRAPH.mkdir(parents=True, exist_ok=True)

    basemap_files = find_basemap_files(PROCESSED_BASEMAP)

    graph_summary = build_all_adjacency_graphs(
        basemap_files=basemap_files,
        output_dir=PROCESSED_GRAPH,
    )

    summary_path = PROCESSED_GRAPH / "region_adjacency_graph_summary.csv"

    graph_summary.to_csv(
        summary_path,
        index=False,
    )

    print("\nStage 2 complete.")
    print(f"Exported summary: {summary_path}")


if __name__ == "__main__":
    main()