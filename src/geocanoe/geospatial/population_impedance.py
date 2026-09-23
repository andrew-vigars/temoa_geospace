"""Build DA population-density evidence for pipeline impedance.

The layer joins Statistics Canada's 2021 population table to dissemination-area
polygons, intersects those polygons with the 2021 population-centre footprint,
and assigns the versioned density-band factors in the pipeline penalty registry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pyogrio
from pyproj import CRS

from geocanoe.acquisition.residential import POPULATION_DENSITY_FIELD
from geocanoe.config import GeospatialBuildConfig
from geocanoe.geospatial.hydrography import find_study_area_boundary
from geocanoe.paths import find_project_root
from geocanoe.regions import PRUID_TO_CODE
from geocanoe.registry.geospatial_sources import GeospatialSourceRegistry
from geocanoe.registry.pipeline_impedance import (
    PipelinePenaltyLayer,
    PipelinePenaltyRegistry,
    PopulationDensityBand,
)


PROJECT_ROOT = find_project_root()
PROCESSED_PIPELINE_IMPEDANCE = (
    PROJECT_ROOT / "data_files" / "processed" / "pipeline_impedance"
)
PENALTY_LAYER_NAME = "population_exposure"
DA_SOURCE_ID = "statcan_2021_da_population_density_inputs"
LPC_SOURCE_ID = "statcan_gasoline_demand_inputs"
FEATURE_LAYER = "population_exposure_features"
IMPEDANCE_LAYER = "pipeline_impedance_polygons"


def _resolve_penalty_registry(config: GeospatialBuildConfig) -> Path:
    path = Path(config.pipeline_impedance.penalty_registry).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def classify_population_density(
    density: pd.Series,
    bands: tuple[PopulationDensityBand, ...],
) -> pd.DataFrame:
    """Classify every numeric density into exactly one configured band."""

    numeric = pd.to_numeric(density, errors="coerce").fillna(0.0).astype(float)
    labels = pd.Series(pd.NA, index=numeric.index, dtype="string")
    factors = pd.Series(float("nan"), index=numeric.index, dtype=float)
    match_count = pd.Series(0, index=numeric.index, dtype=int)
    for band in bands:
        mask = numeric.map(band.contains)
        labels.loc[mask] = band.name
        factors.loc[mask] = band.factor
        match_count.loc[mask] += 1
    invalid = match_count.ne(1)
    if invalid.any():
        values = sorted(numeric.loc[invalid].unique())[:5]
        raise ValueError(
            "Population-density bands must match every value exactly once; "
            f"invalid examples: {values}."
        )
    return pd.DataFrame(
        {"density_band": labels, "configured_factor": factors},
        index=density.index,
    )


def build_population_exposure(
    dissemination_areas: gpd.GeoDataFrame,
    population: pd.DataFrame,
    population_centres: gpd.GeoDataFrame,
    *,
    jurisdictions: tuple[str, ...],
    output_crs: str,
    penalty: PipelinePenaltyLayer,
    build_id: str,
    study_area: str,
    penalty_profile: str,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Join DA density, mask it to LPC polygons, and assign penalty factors."""

    target_crs = CRS.from_user_input(output_crs)
    if not target_crs.is_projected:
        raise ValueError("Population impedance output_crs must be projected.")
    if dissemination_areas.crs is None or population_centres.crs is None:
        raise ValueError("DA and population-centre inputs require CRS metadata.")
    required_da = {"DAUID", "DGUID", "PRUID", dissemination_areas.geometry.name}
    required_lpc = {
        "PCUID",
        "PCPUID",
        "PCNAME",
        "PCCLASS",
        "PRUID",
        population_centres.geometry.name,
    }
    missing_da = sorted(required_da - set(dissemination_areas.columns))
    missing_lpc = sorted(required_lpc - set(population_centres.columns))
    if missing_da or missing_lpc:
        raise ValueError(
            f"Population impedance inputs are missing DA fields {missing_da} "
            f"or LPC fields {missing_lpc}."
        )
    if "DGUID" not in population or POPULATION_DENSITY_FIELD not in population:
        raise ValueError("Population table is missing DGUID or population density.")
    if penalty.penalty_method != "population_density_band_factor" or not penalty.bands:
        raise ValueError(
            "Population exposure requires population_density_band_factor bands."
        )
    if penalty.spatial_scope != "statcan_population_centres":
        raise ValueError(
            "Population exposure currently requires statcan_population_centres scope."
        )

    das = dissemination_areas.to_crs(target_crs).copy()
    centres = population_centres.to_crs(target_crs).copy()
    das["province"] = das["PRUID"].astype(str).map(PRUID_TO_CODE)
    centres["province"] = centres["PRUID"].astype(str).map(PRUID_TO_CODE)
    das = das.loc[das["province"].isin(jurisdictions)].copy()
    centres = centres.loc[centres["province"].isin(jurisdictions)].copy()
    if das.empty or centres.empty:
        raise ValueError("No DA or population-centre polygons match the study area.")

    population_values = population[["DGUID", POPULATION_DENSITY_FIELD]].copy()
    if population_values["DGUID"].duplicated().any():
        population_values = population_values.drop_duplicates("DGUID", keep="first")
    population_values[POPULATION_DENSITY_FIELD] = pd.to_numeric(
        population_values[POPULATION_DENSITY_FIELD], errors="coerce"
    )
    das = das.merge(
        population_values,
        on="DGUID",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    missing_population = int(das["_merge"].ne("both").sum())
    if missing_population:
        raise ValueError(
            f"{missing_population:,} selected DA polygons lack a population-table row."
        )
    das = das.drop(columns="_merge").rename(
        columns={POPULATION_DENSITY_FIELD: "population_density_per_km2"}
    )
    das["population_density_per_km2"] = das[
        "population_density_per_km2"
    ].fillna(0.0)

    for frame in (das, centres):
        invalid = ~frame.geometry.is_valid
        if invalid.any():
            frame.loc[invalid, frame.geometry.name] = frame.loc[
                invalid
            ].geometry.make_valid()
    das = das.loc[
        das.geometry.notna()
        & ~das.geometry.is_empty
        & das.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()
    centres = centres.loc[
        centres.geometry.notna()
        & ~centres.geometry.is_empty
        & centres.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()

    da_fields = [
        "DAUID",
        "DGUID",
        "province",
        "population_density_per_km2",
        das.geometry.name,
    ]
    centre_fields = [
        "PCUID",
        "PCPUID",
        "PCNAME",
        "PCCLASS",
        "province",
        centres.geometry.name,
    ]
    exposure = gpd.overlay(
        das[da_fields],
        centres[centre_fields].rename(columns={"province": "lpc_province"}),
        how="intersection",
        keep_geom_type=True,
    )
    exposure = exposure.loc[
        exposure.geometry.notna()
        & ~exposure.geometry.is_empty
        & exposure.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()
    if exposure.empty:
        raise ValueError("DA polygons do not intersect any selected population centre.")
    cross_border = exposure["province"].ne(exposure["lpc_province"])
    if cross_border.any():
        exposure = exposure.loc[~cross_border].copy()

    classified = classify_population_density(
        exposure["population_density_per_km2"], penalty.bands
    )
    exposure[classified.columns] = classified
    exposure["applied_factor"] = (
        exposure["configured_factor"] if penalty.enabled else 0.0
    )
    exposure["source_id"] = penalty.source_id
    exposure["population_centre_source_id"] = LPC_SOURCE_ID
    exposure["build_id"] = build_id
    exposure["study_area"] = study_area
    exposure["penalty_profile"] = penalty_profile
    exposure["penalty_enabled"] = bool(penalty.enabled)
    exposure["area_km2"] = exposure.geometry.area / 1_000_000.0
    exposure = exposure.reset_index(drop=True)
    exposure["exposure_id"] = exposure.index + 1

    impedance = exposure.copy()
    impedance = impedance[
        [
            "exposure_id",
            "DAUID",
            "PCUID",
            "province",
            "density_band",
            "population_density_per_km2",
            "applied_factor",
            "source_id",
            "build_id",
            "study_area",
            "penalty_profile",
            "geometry",
        ]
    ].reset_index(drop=True)
    return exposure, impedance


def _plot_preview(
    exposure: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    path: Path,
    *,
    title: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(11, 9))
    boundary.to_crs(exposure.crs).boundary.plot(
        ax=axis, color="#252525", linewidth=0.55
    )
    exposure.plot(
        ax=axis,
        column="configured_factor",
        cmap="YlOrRd",
        edgecolor="none",
        linewidth=0,
        legend=True,
        legend_kwds={"label": "Added-distance factor"},
    )
    axis.set_title(title)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def run_population_impedance_build(
    config: GeospatialBuildConfig,
    *,
    penalty_registry_path: Path | str | None = None,
    source_registry_path: Path | str | None = None,
    boundary_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Build auditable DA/LPC population-exposure Silver evidence."""

    if not config.pipeline_impedance.enabled:
        print("Pipeline impedance stage disabled by the build profile.")
        return {}
    registry_path = (
        Path(penalty_registry_path).expanduser().resolve()
        if penalty_registry_path is not None
        else _resolve_penalty_registry(config)
    )
    penalty = PipelinePenaltyRegistry(registry_path).get_layer(
        config.pipeline_impedance.penalty_profile, PENALTY_LAYER_NAME
    )
    if penalty.source_id != DA_SOURCE_ID:
        raise ValueError(
            f"Population penalty source_id must be {DA_SOURCE_ID!r}, not "
            f"{penalty.source_id!r}."
        )
    source_registry = GeospatialSourceRegistry(
        source_registry_path or PROJECT_ROOT / "registry" / "geospatial_sources.yaml"
    )
    da_path = source_registry.resolve_bronze_path(DA_SOURCE_ID)
    population_path = source_registry.resolve_artifact_paths(
        DA_SOURCE_ID, "population_table"
    )[0]
    lpc_path = source_registry.resolve_artifact_paths(
        LPC_SOURCE_ID, "population_centres"
    )[0]
    for label, path in {
        "DA boundaries": da_path,
        "DA population table": population_path,
        "population centres": lpc_path,
    }.items():
        if not path.is_file():
            raise FileNotFoundError(f"Registered Bronze {label} not found: {path}")

    das = gpd.read_file(da_path, engine="pyogrio")
    population = pd.read_csv(
        population_path,
        usecols=["DGUID", POPULATION_DENSITY_FIELD],
        low_memory=False,
    )
    centres = gpd.read_file(lpc_path, engine="pyogrio")
    exposure, impedance = build_population_exposure(
        das,
        population,
        centres,
        jurisdictions=config.study_area.provinces,
        output_crs=config.pipeline_impedance.output_crs,
        penalty=penalty,
        build_id=config.build_id,
        study_area=config.study_area.label,
        penalty_profile=config.pipeline_impedance.penalty_profile,
    )

    resolved_boundary = (
        Path(boundary_path).expanduser().resolve()
        if boundary_path is not None
        else find_study_area_boundary(config)
    )
    boundary = gpd.read_file(resolved_boundary, engine="pyogrio")
    root = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else PROCESSED_PIPELINE_IMPEDANCE / config.build_id / "evidence"
    )
    root.mkdir(parents=True, exist_ok=True)
    gpkg_path = root / "population_exposure.gpkg"
    summary_path = root / "population_exposure_summary.csv"
    manifest_path = root / "population_exposure_manifest.json"
    preview_path = root / "preview" / "population_exposure.png"
    gpkg_path.unlink(missing_ok=True)
    pyogrio.write_dataframe(exposure, gpkg_path, layer=FEATURE_LAYER, driver="GPKG")
    pyogrio.write_dataframe(
        impedance, gpkg_path, layer=IMPEDANCE_LAYER, driver="GPKG", append=False
    )

    summary = (
        exposure.groupby(
            ["province", "density_band", "configured_factor"],
            dropna=False,
            observed=True,
        )
        .agg(
            dissemination_area_count=("DAUID", "nunique"),
            population_centre_count=("PCUID", "nunique"),
            fragment_count=("exposure_id", "size"),
            area_km2=("area_km2", "sum"),
        )
        .reset_index()
    )
    summary["applied_factor"] = (
        summary["configured_factor"] if penalty.enabled else 0.0
    )
    summary["penalty_profile"] = config.pipeline_impedance.penalty_profile
    summary["evidence_status"] = penalty.evidence_status
    summary.to_csv(summary_path, index=False)
    _plot_preview(
        exposure,
        boundary,
        preview_path,
        title=(
            f"{config.study_area.label}: population exposure "
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
        "penalty_registry": str(registry_path),
        "penalty_profile": config.pipeline_impedance.penalty_profile,
        "layer": {
            "name": penalty.name,
            "enabled": penalty.enabled,
            "source_id": penalty.source_id,
            "supporting_source_id": LPC_SOURCE_ID,
            "penalty_method": penalty.penalty_method,
            "density_field": penalty.density_field,
            "spatial_scope": penalty.spatial_scope,
            "bands": [
                {
                    "name": band.name,
                    "minimum": band.minimum,
                    "maximum": band.maximum,
                    "minimum_inclusive": band.minimum_inclusive,
                    "maximum_inclusive": band.maximum_inclusive,
                    "factor": band.factor,
                }
                for band in penalty.bands
            ],
            "evidence": {
                "status": penalty.evidence_status,
                "citation": penalty.citation,
                "publication_year": penalty.publication_year,
                "url": penalty.url,
                "note": penalty.note,
            },
        },
        "method": {
            "join": "DGUID one-to-one DA boundary to 2021 population table",
            "spatial_operation": "DA intersection with population-centre polygons",
            "null_density_treatment": "zero, matching the legacy model",
            "cross_jurisdiction_fragments": "excluded",
        },
        "inputs": {
            "da_boundaries": str(da_path),
            "population_table": str(population_path),
            "population_centres": str(lpc_path),
            "study_area_boundary": str(resolved_boundary),
            "source_registry": str(source_registry.path),
        },
        "outputs": {
            "gpkg": str(gpkg_path),
            "feature_layer": FEATURE_LAYER,
            "impedance_layer": IMPEDANCE_LAYER,
            "summary": str(summary_path),
            "preview": str(preview_path),
            "exposure_fragment_count": int(len(exposure)),
            "active_impedance_fragment_count": int(
                impedance["applied_factor"].gt(0).sum()
            ),
            "exposure_area_km2": float(exposure["area_km2"].sum()),
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
    "FEATURE_LAYER",
    "IMPEDANCE_LAYER",
    "PENALTY_LAYER_NAME",
    "build_population_exposure",
    "classify_population_density",
    "run_population_impedance_build",
]
