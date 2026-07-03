#!/usr/bin/env python
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
    """Find Stage 1 basemap GeoPackages."""

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
    """Validate required basemap columns."""

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
    """Build rook-neighbour graph nodes and directed edge table."""

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
    """Convert directed edge table to centroid-to-centroid line geometries."""

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
    """Save a PNG preview of graph nodes, edges, and isolated regions."""

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
    """Build, export, preview, and summarize all adjacency graphs."""

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