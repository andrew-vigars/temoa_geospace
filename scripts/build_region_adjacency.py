"""
build_region_adjacency.py

Stage 2 of the Geospatial-CANOE workflow.

Build configured rook-adjacency topology from Stage 1 basemap grids.
Supports geographic EPSG:4326 and projected EPSG:3347 inputs, including
custom provincial and multi-province study areas. Four-rook movement is
currently defined but not other k-neighbour movements.
This script does not build road, rail, marine, pipeline, or CANOE SQL logic.
It only maps which model regions are cardinal neighbours.

Inputs:
    data_files/processed/basemaps/
        {study_area}_basemap_{resolution}{deg|km}_{keep_method}.gpkg

Outputs:
    data_files/processed/graph/
        {basemap_stem}_graph_nodes.gpkg
        {basemap_stem}_graph_nodes.csv
        {basemap_stem}_graph_edges.gpkg
        {basemap_stem}_graph_edges.csv
        {study_area}_region_adjacency_graph_summary.csv

    data_files/processed/graph/preview/
        {basemap_stem}_graph_preview.png
"""

import argparse
from pathlib import Path
import shutil

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from geopy.distance import distance
from shapely.geometry import LineString

from project_config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)


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
# Adjacency implementation constants
# =============================================================================

NEIGHBOR_MAP = {
    "up_id": "up",
    "down_id": "down",
    "right_id": "right",
    "left_id": "left",
}


# =============================================================================
# Helpers
# =============================================================================

def find_basemap_files(
    basemap_dir: Path,
    config: GeospatialBuildConfig,
) -> list[Path]:
    """Discover and validate Stage 1 basemaps for one build profile.

    The function searches ``basemap_dir`` for GeoPackage files whose names match
    the configured study-area label and basemap retention method. Study-area
    boundary products are excluded. Each discovered basemap is then opened
    briefly to validate the profile metadata embedded in its first row.

    Basemaps are retained only when their ``grid_type`` and ``keep_method``
    metadata match the active build profile. A study-area mismatch or missing
    metadata is treated as an invalid upstream product rather than silently
    skipped.

    Parameters
    ----------
    basemap_dir : Path
        Directory containing processed Stage 1 basemap GeoPackages.
    config : GeospatialBuildConfig
        Shared geospatial build profile defining the expected study-area label,
        grid families, and basemap retention method.

    Returns
    -------
    list[Path]
        Sorted paths to basemap GeoPackages that match the active build profile.

    Raises
    ------
    FileNotFoundError
        If no files match the expected filename pattern, or if files are
        discovered but none match the configured grid types and retention
        method.
    ValueError
        If a discovered basemap lacks required profile metadata or its
        ``study_area`` value does not match the configured study area.
    """

    pattern = (
        f"{config.study_area.label}_basemap_*_"
        f"{config.basemaps.keep_method}.gpkg"
    )

    discovered = sorted(
        path
        for path in basemap_dir.glob(pattern)
        if "_boundary_" not in path.name
    )

    if not discovered:
        raise FileNotFoundError(
            "No Stage 1 basemaps were found for build profile "
            f"{config.study_area.label!r} in {basemap_dir}. "
            f"Expected pattern: {pattern}"
        )

    expected_grid_types = set(config.basemaps.grid_types)
    validated_paths: list[Path] = []

    for path in discovered:
        metadata = gpd.read_file(
            path,
            rows=1,
        )

        required_metadata = {
            "study_area",
            "province_codes",
            "grid_type",
            "resolution",
            "resolution_unit",
            "keep_method",
        }
        missing = required_metadata - set(metadata.columns)

        if missing:
            raise ValueError(
                f"{path.name} is missing profile metadata columns: "
                f"{sorted(missing)}"
            )

        study_area = str(metadata["study_area"].iloc[0])
        grid_type = str(metadata["grid_type"].iloc[0])
        keep_method = str(metadata["keep_method"].iloc[0])

        if study_area != config.study_area.label:
            raise ValueError(
                f"{path.name} has study_area={study_area!r}, expected "
                f"{config.study_area.label!r}."
            )

        if grid_type not in expected_grid_types:
            continue

        if keep_method != config.basemaps.keep_method:
            continue

        validated_paths.append(path)

    if not validated_paths:
        raise FileNotFoundError(
            "Basemap files were discovered, but none matched the configured "
            "grid types and retention method."
        )

    print(
        f"Found {len(validated_paths):,} basemap files for profile "
        f"{config.study_area.label!r}:"
    )

    for path in validated_paths:
        print(f"  - {path.name}")

    return validated_paths


