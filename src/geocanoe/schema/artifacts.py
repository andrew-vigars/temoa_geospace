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


def resolve_gasoline_demand_artifact_path(
    project_root: Path,
    build_id: str,
    basemap_stem: str,
) -> Path:
    """Return the resolution-specific Silver gasoline-demand artifact."""

    return (
        project_root
        / "data_files"
        / "processed"
        / "gasoline_demand"
        / build_id
        / "basemaps"
        / f"{basemap_stem}_gasoline_demand.gpkg"
    )


def resolve_schema_artifact_paths(
    project_root: Path,
    basemap_stem: str,
    road_layer: str,
    connection_method: str,
    *,
    build_id: str | None = None,
    scenario_id: str | None = None,
    fingerprint: str | None = None,
    artifact_barcode: str | None = None,
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
    if artifact_barcode is not None and (
        len(artifact_barcode) != 8
        or any(character not in "0123456789abcdef" for character in artifact_barcode)
    ):
        raise ValueError("artifact_barcode must contain eight lowercase hex characters.")
    if artifact_barcode is not None and not all(identity_parts):
        raise ValueError(
            "artifact_barcode requires build_id, scenario_id, and fingerprint."
        )
    if all(identity_parts):
        identity = f"gold_{build_id}_{scenario_id}_{fingerprint}"
        schema_name = (
            f"{identity}_{artifact_barcode}.sqlite"
            if artifact_barcode
            else f"{identity}.sqlite"
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


def resolve_latest_schema_artifact(canonical_path: Path) -> Path:
    """Return the newest build instance matching a canonical schema identity."""
    barcode_pattern = "[0-9a-f]" * 8
    candidates = list(
        canonical_path.parent.glob(
            f"{canonical_path.stem}_{barcode_pattern}.sqlite"
        )
    )
    if canonical_path.exists():
        candidates.append(canonical_path)
    if not candidates:
        return canonical_path
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)
