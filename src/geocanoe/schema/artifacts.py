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
    co2_storage: Path
    schema: Path


def resolve_schema_artifact_paths(
    project_root: Path,
    basemap_stem: str,
    road_layer: str,
    connection_method: str,
    *,
    build_id: str | None = None,
    scenario_id: str | None = None,
    fingerprint: str | None = None,
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

    identity_parts = (build_id, scenario_id, fingerprint)
    if any(identity_parts) and not all(identity_parts):
        raise ValueError(
            "build_id, scenario_id, and fingerprint must be provided together."
        )
    if all(identity_parts):
        schema_name = (
            f"gold_{build_id}_{scenario_id}_{fingerprint}.sqlite"
        )
    else:
        schema_name = (
            f"CANOE_geospatial_{basemap_stem}_"
            f"{road_layer}_{connection_method}.sqlite"
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
        co2_storage=(
            processed / "co2_storage" / f"{basemap_stem}_co2_storage.gpkg"
        ),
        schema=processed / "schema" / schema_name,
    )