def validate_regions(
    regions: gpd.GeoDataFrame,
    basemap_path: Path,
) -> None:
    """Validate that a Stage 1 basemap is suitable for adjacency construction.

    The function verifies that the basemap contains the required region, centroid,
    grid, profile-metadata, and geometry columns. It also checks that the dataset is
    non-empty, has a defined coordinate reference system, and contains unique region
    IDs, site IDs, and native centroid coordinates.

    Metadata fields that describe the basemap as a whole must each contain exactly
    one value. The grid type must be either ``"geographic"`` or ``"projected"``, and
    the CRS recorded in the ``grid_crs`` metadata column must match the
    GeoDataFrame CRS.

    Parameters
    ----------
    regions : gpd.GeoDataFrame
        Stage 1 basemap regions to validate before constructing adjacency.
    basemap_path : Path
        Path to the source basemap GeoPackage, used in validation error messages.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If required columns are missing, the basemap is empty, its CRS is undefined,
        identifiers or native centroids are duplicated, profile metadata is
        inconsistent, the grid type is unsupported, or the recorded CRS does not
        match the file CRS.
    """

    required_columns = [
        "region",
        "site_id",
        "study_area",
        "province_codes",
        "lon",
        "lat",
        "centroid_x",
        "centroid_y",
        "grid_type",
        "grid_crs",
        "resolution",
        "resolution_unit",
        "cell_size_native",
        "keep_method",
        "geometry",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in regions.columns
    ]

    if missing_columns:
        raise ValueError(
            f"{basemap_path.name} missing columns: {missing_columns}"
        )

    if regions.empty:
        raise ValueError(
            f"{basemap_path.name} contains no regions."
        )

    if regions.crs is None:
        raise ValueError(
            f"{basemap_path.name} has an undefined CRS."
        )

    if not regions["region"].is_unique:
        raise ValueError(
            f"{basemap_path.name} has duplicate region IDs."
        )

    if not regions["site_id"].is_unique:
        raise ValueError(
            f"{basemap_path.name} has duplicate site IDs."
        )

    if (
        regions[["centroid_x", "centroid_y"]]
        .drop_duplicates()
        .shape[0]
        != len(regions)
    ):
        raise ValueError(
            f"{basemap_path.name} has duplicate native centroids."
        )

    single_value_columns = [
        "study_area",
        "grid_type",
        "grid_crs",
        "resolution",
        "resolution_unit",
        "cell_size_native",
        "keep_method",
    ]

    for column in single_value_columns:
        if regions[column].nunique(dropna=False) != 1:
            raise ValueError(
                f"{basemap_path.name} contains multiple values "
                f"for {column!r}."
            )

    grid_type = str(regions["grid_type"].iloc[0])

    if grid_type not in {"geographic", "projected"}:
        raise ValueError(
            f"{basemap_path.name} has unsupported grid type: "
            f"{grid_type!r}"
        )

    metadata_crs = str(regions["grid_crs"].iloc[0])
    actual_crs = regions.crs.to_string()

    if metadata_crs != actual_crs:
        raise ValueError(
            f"{basemap_path.name} grid_crs metadata "
            f"({metadata_crs}) does not match file CRS "
            f"({actual_crs})."
        )


