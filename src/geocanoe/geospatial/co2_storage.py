"""Map unified CanCO2 storage evidence onto onshore GeoCANOE basemaps.

This Silver transformation preserves two products for every configured basemap:

``regional_storage_evidence``
    One row per GeoCANOE region with source-presence, assessment-evidence, and
    unioned positive-capacity coverage fields.

``storage_region_crosswalk``
    The many-to-many storage-feature to model-region spatial relationship with
    overlap areas and fractions.  Geological capacity is deliberately not
    allocated or summed by region.

The current implementation covers the onshore basemap domain only. Offshore
mapping requires an offshore model-region product and is outside this stage.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geocanoe.acquisition.co2_storage import (
    RAW_CO2_STORAGE,
    find_latest_raw_storage_gpkg,
)
from geocanoe.config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)
from geocanoe.geospatial.adjacency import find_basemap_files
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
PROCESSED_BASEMAPS = PROJECT_ROOT / "data_files" / "processed" / "basemaps"
PROCESSED_CO2_STORAGE = (
    PROJECT_ROOT / "data_files" / "processed" / "co2_storage"
)
METRIC_CRS = "EPSG:3347"

SOURCE_EVIDENCE_FIELDS = {
    "NATCARB": ("natcarb_coverage_fraction", "has_natcarb"),
    "BC_STORAGE_ATLAS": ("bc_coverage_fraction", "has_bc_storage_atlas"),
    "ATLANTIC_COS": ("atlantic_coverage_fraction", "has_atlantic_cos"),
}
CAPACITY_COLUMNS = (
    "storage_p10_tonnes",
    "storage_p50_tonnes",
    "storage_p90_tonnes",
    "theoretical_storage_tonnes",
    "effective_storage_tonnes",
)
CAPACITY_FLAG_MAP = {
    "storage_p10_tonnes": "has_p10_capacity",
    "storage_p50_tonnes": "has_p50_capacity",
    "storage_p90_tonnes": "has_p90_capacity",
    "theoretical_storage_tonnes": "has_theoretical_capacity",
    "effective_storage_tonnes": "has_effective_capacity",
}
CAPACITY_FLAGS = tuple(CAPACITY_FLAG_MAP.values())


def _require_columns(
    dataframe: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    """Raise a descriptive error when a source table lacks required columns."""

    missing = required - set(dataframe.columns)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def _polygonal_geometry(geometry: BaseGeometry | None) -> BaseGeometry | None:
    """Return the polygonal component of a valid geometry."""

    if geometry is None or geometry.is_empty:
        return None
    valid_geometry = geometry if geometry.is_valid else make_valid(geometry)
    if valid_geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return valid_geometry
    if valid_geometry.geom_type == "GeometryCollection":
        polygon_parts = [
            part
            for part in valid_geometry.geoms
            if part.geom_type in {"Polygon", "MultiPolygon"}
        ]
        return unary_union(polygon_parts) if polygon_parts else None
    return None


def load_storage_source(
    storage_gpkg: Path,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    """Load and validate canonical CanCO2 feature, unit, and assessment tables."""

    if not storage_gpkg.is_file():
        raise FileNotFoundError(f"CanCO2 storage GeoPackage not found: {storage_gpkg}")

    storage_features = gpd.read_file(storage_gpkg, layer="storage_features")
    with sqlite3.connect(storage_gpkg) as connection:
        storage_units = pd.read_sql_query("SELECT * FROM storage_units", connection)
        storage_assessments = pd.read_sql_query(
            "SELECT * FROM storage_assessments", connection
        )

    _require_columns(
        storage_features,
        {
            "storage_feature_id",
            "storage_unit_id",
            "source_dataset",
            "source_layer",
            "storage_type",
            "representation",
            "assessment_type",
            "data_class",
            "capacity_data",
            "injectivity_status",
            "geometry",
        },
        "storage_features",
    )
    _require_columns(storage_units, {"storage_unit_id"}, "storage_units")
    _require_columns(
        storage_assessments,
        {
            "storage_feature_id",
            "storage_unit_id",
            "assessment_scope",
            *CAPACITY_COLUMNS,
        },
        "storage_assessments",
    )

    if storage_features.empty:
        raise ValueError("storage_features is empty.")
    if storage_features.crs is None:
        raise ValueError("storage_features has no coordinate reference system.")
    if storage_features["storage_feature_id"].isna().any():
        raise ValueError("storage_features contains null storage_feature_id values.")
    if storage_features["storage_feature_id"].duplicated().any():
        raise ValueError("storage_features contains duplicate storage_feature_id values.")
    if storage_units["storage_unit_id"].dropna().duplicated().any():
        raise ValueError("storage_units contains duplicate storage_unit_id values.")

    normalized = storage_features.copy()
    normalized["geometry"] = normalized.geometry.apply(_polygonal_geometry)
    normalized = normalized.loc[
        normalized.geometry.notna() & ~normalized.geometry.is_empty
    ].copy()
    if normalized.empty:
        raise ValueError("storage_features contains no usable polygonal geometry.")
    if (~normalized.geometry.is_valid).any():
        raise ValueError("storage_features contains invalid geometry after normalization.")

    valid_units = set(storage_units["storage_unit_id"].dropna().astype(str))
    feature_units = set(normalized["storage_unit_id"].dropna().astype(str))
    missing_units = feature_units - valid_units
    if missing_units:
        raise ValueError(
            "storage_features references unknown storage units: "
            f"{sorted(missing_units)[:10]}"
        )

    scopes = set(storage_assessments["assessment_scope"].dropna().astype(str))
    invalid_scopes = scopes - {"feature", "unit"}
    if invalid_scopes:
        raise ValueError(
            f"storage_assessments contains unsupported scopes: {sorted(invalid_scopes)}"
        )

    return normalized, storage_units, storage_assessments


def build_feature_capacity_flags(
    storage_features: gpd.GeoDataFrame,
    storage_assessments: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve positive capacity-assessment evidence to storage features."""

    assessments = storage_assessments.copy()
    for source_column, flag_column in CAPACITY_FLAG_MAP.items():
        assessments[source_column] = pd.to_numeric(
            assessments[source_column], errors="coerce"
        )
        assessments[flag_column] = assessments[source_column].fillna(0).gt(0)

    feature_scoped = assessments.loc[
        assessments["assessment_scope"].eq("feature")
        & assessments["storage_feature_id"].notna(),
        ["storage_feature_id", *CAPACITY_FLAGS],
    ]
    feature_flags = (
        feature_scoped.groupby("storage_feature_id", as_index=False)[
            list(CAPACITY_FLAGS)
        ].any()
        if not feature_scoped.empty
        else pd.DataFrame(columns=["storage_feature_id", *CAPACITY_FLAGS])
    )

    unit_scoped = assessments.loc[
        assessments["assessment_scope"].eq("unit")
        & assessments["storage_unit_id"].notna(),
        ["storage_unit_id", *CAPACITY_FLAGS],
    ]
    unit_flags = (
        unit_scoped.groupby("storage_unit_id", as_index=False)[
            list(CAPACITY_FLAGS)
        ].any()
        if not unit_scoped.empty
        else pd.DataFrame(columns=["storage_unit_id", *CAPACITY_FLAGS])
    )
    resolved_unit_flags = storage_features[
        ["storage_feature_id", "storage_unit_id"]
    ].merge(unit_flags, on="storage_unit_id", how="inner", validate="many_to_one")

    combined = pd.concat(
        [
            feature_flags,
            resolved_unit_flags[["storage_feature_id", *CAPACITY_FLAGS]],
        ],
        ignore_index=True,
    )
    if combined.empty:
        resolved = storage_features[["storage_feature_id"]].copy()
        for flag in CAPACITY_FLAGS:
            resolved[flag] = False
    else:
        resolved = combined.groupby("storage_feature_id", as_index=False)[
            list(CAPACITY_FLAGS)
        ].any()
        resolved = storage_features[["storage_feature_id"]].merge(
            resolved,
            on="storage_feature_id",
            how="left",
            validate="one_to_one",
        )
        resolved[list(CAPACITY_FLAGS)] = (
            resolved[list(CAPACITY_FLAGS)].fillna(False).astype(bool)
        )

    resolved["has_any_capacity_evidence"] = resolved[
        list(CAPACITY_FLAGS)
    ].any(axis=1)
    return resolved


