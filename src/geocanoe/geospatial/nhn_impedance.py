"""Build polygonal NHN Silver evidence for pipeline impedance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pyogrio
from pyproj import CRS

from geocanoe.config import GeospatialBuildConfig
from geocanoe.geospatial.hydrography import (
    PROCESSED_NHN,
    find_study_area_boundary,
)
from geocanoe.geospatial.pipeline_impedance import dissolve_area_overlaps
from geocanoe.paths import find_project_root
from geocanoe.registry.pipeline_impedance import PipelinePenaltyRegistry


PROJECT_ROOT = find_project_root()
PROCESSED_PIPELINE_IMPEDANCE = (
    PROJECT_ROOT / "data_files" / "processed" / "pipeline_impedance"
)
PENALTY_LAYER_NAME = "nhn_waterbodies"
SOURCE_LAYER = "waterbody_polygons"
FEATURE_LAYER = "nhn_waterbody_features"
DISSOLVED_LAYER = "pipeline_impedance_polygons"


def _resolve_penalty_registry(config: GeospatialBuildConfig) -> Path:
    path = Path(config.pipeline_impedance.penalty_registry).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def find_silver_hydrography(config: GeospatialBuildConfig) -> Path:
    """Return the filtered polygonal NHN product built upstream."""

    path = PROCESSED_NHN / f"{config.study_area.label}_filtered_hydrography.gpkg"
    if not path.is_file():
        raise FileNotFoundError(
            f"NHN impedance requires the filtered Silver hydrography GeoPackage: {path}"
        )
    layers = {name for name, _geometry_type in pyogrio.list_layers(path)}
    if SOURCE_LAYER not in layers:
        raise ValueError(
            f"Silver hydrography {path} has no {SOURCE_LAYER!r} layer. Enable "
            "[hydrography.polygons] and select at least one polygon class."
        )
    return path


def normalize_nhn_waterbodies(
    source: gpd.GeoDataFrame,
    *,
    output_crs: str,
    expected_source_id: str,
    build_id: str,
    study_area: str,
    penalty_profile: str,
    penalty_enabled: bool,
    configured_scalar: float,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Validate NHN polygons and construct non-overlapping penalty components."""

    target_crs = CRS.from_user_input(output_crs)
    if not target_crs.is_projected:
        raise ValueError(
            "Pipeline impedance output_crs must be projected so intersection "
            "lengths have metric meaning."
        )
    if source.crs is None:
        raise ValueError("Silver NHN waterbodies require CRS metadata.")

    required = {
        "source_id",
        "source_feature_id",
        "feature_class",
        "permanency_class",
        "study_area",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(f"Silver NHN waterbodies are missing fields: {missing}")
    source_ids = set(source["source_id"].dropna().astype(str))
    if source_ids != {expected_source_id}:
        raise ValueError(
            "Silver NHN source identity does not match the selected penalty "
            f"profile: expected {expected_source_id!r}, found {sorted(source_ids)}."
        )
    study_areas = set(source["study_area"].dropna().astype(str))
    if study_areas != {study_area}:
        raise ValueError(
            f"Silver NHN study area must be {study_area!r}; found {sorted(study_areas)}."
        )

    features = source.to_crs(target_crs).copy()
    features = features.loc[
        features.geometry.notna() & ~features.geometry.is_empty
    ].copy()
    invalid = ~features.geometry.is_valid
    if invalid.any():
        features.loc[invalid, features.geometry.name] = features.loc[
            invalid
        ].geometry.make_valid()
    features = features.loc[
        features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].reset_index(drop=True)
    if features.empty:
        raise ValueError("Selected Silver NHN waterbody layer contains no polygons.")

    features["build_id"] = build_id
    features["penalty_profile"] = penalty_profile
    features["penalty_enabled"] = bool(penalty_enabled)
    features["configured_scalar"] = float(configured_scalar)
    features["applied_scalar"] = float(configured_scalar) if penalty_enabled else 0.0
    features["area_km2"] = features.geometry.area / 1_000_000.0

    component_geometries, source_counts = dissolve_area_overlaps(features)
    component_count = len(component_geometries)
    classes = ",".join(sorted(features["feature_class"].astype(str).unique()))
    permanency = ",".join(sorted(features["permanency_class"].astype(str).unique()))
    dissolved = gpd.GeoDataFrame(
        {
            "component_id": range(1, component_count + 1),
            "layer_name": [PENALTY_LAYER_NAME] * component_count,
            "source_id": [expected_source_id] * component_count,
            "build_id": [build_id] * component_count,
            "study_area": [study_area] * component_count,
            "feature_classes": [classes] * component_count,
            "permanency_classes": [permanency] * component_count,
            "penalty_profile": [penalty_profile] * component_count,
            "penalty_enabled": [bool(penalty_enabled)] * component_count,
            "configured_scalar": [float(configured_scalar)] * component_count,
            "applied_scalar": [float(configured_scalar) if penalty_enabled else 0.0]
            * component_count,
            "source_feature_count": source_counts,
        },
        geometry=component_geometries,
        crs=target_crs,
    )
    dissolved["area_km2"] = dissolved.geometry.area / 1_000_000.0
    return features.reset_index(drop=True), dissolved


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
    class_count = features["feature_class"].nunique()
    features.plot(
        ax=axis,
        column="feature_class" if class_count > 1 else None,
        color="#4f9fcf" if class_count == 1 else None,
        edgecolor="#174f73",
        linewidth=0.15,
        alpha=0.72,
        legend=class_count > 1,
    )
    axis.set_title(title)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def run_nhn_impedance_build(
    config: GeospatialBuildConfig,
    *,
    penalty_registry_path: Path | str | None = None,
    hydrography_path: Path | str | None = None,
    boundary_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Build audited polygonal NHN evidence for pipeline cost distance."""

    if not config.pipeline_impedance.enabled:
        print("Pipeline impedance stage disabled by the build profile.")
        return {}

    registry_path = (
        Path(penalty_registry_path).expanduser().resolve()
        if penalty_registry_path is not None
        else _resolve_penalty_registry(config)
    )
    registry = PipelinePenaltyRegistry(registry_path)
    penalty = registry.get_layer(
        config.pipeline_impedance.penalty_profile,
        PENALTY_LAYER_NAME,
    )
    if not config.hydrography.enabled or not config.hydrography.polygons.enabled:
        if penalty.enabled:
            raise ValueError(
                "The active NHN waterbody penalty requires hydrography and "
                "hydrography.polygons to be enabled."
            )
        print(
            "NHN waterbody evidence not built because hydrography polygons are disabled."
        )
        return {}
    if penalty.source_id != config.hydrography.source_id:
        raise ValueError(
            "NHN penalty source_id must match [hydrography].source_id: "
            f"{penalty.source_id!r} != {config.hydrography.source_id!r}."
        )

    silver_path = (
        Path(hydrography_path).expanduser().resolve()
        if hydrography_path is not None
        else find_silver_hydrography(config)
    )
    upstream_manifest = (
        PROCESSED_NHN / f"{config.study_area.label}_hydrography_manifest.json"
    )
    resolved_boundary = (
        Path(boundary_path).expanduser().resolve()
        if boundary_path is not None
        else find_study_area_boundary(config)
    )
    source = gpd.read_file(silver_path, layer=SOURCE_LAYER, engine="pyogrio")
    boundary = gpd.read_file(resolved_boundary, engine="pyogrio")
    features, dissolved = normalize_nhn_waterbodies(
        source,
        output_crs=config.pipeline_impedance.output_crs,
        expected_source_id=penalty.source_id,
        build_id=config.build_id,
        study_area=config.study_area.label,
        penalty_profile=config.pipeline_impedance.penalty_profile,
        penalty_enabled=penalty.enabled,
        configured_scalar=penalty.scalar,
    )

    root = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else PROCESSED_PIPELINE_IMPEDANCE / config.build_id / "evidence"
    )
    root.mkdir(parents=True, exist_ok=True)
    gpkg_path = root / "nhn_waterbodies.gpkg"
    summary_path = root / "nhn_waterbodies_summary.csv"
    manifest_path = root / "nhn_waterbodies_manifest.json"
    preview_path = root / "preview" / "nhn_waterbodies.png"
    gpkg_path.unlink(missing_ok=True)
    pyogrio.write_dataframe(features, gpkg_path, layer=FEATURE_LAYER, driver="GPKG")
    pyogrio.write_dataframe(
        dissolved,
        gpkg_path,
        layer=DISSOLVED_LAYER,
        driver="GPKG",
        append=False,
    )

    summary = (
        features.groupby(["feature_class", "permanency_class"], dropna=False)
        .agg(
            source_feature_count=("source_feature_id", "nunique"),
            normalized_geometry_count=("source_feature_id", "size"),
            area_km2=("area_km2", "sum"),
        )
        .reset_index()
    )
    summary["penalty_enabled"] = penalty.enabled
    summary["configured_scalar"] = penalty.scalar
    summary["applied_scalar"] = penalty.applied_scalar
    summary["penalty_profile"] = config.pipeline_impedance.penalty_profile
    summary["evidence_status"] = penalty.evidence_status
    summary.to_csv(summary_path, index=False)
    _plot_preview(
        features,
        boundary,
        preview_path,
        title=(
            f"{config.study_area.label}: NHN waterbodies "
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
        "penalty_registry": str(registry.path),
        "penalty_profile": config.pipeline_impedance.penalty_profile,
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
        "selection": {
            "source_layer": SOURCE_LAYER,
            "feature_classes": sorted(features["feature_class"].unique()),
            "permanency_classes": sorted(features["permanency_class"].unique()),
            "polygon_thresholds": config.hydrography.polygons.minimum_source_measure,
            "line_treatment": (
                "not_included; unbuffered NHN lines require a separate crossing "
                "or width model"
            ),
        },
        "inputs": {
            "silver_hydrography": str(silver_path),
            "silver_hydrography_manifest": (
                str(upstream_manifest) if upstream_manifest.is_file() else None
            ),
            "study_area_boundary": str(resolved_boundary),
        },
        "outputs": {
            "gpkg": str(gpkg_path),
            "feature_layer": FEATURE_LAYER,
            "dissolved_layer": DISSOLVED_LAYER,
            "summary": str(summary_path),
            "preview": str(preview_path),
            "source_feature_count": int(features["source_feature_id"].nunique()),
            "normalized_geometry_count": int(len(features)),
            "dissolved_component_count": int(len(dissolved)),
            "dissolved_area_km2": float(dissolved["area_km2"].sum()),
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
    "PENALTY_LAYER_NAME",
    "find_silver_hydrography",
    "normalize_nhn_waterbodies",
    "run_nhn_impedance_build",
]
