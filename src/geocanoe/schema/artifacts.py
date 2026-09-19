"""Canonical paths for one encoded Geospatial-CANOE schema variant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SchemaArtifactPaths:
    """Paths derived from a basemap, road layer, and connection method."""

    basemap: Path
    graph_nodes: Path
    graph_edges: Path
    road_edge_connections: Path
    road_edges: Path
    road_region_overlay: Path
    schema: Path


def resolve_schema_artifact_paths(
    project_root: Path,
    basemap_stem: str,
    road_layer: str,
    connection_method: str,
) -> SchemaArtifactPaths:
    """Build the canonical Stage 1-4 and encoded-schema paths.

    Keeping this naming rule in one function prevents schema construction,
    diagnostics, and future workflow stages from drifting independently.
    """

    processed = project_root / "data_files" / "processed"
    road_prefix = (
        f"{basemap_stem}_{road_layer}_road_connectivity_"
        f"{connection_method}"
    )

    return SchemaArtifactPaths(
        basemap=processed / "basemaps" / f"{basemap_stem}.gpkg",
        graph_nodes=processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg",
        graph_edges=processed / "graph" / f"{basemap_stem}_graph_edges.csv",
        road_edge_connections=(
            processed
            / "road_connectivity"
            / f"{road_prefix}_road_edge_connections.csv"
        ),
        road_edges=(
            processed / "road_connectivity" / f"{road_prefix}_road_edges.gpkg"
        ),
        road_region_overlay=(
            processed
            / "road_connectivity"
            / (
                f"{basemap_stem}_{road_layer}_"
                "road_connectivity_road_region_overlay.gpkg"
            )
        ),
        schema=(
            processed
            / "schema"
            / (
                f"CANOE_geospatial_{basemap_stem}_"
                f"{road_layer}_{connection_method}.sqlite"
            )
        ),
    )