def build_region_adjacency(
    basemap_path: Path,
    coord_precision: int,
    no_neighbor_id: str,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Build rook-adjacency nodes and directed edges for one basemap.

    The function loads and validates a processed Stage 1 basemap, derives the
    cardinal neighbour of each region from its native centroid coordinates, and
    constructs a directed edge table for all valid up, down, left, and right
    connections.

    Neighbour lookup is performed in the basemap's native coordinate system using
    the configured coordinate precision and cell size. Missing neighbours are
    recorded with ``no_neighbor_id``. Geodesic distances between connected region
    centroids are calculated in kilometres using their latitude and longitude
    coordinates.

    Parameters
    ----------
    basemap_path : Path
        Path to the processed Stage 1 basemap GeoPackage.
    coord_precision : int
        Number of decimal places used when rounding native centroid coordinates
        during neighbour lookup.
    no_neighbor_id : str
        Sentinel value assigned when no cardinal neighbour exists.

    Returns
    -------
    tuple[gpd.GeoDataFrame, pd.DataFrame]
        Updated region GeoDataFrame containing neighbour identifiers, neighbour
        distances, and neighbour counts, together with a directed adjacency-edge
        table containing topology, profile metadata, endpoint coordinates, and
        geodesic distance for each valid connection.

    Raises
    ------
    FileNotFoundError
        If the basemap GeoPackage cannot be found or opened.
    ValueError
        If the basemap fails validation, contains inconsistent metadata, or uses an
        unsupported grid representation.
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

    study_area = str(regions["study_area"].iloc[0])
    grid_type = str(regions["grid_type"].iloc[0])
    grid_crs = str(regions["grid_crs"].iloc[0])
    resolution = float(regions["resolution"].iloc[0])
    resolution_unit = str(regions["resolution_unit"].iloc[0])
    cell_size_native = float(
        regions["cell_size_native"].iloc[0]
    )
    keep_method = str(regions["keep_method"].iloc[0])

    display_unit = (
        "°"
        if resolution_unit == "degree"
        else f" {resolution_unit}"
    )

    print(f"Study area: {study_area}")
    print(f"Grid type: {grid_type}")
    print(f"Resolution: {resolution:g}{display_unit}")
    print(f"Native cell size: {cell_size_native:g}")
    print(f"Keep method: {keep_method}")

    regions["x_key"] = (
        regions["centroid_x"]
        .astype(float)
        .round(coord_precision)
    )
    regions["y_key"] = (
        regions["centroid_y"]
        .astype(float)
        .round(coord_precision)
    )

    coord_to_region = {
        (
            float(row.x_key),
            float(row.y_key),
        ): str(row.region)
        for row in regions.itertuples(index=False)
    }

    regions["up_id"] = [
        coord_to_region.get(
            (
                round(float(row.x_key), coord_precision),
                round(
                    float(row.y_key) + cell_size_native,
                    coord_precision,
                ),
            ),
            no_neighbor_id,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["down_id"] = [
        coord_to_region.get(
            (
                round(float(row.x_key), coord_precision),
                round(
                    float(row.y_key) - cell_size_native,
                    coord_precision,
                ),
            ),
            no_neighbor_id,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["right_id"] = [
        coord_to_region.get(
            (
                round(
                    float(row.x_key) + cell_size_native,
                    coord_precision,
                ),
                round(float(row.y_key), coord_precision),
            ),
            no_neighbor_id,
        )
        for row in regions.itertuples(index=False)
    ]

    regions["left_id"] = [
        coord_to_region.get(
            (
                round(
                    float(row.x_key) - cell_size_native,
                    coord_precision,
                ),
                round(float(row.y_key), coord_precision),
            ),
            no_neighbor_id,
        )
        for row in regions.itertuples(index=False)
    ]

    neighbor_columns = list(NEIGHBOR_MAP.keys())

    regions["n_neighbors"] = sum(
        (regions[column] != no_neighbor_id).astype(int)
        for column in neighbor_columns
    )

    print("Neighbour count distribution:")
    print(regions["n_neighbors"].value_counts().sort_index())
    print(
        f"Average neighbours: "
        f"{regions['n_neighbors'].mean():.2f}"
    )
    print(
        "Isolated regions: "
        f"{(regions['n_neighbors'] == 0).sum():,}"
    )

    lookup_columns = [
        "lat",
        "lon",
        "centroid_x",
        "centroid_y",
    ]

    region_lookup = (
        regions
        .set_index("region")[lookup_columns]
        .to_dict("index")
    )

    for direction in ["up", "down", "right", "left"]:
        distance_column = f"{direction}_distance"
        neighbor_column = f"{direction}_id"

        regions[distance_column] = np.nan

        for row in regions.itertuples():
            neighbor = getattr(row, neighbor_column)

            if neighbor == no_neighbor_id:
                continue

            coord_from = (
                float(row.lat),
                float(row.lon),
            )
            coord_to = (
                float(region_lookup[neighbor]["lat"]),
                float(region_lookup[neighbor]["lon"]),
            )

            regions.at[row.Index, distance_column] = float(
                distance(coord_from, coord_to).km
            )

    edge_rows: list[dict] = []

    for row in regions.itertuples(index=False):
        for neighbor_column, direction in NEIGHBOR_MAP.items():
            region_to = getattr(row, neighbor_column)

            if region_to == no_neighbor_id:
                continue

            neighbor_data = region_lookup[region_to]
            distance_column = f"{direction}_distance"

            edge_rows.append(
                {
                    "edge_region": f"{row.region}-{region_to}",
                    "region_from": row.region,
                    "region_to": region_to,
                    "direction": direction,
                    "study_area": row.study_area,
                    "province_codes": row.province_codes,
                    "grid_type": grid_type,
                    "grid_crs": grid_crs,
                    "resolution": resolution,
                    "resolution_unit": resolution_unit,
                    "cell_size_native": cell_size_native,
                    "keep_method": keep_method,
                    "x_from": float(row.centroid_x),
                    "y_from": float(row.centroid_y),
                    "x_to": float(neighbor_data["centroid_x"]),
                    "y_to": float(neighbor_data["centroid_y"]),
                    "lon_from": float(row.lon),
                    "lat_from": float(row.lat),
                    "lon_to": float(neighbor_data["lon"]),
                    "lat_to": float(neighbor_data["lat"]),
                    "distance_km": float(
                        getattr(row, distance_column)
                    ),
                }
            )

    adjacency_edges = pd.DataFrame(edge_rows)

    print(
        f"Directed adjacency edges: "
        f"{len(adjacency_edges):,}"
    )

    regions = regions.drop(
        columns=["x_key", "y_key"],
        errors="ignore",
    )

    return regions, adjacency_edges


def build_edge_geometries(
    adjacency_edges: pd.DataFrame,
    grid_crs: str,
) -> gpd.GeoDataFrame:
    """Convert directed adjacency records into native-CRS line geometries.

The function copies the directed adjacency-edge table and constructs one
``LineString`` geometry per row from the native-coordinate endpoint fields
``x_from``, ``y_from``, ``x_to``, and ``y_to``. The resulting geometries retain
the coordinate reference system supplied through ``grid_crs``.

If the input table is empty, an empty GeoDataFrame with the same tabular
structure and configured CRS is returned.

Parameters
----------
adjacency_edges : pd.DataFrame
    Directed adjacency-edge table containing native-coordinate endpoint fields.
grid_crs : str
    Coordinate reference system assigned to the resulting edge geometries.

Returns
-------
gpd.GeoDataFrame
    Directed adjacency edges with a ``LineString`` geometry column in the
    supplied native CRS.
    """

    if adjacency_edges.empty:
        return gpd.GeoDataFrame(
            adjacency_edges,
            geometry=[],
            crs=grid_crs,
        )

    edges = adjacency_edges.copy()

    edges["geometry"] = edges.apply(
        lambda row: LineString(
            [
                (
                    float(row["x_from"]),
                    float(row["y_from"]),
                ),
                (
                    float(row["x_to"]),
                    float(row["y_to"]),
                ),
            ]
        ),
        axis=1,
    )

    return gpd.GeoDataFrame(
        edges,
        geometry="geometry",
        crs=grid_crs,
    )


def save_adjacency_preview(
    regions_graph: gpd.GeoDataFrame,
    adjacency_edges: pd.DataFrame,
    png_path: Path,
) -> None:
    """Save a PNG preview of a region-adjacency graph.

    The function validates that the graph nodes have a defined coordinate reference
    system, converts the directed adjacency table into line geometries, and plots
    the graph over the region polygons. Regions with no valid neighbours are
    highlighted separately.

    The preview title records the study area, grid type, resolution, retention
    method, node count, directed-edge count, and number of isolated regions. The
    completed figure is saved as a 300-DPI PNG and then closed.

    Parameters
    ----------
    regions_graph : gpd.GeoDataFrame
        Validated graph-node regions containing adjacency counts, profile metadata,
        geometries, and a defined CRS.
    adjacency_edges : pd.DataFrame
        Directed adjacency-edge table containing native-coordinate endpoints.
    png_path : Path
        Destination path for the generated PNG preview.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the graph-node GeoDataFrame has an undefined CRS.
    """

    if regions_graph.crs is None:
        raise ValueError(
            "Cannot save adjacency preview because node CRS is undefined."
        )

    edge_gdf = build_edge_geometries(
        adjacency_edges=adjacency_edges,
        grid_crs=regions_graph.crs.to_string(),
    )

    isolated_regions = regions_graph.loc[
        regions_graph["n_neighbors"] == 0
    ]

    study_area = str(
        regions_graph["study_area"].iloc[0]
    )
    grid_type = str(
        regions_graph["grid_type"].iloc[0]
    )
    resolution = float(
        regions_graph["resolution"].iloc[0]
    )
    resolution_unit = str(
        regions_graph["resolution_unit"].iloc[0]
    )
    keep_method = str(
        regions_graph["keep_method"].iloc[0]
    )

    display_unit = (
        "°"
        if resolution_unit == "degree"
        else f" {resolution_unit}"
    )

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
        f"{study_area} region adjacency graph: "
        f"{resolution:g}{display_unit} {grid_type} "
        f"{keep_method}\n"
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
    config: GeospatialBuildConfig,
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
            coord_precision=config.adjacency.coordinate_precision,
            no_neighbor_id=config.adjacency.no_neighbor_id,
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

        if regions_graph.crs is None:
            raise ValueError(
                f"{basemap_path.name} has an undefined CRS."
            )

        edge_gdf = build_edge_geometries(
            adjacency_edges=adjacency_edges,
            grid_crs=regions_graph.crs.to_string(),
        )

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
                "study_area": str(
                    regions_graph["study_area"].iloc[0]
                ),
                "province_codes": str(
                    regions_graph["province_codes"].iloc[0]
                ),
                "grid_type": str(
                    regions_graph["grid_type"].iloc[0]
                ),
                "grid_crs": str(
                    regions_graph["grid_crs"].iloc[0]
                ),
                "resolution": float(
                    regions_graph["resolution"].iloc[0]
                ),
                "resolution_unit": str(
                    regions_graph["resolution_unit"].iloc[0]
                ),
                "cell_size_native": float(
                    regions_graph["cell_size_native"].iloc[0]
                ),
                "keep_method": str(
                    regions_graph["keep_method"].iloc[0]
                ),
                "nodes": len(regions_graph),
                "directed_edges": len(adjacency_edges),
                "average_neighbors": float(regions_graph["n_neighbors"].mean()),
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


def parse_args() -> argparse.Namespace:
    """Parse the command-line path to a Stage 2 build profile.

    The parser requires a ``--config`` argument identifying the TOML file that
    defines the shared Geospatial-CANOE preprocessing configuration.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments containing the build-profile path in
        ``config``.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Build configured Geospatial-CANOE region-adjacency graphs."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help=(
            "Path to a geospatial preprocessing TOML build profile."
        ),
    )
    return parser.parse_args()


def run_adjacency_build(
    config: GeospatialBuildConfig,
) -> pd.DataFrame:
    """Run Stage 2 region-adjacency construction for one build profile.

    The function verifies that the configured adjacency method is supported,
    creates the processed graph directory, discovers matching Stage 1 basemaps,
    and builds adjacency graph products for each basemap. It then exports the
    combined graph summary as a profile-labelled CSV.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Loaded geospatial build profile defining the study area, basemap variants,
        and adjacency settings.

    Returns
    -------
    pd.DataFrame
        Summary table describing the adjacency graph produced for each matching
        basemap.

    Raises
    ------
    ValueError
        If the configured adjacency method is not ``"rook"``.
    FileNotFoundError
        If no matching Stage 1 basemaps can be found.
    """

    if config.adjacency.method != "rook":
        raise ValueError(
            "build_region_adjacency.py currently supports only rook "
            "adjacency."
        )

    PROCESSED_GRAPH.mkdir(parents=True, exist_ok=True)

    basemap_files = find_basemap_files(
        basemap_dir=PROCESSED_BASEMAP,
        config=config,
    )

    graph_summary = build_all_adjacency_graphs(
        basemap_files=basemap_files,
        output_dir=PROCESSED_GRAPH,
        config=config,
    )

    summary_path = (
        PROCESSED_GRAPH
        / f"{config.study_area.label}_region_adjacency_graph_summary.csv"
    )

    graph_summary.to_csv(
        summary_path,
        index=False,
    )

    print("\nStage 2 complete.")
    print(f"Exported summary: {summary_path}")

    return graph_summary


def main() -> None:
    """Load a TOML build profile and run Stage 2 adjacency construction.

    The function parses the command-line configuration path, loads and prints the
    shared geospatial build profile, and executes the region-adjacency workflow.

    Returns
    -------
    None
    """

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_adjacency_build(config)


if __name__ == "__main__":
    main()
