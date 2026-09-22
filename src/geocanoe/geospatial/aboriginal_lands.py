"""Build study-area Silver Aboriginal Lands evidence for pipeline impedance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pyogrio
from pyproj import CRS

from geocanoe.config import GeospatialBuildConfig
from geocanoe.geospatial.hydrography import find_study_area_boundary
from geocanoe.geospatial.pipeline_impedance import dissolve_area_overlaps
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import GeospatialSourceRegistry
from geocanoe.registry.pipeline_impedance import PipelinePenaltyRegistry


PROJECT_ROOT = find_project_root()
PROCESSED_PIPELINE_IMPEDANCE = (
    PROJECT_ROOT / "data_files" / "processed" / "pipeline_impedance"
)
LAYER_NAME = "aboriginal_lands"
FEATURE_LAYER = "aboriginal_lands_features"
DISSOLVED_LAYER = "pipeline_impedance_polygons"


def _resolve_profile_path(config: GeospatialBuildConfig) -> Path:
    """Resolve a repository-relative or absolute penalty-registry path."""

    path = Path(config.pipeline_impedance.penalty_registry).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def normalize_aboriginal_lands(
    source: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    *,
    output_crs: str,
    source_id: str,
    build_id: str,
    study_area: str,
    penalty_profile: str,
    penalty_enabled: bool,
    configured_scalar: float,
    jurisdictions: tuple[str, ...] | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Repair, jurisdiction-filter, normalize, and dissolve source polygons.

    The source's legislative jurisdiction fields define study-area membership.
    This preserves complete land polygons instead of slicing them against a
    generalized census boundary; the boundary remains the shared map extent and
    CRS reference used by the Silver build.
    """

    target_crs = CRS.from_user_input(output_crs)
    if not target_crs.is_projected:
        raise ValueError(
            "Pipeline impedance output_crs must be projected so areas and "
            "intersection lengths have metric meaning."
        )
    if source.crs is None or boundary.crs is None:
        raise ValueError(
            "Aboriginal Lands source and study boundary require CRS metadata."
        )

    required = {"NID", "ALCODE", "NAME1", "JUR1", "ALTYPE", "WEBREF"}
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(f"Aboriginal Lands source is missing fields: {missing}")

    jurisdiction_fields = [
        field for field in ("JUR1", "JUR2", "JUR3", "JUR4") if field in source
    ]
    selected = source[
        [
            "NID",
            "ALCODE",
            "NAME1",
            "JUR1",
            "ALTYPE",
            "WEBREF",
            *[field for field in jurisdiction_fields if field != "JUR1"],
            source.geometry.name,
        ]
    ].copy()
    if jurisdictions is not None:
        selected = selected.loc[
            selected[jurisdiction_fields].isin(jurisdictions).any(axis=1)
        ].copy()
    selected = selected.to_crs(target_crs)
    invalid = ~selected.geometry.is_valid
    if invalid.any():
        selected.loc[invalid, selected.geometry.name] = selected.loc[
            invalid
        ].geometry.make_valid()
    selected = selected.loc[
        selected.geometry.notna() & ~selected.geometry.is_empty
    ].explode(index_parts=False, ignore_index=True)
    selected = selected.loc[
        selected.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()

    selected = selected.rename(
        columns={
            "NID": "source_feature_id",
            "ALCODE": "source_land_code",
            "NAME1": "land_name",
            "JUR1": "source_jurisdiction",
            "ALTYPE": "land_type",
            "WEBREF": "source_web_reference",
        }
    )
    selected.insert(0, "source_id", source_id)
    selected["build_id"] = build_id
    selected["study_area"] = study_area
    selected["penalty_profile"] = penalty_profile
    selected["penalty_enabled"] = bool(penalty_enabled)
    selected["configured_scalar"] = float(configured_scalar)
    selected["applied_scalar"] = float(configured_scalar) if penalty_enabled else 0.0
    selected["area_km2"] = selected.geometry.area / 1_000_000.0

    if selected.empty:
        dissolved = gpd.GeoDataFrame(
            {
                "layer_name": pd.Series(dtype=str),
                "source_id": pd.Series(dtype=str),
                "penalty_profile": pd.Series(dtype=str),
                "penalty_enabled": pd.Series(dtype=bool),
                "configured_scalar": pd.Series(dtype=float),
                "applied_scalar": pd.Series(dtype=float),
            },
            geometry=gpd.GeoSeries([], crs=target_crs),
            crs=target_crs,
        )
    else:
        dissolved_geometries, source_counts = dissolve_area_overlaps(selected)
        component_count = len(dissolved_geometries)
        dissolved = gpd.GeoDataFrame(
            {
                "component_id": range(1, component_count + 1),
                "layer_name": [LAYER_NAME] * component_count,
                "source_id": [source_id] * component_count,
                "build_id": [build_id] * component_count,
                "study_area": [study_area] * component_count,
                "penalty_profile": [penalty_profile] * component_count,
                "penalty_enabled": [bool(penalty_enabled)] * component_count,
                "configured_scalar": [float(configured_scalar)] * component_count,
                "applied_scalar": [float(configured_scalar) if penalty_enabled else 0.0]
                * component_count,
                "source_feature_count": source_counts,
            },
            geometry=dissolved_geometries,
            crs=target_crs,
        )
        dissolved["area_km2"] = dissolved.geometry.area / 1_000_000.0
    return selected.reset_index(drop=True), dissolved


def _plot_preview(
    features: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    path: Path,
    *,
    title: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(11, 9))
    boundary.to_crs(features.crs).boundary.plot(
        ax=axis, color="#282828", linewidth=0.55
    )
    if not features.empty:
        features.plot(
            ax=axis,
            color="#d17c2f",
            edgecolor="#7a3d0c",
            linewidth=0.15,
            alpha=0.7,
        )
    axis.set_title(title)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def run_aboriginal_lands_build(
    config: GeospatialBuildConfig,
    *,
    source_registry_path: Path | str | None = None,
    penalty_registry_path: Path | str | None = None,
    boundary_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Build the first Silver pipeline-impedance evidence layer."""

    if not config.pipeline_impedance.enabled:
        print("Pipeline impedance stage disabled by the build profile.")
        return {}

    source_registry = (
        GeospatialSourceRegistry(source_registry_path)
        if source_registry_path is not None
        else GeospatialSourceRegistry()
    )
    resolved_penalty_registry = (
        Path(penalty_registry_path).expanduser().resolve()
        if penalty_registry_path is not None
        else _resolve_profile_path(config)
    )
    penalty_registry = PipelinePenaltyRegistry(resolved_penalty_registry)
    penalty = penalty_registry.get_layer(
        config.pipeline_impedance.penalty_profile,
        LAYER_NAME,
    )
    source_registry.get(penalty.source_id)

    source_path = source_registry.resolve_bronze_path(penalty.source_id)
    metadata_path = source_registry.resolve_metadata_path(penalty.source_id)
    acquisition_manifest_path = source_registry.resolve_acquisition_manifest_path(
        penalty.source_id
    )
    for label, path in (
        ("Bronze Aboriginal Lands", source_path),
        ("Aboriginal Lands metadata", metadata_path),
        ("Aboriginal Lands acquisition manifest", acquisition_manifest_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")

    resolved_boundary = (
        Path(boundary_path).expanduser().resolve()
        if boundary_path is not None
        else find_study_area_boundary(config)
    )
    source = gpd.read_file(source_path, engine="pyogrio")
    boundary = gpd.read_file(resolved_boundary, engine="pyogrio")
    features, dissolved = normalize_aboriginal_lands(
        source,
        boundary,
        output_crs=config.pipeline_impedance.output_crs,
        source_id=penalty.source_id,
        build_id=config.build_id,
        study_area=config.study_area.label,
        penalty_profile=config.pipeline_impedance.penalty_profile,
        penalty_enabled=penalty.enabled,
        configured_scalar=penalty.scalar,
        jurisdictions=config.study_area.provinces,
    )

    root = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else PROCESSED_PIPELINE_IMPEDANCE / config.build_id / "evidence"
    )
    root.mkdir(parents=True, exist_ok=True)
    gpkg_path = root / "aboriginal_lands.gpkg"
    summary_path = root / "aboriginal_lands_summary.csv"
    manifest_path = root / "aboriginal_lands_manifest.json"
    preview_path = root / "preview" / "aboriginal_lands.png"
    gpkg_path.unlink(missing_ok=True)
    pyogrio.write_dataframe(features, gpkg_path, layer=FEATURE_LAYER, driver="GPKG")
    pyogrio.write_dataframe(
        dissolved,
        gpkg_path,
        layer=DISSOLVED_LAYER,
        driver="GPKG",
        append=False,
    )

    dissolved_area = float(dissolved["area_km2"].sum()) if not dissolved.empty else 0.0
    source_feature_count = int(features["source_feature_id"].nunique())
    normalized_part_count = int(len(features))
    dissolved_component_count = int(len(dissolved))
    pd.DataFrame(
        [
            {
                "layer_name": LAYER_NAME,
                "source_feature_count": source_feature_count,
                "normalized_polygon_part_count": normalized_part_count,
                "dissolved_component_count": dissolved_component_count,
                "dissolved_area_km2": dissolved_area,
                "penalty_enabled": penalty.enabled,
                "configured_scalar": penalty.scalar,
                "applied_scalar": penalty.applied_scalar,
                "penalty_profile": config.pipeline_impedance.penalty_profile,
                "evidence_status": penalty.evidence_status,
            }
        ]
    ).to_csv(summary_path, index=False)
    _plot_preview(
        features,
        boundary,
        preview_path,
        title=(
            f"{config.study_area.label}: Aboriginal Lands "
            f"({config.pipeline_impedance.penalty_profile})"
        ),
    )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_profile": str(config.source_path),
        "build_id": config.build_id,
        "study_area": config.study_area.label,
        "output_crs": config.pipeline_impedance.output_crs,
        "source_registry": str(source_registry.path),
        "penalty_registry": str(penalty_registry.path),
        "penalty_profile": config.pipeline_impedance.penalty_profile,
        "study_area_selection": {
            "method": "source_legislative_jurisdiction",
            "fields": ["JUR1", "JUR2", "JUR3", "JUR4"],
            "jurisdictions": list(config.study_area.provinces),
            "note": (
                "Complete legislative polygons are retained rather than clipped "
                "against the generalized census study-area boundary."
            ),
        },
        "layer": {
            "name": penalty.name,
            "enabled": penalty.enabled,
            "source_id": penalty.source_id,
            "penalty_method": penalty.penalty_method,
            "configured_scalar": penalty.scalar,
            "applied_scalar": penalty.applied_scalar,
            "evidence": {
                "status": penalty.evidence_status,
                "citation": penalty.citation,
                "publication_year": penalty.publication_year,
                "url": penalty.url,
                "note": penalty.note,
            },
        },
        "inputs": {
            "bronze_path": str(source_path),
            "bronze_size_bytes": source_path.stat().st_size,
            "metadata_path": str(metadata_path),
            "acquisition_manifest": str(acquisition_manifest_path),
            "study_area_boundary": str(resolved_boundary),
        },
        "outputs": {
            "gpkg": str(gpkg_path),
            "feature_layer": FEATURE_LAYER,
            "dissolved_layer": DISSOLVED_LAYER,
            "summary": str(summary_path),
            "preview": str(preview_path),
            "source_feature_count": source_feature_count,
            "normalized_polygon_part_count": normalized_part_count,
            "dissolved_component_count": dissolved_component_count,
            "dissolved_area_km2": dissolved_area,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "gpkg": gpkg_path,
        "summary": summary_path,
        "manifest": manifest_path,
        "preview": preview_path,
    }


__all__ = [
    "DISSOLVED_LAYER",
    "FEATURE_LAYER",
    "PROCESSED_PIPELINE_IMPEDANCE",
    "normalize_aboriginal_lands",
    "run_aboriginal_lands_build",
]