def validate_basemap(regions: gpd.GeoDataFrame, basemap_path: Path) -> None:
    """Validate the minimum regional contract required for storage mapping."""

    _require_columns(regions, {"region", "site_id", "geometry"}, basemap_path.name)
    if regions.empty:
        raise ValueError(f"Basemap is empty: {basemap_path}")
    if regions.crs is None:
        raise ValueError(f"Basemap has no coordinate reference system: {basemap_path}")
    if regions["region"].isna().any() or regions["region"].duplicated().any():
        raise ValueError(f"Basemap region identifiers are null or duplicated: {basemap_path}")
    if regions["site_id"].isna().any() or regions["site_id"].duplicated().any():
        raise ValueError(f"Basemap site identifiers are null or duplicated: {basemap_path}")
    if regions.geometry.isna().any() or regions.geometry.is_empty.any():
        raise ValueError(f"Basemap contains null or empty geometry: {basemap_path}")


def build_storage_region_intersections(
    regions: gpd.GeoDataFrame,
    storage_features: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Build positive-area storage-feature to model-region intersections."""

    region_fields = ["region", "site_id", "geometry"]
    storage_fields = [
        "storage_feature_id",
        "storage_unit_id",
        "source_dataset",
        "source_layer",
        "storage_type",
        "representation",
        "assessment_type",
        "data_class",
        "capacity_data",
        "injectivity_status",
        "geometry",
    ]
    regions_metric = regions[region_fields].to_crs(METRIC_CRS)
    storage_metric = storage_features[storage_fields].to_crs(METRIC_CRS)
    regions_metric["region_area_m2"] = regions_metric.geometry.area
    storage_metric["storage_feature_area_m2"] = storage_metric.geometry.area

    intersections = gpd.overlay(
        regions_metric,
        storage_metric,
        how="intersection",
        keep_geom_type=False,
    )
    intersections["geometry"] = intersections.geometry.apply(_polygonal_geometry)
    intersections = intersections.loc[
        intersections.geometry.notna() & ~intersections.geometry.is_empty
    ].copy()
    intersections["intersection_area_m2"] = intersections.geometry.area
    intersections = intersections.loc[
        intersections["intersection_area_m2"].gt(0)
    ].copy()
    if intersections.empty:
        raise ValueError("No positive-area storage intersections were found.")

    intersections["region_overlap_fraction"] = (
        intersections["intersection_area_m2"] / intersections["region_area_m2"]
    ).clip(0, 1)
    intersections["feature_overlap_fraction"] = (
        intersections["intersection_area_m2"]
        / intersections["storage_feature_area_m2"]
    ).clip(0, 1)

    duplicate_pairs = intersections.duplicated(
        ["region", "site_id", "storage_feature_id"], keep=False
    )
    if duplicate_pairs.any():
        raise ValueError(
            "Spatial overlay produced duplicate region/site/storage-feature pairs."
        )
    return intersections


def build_regional_storage_evidence(
    regions: gpd.GeoDataFrame,
    intersections: gpd.GeoDataFrame,
    feature_capacity_flags: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Aggregate storage topology and evidence without allocating capacity."""

    source_coverage = intersections[
        ["region", "site_id", "source_dataset", "region_area_m2", "geometry"]
    ].dissolve(by=["region", "site_id", "source_dataset"], as_index=False)
    source_coverage["storage_union_area_m2"] = source_coverage.geometry.area
    source_coverage["storage_coverage_fraction"] = (
        source_coverage["storage_union_area_m2"]
        / source_coverage["region_area_m2"]
    ).clip(0, 1)
    coverage_wide = source_coverage.pivot_table(
        index=["region", "site_id"],
        columns="source_dataset",
        values="storage_coverage_fraction",
        aggfunc="max",
        fill_value=0.0,
    ).reset_index()
    coverage_wide.columns.name = None

    regional = regions[["region", "site_id", "geometry"]].copy()
    regional = regional.merge(
        coverage_wide,
        on=["region", "site_id"],
        how="left",
        validate="one_to_one",
    )
    coverage_fields: list[str] = []
    for source, (coverage_field, flag) in SOURCE_EVIDENCE_FIELDS.items():
        coverage_fields.append(coverage_field)
        if source in regional.columns:
            regional = regional.rename(columns={source: coverage_field})
        else:
            regional[coverage_field] = 0.0
        regional[coverage_field] = regional[coverage_field].fillna(0.0).clip(0, 1)
        regional[flag] = regional[coverage_field].gt(0)

    regional["has_quantitative_storage_evidence"] = (
        regional["has_natcarb"] | regional["has_bc_storage_atlas"]
    )
    regional["has_qualitative_storage_evidence"] = regional["has_atlantic_cos"]
    regional["storage_accessible"] = (
        regional["has_quantitative_storage_evidence"]
        | regional["has_qualitative_storage_evidence"]
    )
    regional["storage_accessibility"] = regional["storage_accessible"].astype("int8")

    flagged_intersections = intersections.merge(
        feature_capacity_flags,
        on="storage_feature_id",
        how="left",
        validate="many_to_one",
    )
    all_capacity_flags = [*CAPACITY_FLAGS, "has_any_capacity_evidence"]
    flagged_intersections[all_capacity_flags] = (
        flagged_intersections[all_capacity_flags]
        .astype("boolean")
        .fillna(False)
        .astype(bool)
    )
    regional_capacity_flags = flagged_intersections.groupby(
        ["region", "site_id"], as_index=False
    )[all_capacity_flags].any()
    regional = regional.merge(
        regional_capacity_flags,
        on=["region", "site_id"],
        how="left",
        validate="one_to_one",
    )
    regional[all_capacity_flags] = (
        regional[all_capacity_flags]
        .astype("boolean")
        .fillna(False)
        .astype(bool)
    )

    positive = flagged_intersections.loc[
        flagged_intersections["has_any_capacity_evidence"],
        ["region", "site_id", "region_area_m2", "geometry"],
    ].copy()
    if positive.empty:
        regional["capacity_evidence_area_m2"] = 0.0
        regional["capacity_evidence_coverage_fraction"] = 0.0
    else:
        positive_union = positive.dissolve(
            by=["region", "site_id"], as_index=False
        )
        positive_union["capacity_evidence_area_m2"] = positive_union.geometry.area
        positive_union["capacity_evidence_coverage_fraction"] = (
            positive_union["capacity_evidence_area_m2"]
            / positive_union["region_area_m2"]
        ).clip(0, 1)
        regional = regional.merge(
            positive_union[
                [
                    "region",
                    "site_id",
                    "capacity_evidence_area_m2",
                    "capacity_evidence_coverage_fraction",
                ]
            ],
            on=["region", "site_id"],
            how="left",
            validate="one_to_one",
        )
        regional[
            ["capacity_evidence_area_m2", "capacity_evidence_coverage_fraction"]
        ] = regional[
            ["capacity_evidence_area_m2", "capacity_evidence_coverage_fraction"]
        ].fillna(0.0)

    regional["capacity_evidence_coverage_percent"] = (
        regional["capacity_evidence_coverage_fraction"] * 100
    )
    return gpd.GeoDataFrame(regional, geometry="geometry", crs=regions.crs)


def build_storage_products(
    regions: gpd.GeoDataFrame,
    storage_features: gpd.GeoDataFrame,
    storage_units: pd.DataFrame,
    storage_assessments: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Build and validate the regional evidence and detailed crosswalk products."""

    feature_capacity_flags = build_feature_capacity_flags(
        storage_features, storage_assessments
    )
    intersections_metric = build_storage_region_intersections(
        regions, storage_features
    )
    regional_evidence = build_regional_storage_evidence(
        regions, intersections_metric, feature_capacity_flags
    )

    crosswalk_fields = [
        "region",
        "site_id",
        "storage_feature_id",
        "storage_unit_id",
        "source_dataset",
        "source_layer",
        "storage_type",
        "representation",
        "assessment_type",
        "data_class",
        "capacity_data",
        "injectivity_status",
        "region_area_m2",
        "storage_feature_area_m2",
        "intersection_area_m2",
        "region_overlap_fraction",
        "feature_overlap_fraction",
        "geometry",
    ]
    crosswalk = intersections_metric[crosswalk_fields].copy().to_crs(regions.crs)
    crosswalk = crosswalk.sort_values(
        ["region", "source_dataset", "storage_unit_id", "storage_feature_id"],
        na_position="last",
    ).reset_index(drop=True)

    validate_storage_products(
        regions=regions,
        storage_features=storage_features,
        storage_units=storage_units,
        regional_evidence=regional_evidence,
        crosswalk=crosswalk,
    )
    return regional_evidence, crosswalk


def validate_storage_products(
    *,
    regions: gpd.GeoDataFrame,
    storage_features: gpd.GeoDataFrame,
    storage_units: pd.DataFrame,
    regional_evidence: gpd.GeoDataFrame,
    crosswalk: gpd.GeoDataFrame,
) -> None:
    """Validate structural, referential, spatial, and semantic invariants."""

    errors: list[str] = []
    if len(regional_evidence) != len(regions):
        errors.append("Regional evidence does not contain one row per basemap region.")
    if regional_evidence["region"].duplicated().any():
        errors.append("Regional evidence contains duplicate region identifiers.")
    if crosswalk.duplicated(["region", "site_id", "storage_feature_id"]).any():
        errors.append("Crosswalk contains duplicate region/site/storage-feature keys.")
    if not crosswalk["region"].isin(regional_evidence["region"]).all():
        errors.append("Crosswalk references unknown GeoCANOE regions.")
    if not crosswalk["storage_feature_id"].isin(
        storage_features["storage_feature_id"]
    ).all():
        errors.append("Crosswalk references unknown storage features.")
    valid_units = set(storage_units["storage_unit_id"].dropna())
    referenced_units = set(crosswalk["storage_unit_id"].dropna())
    if not referenced_units.issubset(valid_units):
        errors.append("Crosswalk references unknown storage units.")
    for column in (
        "region_overlap_fraction",
        "feature_overlap_fraction",
    ):
        if not crosswalk[column].between(0, 1).all():
            errors.append(f"Crosswalk {column} contains values outside [0, 1].")
    if not regional_evidence["capacity_evidence_coverage_fraction"].between(
        0, 1
    ).all():
        errors.append("Regional capacity-evidence coverage lies outside [0, 1].")

    crosswalk_regions = set(crosswalk["region"])
    accessible_regions = set(
        regional_evidence.loc[
            regional_evidence["storage_accessible"], "region"
        ]
    )
    if crosswalk_regions != accessible_regions:
        errors.append("Crosswalk and regional accessibility coverage do not match.")
    capacity_regions = regional_evidence["has_any_capacity_evidence"]
    coverage_regions = regional_evidence[
        "capacity_evidence_coverage_fraction"
    ].gt(0)
    if not capacity_regions.equals(coverage_regions):
        errors.append("Positive capacity flags and unioned coverage do not match.")
    if not regional_evidence.loc[
        capacity_regions, "has_quantitative_storage_evidence"
    ].all():
        errors.append("Capacity evidence exists outside quantitative evidence regions.")

    if errors:
        raise ValueError("Invalid CO2 storage integration products:\n  - " + "\n  - ".join(errors))


def save_source_preview(
    storage_features: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Save a national preview of the normalized source storage features."""

    source = storage_features.to_crs(METRIC_CRS)
    figure, axis = plt.subplots(figsize=(12, 10))
    source.plot(
        ax=axis,
        column="source_dataset",
        categorical=True,
        legend=True,
        linewidth=0,
        alpha=0.65,
    )
    axis.set_title("CanCO2 unified storage features (offshore regions not modeled)")
    axis.set_axis_off()
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def save_mapping_preview(
    regional_evidence: gpd.GeoDataFrame,
    crosswalk: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Save a basemap preview of accessible regions and source footprints."""

    evidence = regional_evidence.to_crs(METRIC_CRS)
    mapped = crosswalk.to_crs(METRIC_CRS).dissolve(by="source_dataset")
    figure, axis = plt.subplots(figsize=(12, 10))
    evidence.plot(
        ax=axis,
        column="storage_accessibility",
        categorical=True,
        cmap="Greens",
        edgecolor="#888888",
        linewidth=0.15,
        alpha=0.65,
    )
    mapped.boundary.plot(ax=axis, color="#244a73", linewidth=0.5, alpha=0.8)
    axis.set_title("GeoCANOE regions with mapped onshore CO2 storage evidence")
    axis.set_axis_off()
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def export_storage_products(
    *,
    basemap_stem: str,
    regional_evidence: gpd.GeoDataFrame,
    crosswalk: gpd.GeoDataFrame,
    output_dir: Path,
) -> dict[str, Path]:
    """Export spatial products, inspection CSVs, and a mapping preview."""

    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    gpkg_path = output_dir / f"{basemap_stem}_co2_storage.gpkg"
    temporary_gpkg = output_dir / f"{basemap_stem}_co2_storage.tmp.gpkg"
    regional_csv = output_dir / f"{basemap_stem}_regional_storage_evidence.csv"
    crosswalk_csv = output_dir / f"{basemap_stem}_storage_region_crosswalk.csv"
    preview_path = preview_dir / f"{basemap_stem}_co2_storage_mapping.png"

    temporary_gpkg.unlink(missing_ok=True)
    regional_evidence.to_file(
        temporary_gpkg,
        layer="regional_storage_evidence",
        driver="GPKG",
    )
    crosswalk.to_file(
        temporary_gpkg,
        layer="storage_region_crosswalk",
        driver="GPKG",
        mode="a",
    )
    temporary_gpkg.replace(gpkg_path)
    regional_evidence.drop(columns="geometry").to_csv(regional_csv, index=False)
    crosswalk.drop(columns="geometry").to_csv(crosswalk_csv, index=False)
    save_mapping_preview(regional_evidence, crosswalk, preview_path)

    return {
        "gpkg": gpkg_path,
        "regional_csv": regional_csv,
        "crosswalk_csv": crosswalk_csv,
        "mapping_preview": preview_path,
    }


def run_co2_storage_build(
    config: GeospatialBuildConfig,
    *,
    storage_gpkg: Path | None = None,
) -> pd.DataFrame:
    """Build onshore CO2 storage integration products for all profile basemaps."""

    source_path = (
        storage_gpkg.expanduser().resolve()
        if storage_gpkg is not None
        else find_latest_raw_storage_gpkg(RAW_CO2_STORAGE)
    )
    storage_features, storage_units, storage_assessments = load_storage_source(
        source_path
    )
    basemap_files = find_basemap_files(PROCESSED_BASEMAPS, config)

    PROCESSED_CO2_STORAGE.mkdir(parents=True, exist_ok=True)
    preview_dir = PROCESSED_CO2_STORAGE / "preview"
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    save_source_preview(
        storage_features,
        preview_dir / "canco2_storage_features.png",
    )

    summary_rows: list[dict[str, object]] = []
    for basemap_path in basemap_files:
        print(f"\nMapping CO2 storage onto {basemap_path.name}")
        regions = gpd.read_file(basemap_path, layer="regions")
        validate_basemap(regions, basemap_path)
        regional_evidence, crosswalk = build_storage_products(
            regions,
            storage_features,
            storage_units,
            storage_assessments,
        )
        outputs = export_storage_products(
            basemap_stem=basemap_path.stem,
            regional_evidence=regional_evidence,
            crosswalk=crosswalk,
            output_dir=PROCESSED_CO2_STORAGE,
        )
        summary_rows.append(
            {
                "source_storage_gpkg": source_path.name,
                "basemap_file": basemap_path.name,
                "regions": len(regional_evidence),
                "accessible_regions": int(
                    regional_evidence["storage_accessibility"].sum()
                ),
                "capacity_evidence_regions": int(
                    regional_evidence["has_any_capacity_evidence"].sum()
                ),
                "crosswalk_records": len(crosswalk),
                "storage_features_mapped": crosswalk["storage_feature_id"].nunique(),
                "output_gpkg": outputs["gpkg"].name,
                "regional_csv": outputs["regional_csv"].name,
                "crosswalk_csv": outputs["crosswalk_csv"].name,
                "mapping_preview": outputs["mapping_preview"].name,
            }
        )
        print(f"Exported: {outputs['gpkg'].name}")
        print(f"Accessible regions: {summary_rows[-1]['accessible_regions']:,}")
        print(f"Crosswalk records: {len(crosswalk):,}")

    summary = pd.DataFrame(summary_rows)
    summary_path = (
        PROCESSED_CO2_STORAGE
        / f"{config.study_area.label}_co2_storage_summary.csv"
    )
    summary.to_csv(summary_path, index=False)
    print("\nCO2 storage Silver stage complete.")
    print(f"Source: {source_path}")
    print(f"Summary: {summary_path}")
    return summary


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the storage integration stage."""

    parser = argparse.ArgumentParser(
        description="Map acquired CanCO2 storage evidence onto GeoCANOE basemaps."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a geospatial preprocessing TOML build profile.",
    )
    parser.add_argument(
        "--storage-gpkg",
        type=Path,
        help="Optional exact acquired CanCO2 GeoPackage; defaults to newest raw release.",
    )
    return parser.parse_args()


def main() -> None:
    """Load a build profile and run onshore storage integration."""

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_co2_storage_build(config, storage_gpkg=args.storage_gpkg)


if __name__ == "__main__":
    main()
