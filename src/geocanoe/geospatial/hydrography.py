"""Build topology-specific Silver hydrography from registered Bronze sources."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pyogrio
from shapely import clip_by_rect
from shapely.geometry import box
from tqdm.auto import tqdm

from geocanoe.config import GeospatialBuildConfig
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import GeospatialSourceRegistry


PROJECT_ROOT = find_project_root()
PROCESSED_BASEMAPS = PROJECT_ROOT / "data_files" / "processed" / "basemaps"
PROCESSED_NHN = PROJECT_ROOT / "data_files" / "processed" / "nhn"
SPATIAL_TILE_DEGREES = 5.0
PREVIEW_FEATURE_LIMIT = 5_000
# Conservative upper bound for square kilometres represented by one
# longitude-degree by one latitude-degree anywhere in Canada. It supports a
# safe R-tree bbox prefilter; exact area is still calculated in EPSG:3347.
MAX_KM2_PER_SQUARE_DEGREE = 13_000.0


def find_study_area_boundary(config: GeospatialBuildConfig) -> Path:
    """Return the projected boundary produced by the configured basemap stage."""

    path = (
        PROCESSED_BASEMAPS
        / f"{config.study_area.label}_boundary_epsg3347.gpkg"
    )
    if not path.is_file():
        raise FileNotFoundError(
            "Hydrography requires the projected Silver study-area boundary: "
            f"{path}"
        )
    return path


def _spatial_tiles(
    bounds: tuple[float, float, float, float],
    tile_size: float = SPATIAL_TILE_DEGREES,
) -> Iterable[tuple[float, float, float, float]]:
    """Yield deterministic longitude/latitude tiles covering source bounds."""

    min_x, min_y, max_x, max_y = bounds
    start_x = math.floor(min_x / tile_size) * tile_size
    start_y = math.floor(min_y / tile_size) * tile_size
    x = start_x
    while x < max_x:
        y = start_y
        while y < max_y:
            yield (x, y, min(x + tile_size, max_x), min(y + tile_size, max_y))
            y += tile_size
        x += tile_size


def _sql_in(field: str, values: Iterable[int]) -> str:
    codes = ", ".join(str(int(value)) for value in values)
    return f"{field} IN ({codes})"


def _quote_identifier(value: str) -> str:
    """Quote a trusted registry identifier for SQLite SQL."""

    return '"' + value.replace('"', '""') + '"'


def _read_source_tile(
    *,
    source_path: Path,
    source_layer: str,
    primary_key_field: str,
    geometry_field: str,
    preserve_fields: list[str],
    class_field: str,
    class_codes: dict[str, int],
    permanency_field: str,
    permanency_codes: dict[str, int],
    thresholds: dict[str, float],
    geometry_family: str,
    tile_bounds: tuple[float, float, float, float],
) -> gpd.GeoDataFrame:
    """Read one tile through the GeoPackage R-tree instead of scanning a layer."""

    layer = _quote_identifier(source_layer)
    primary_key = _quote_identifier(primary_key_field)
    geometry = _quote_identifier(geometry_field)
    spatial_index = _quote_identifier(
        f"rtree_{source_layer}_{geometry_field}"
    )
    selected = ", ".join(
        f"w.{_quote_identifier(field)}" for field in preserve_fields
    )
    selected = f"{selected}, w.{geometry}"
    min_x, min_y, max_x, max_y = (float(value) for value in tile_bounds)
    spatial_predicate = (
        f"r.maxx >= {min_x!r} AND r.minx <= {max_x!r} "
        f"AND r.maxy >= {min_y!r} AND r.miny <= {max_y!r}"
    )
    permanence_predicate = _sql_in(
        f"w.{_quote_identifier(permanency_field)}",
        permanency_codes.values(),
    )

    if geometry_family == "polygon":
        bbox_area = "((r.maxx - r.minx) * (r.maxy - r.miny))"
        class_terms = [
            (
                f"(w.{_quote_identifier(class_field)} = {code} AND "
                f"{bbox_area} >= "
                f"{thresholds[name] / MAX_KM2_PER_SQUARE_DEGREE!r})"
            )
            for name, code in class_codes.items()
        ]
        class_predicate = "(" + " OR ".join(class_terms) + ")"
    else:
        class_predicate = _sql_in(
            f"w.{_quote_identifier(class_field)}",
            class_codes.values(),
        )

    sql = (
        f"SELECT {selected} FROM {layer} AS w "
        f"JOIN {spatial_index} AS r ON w.{primary_key} = r.id "
        f"WHERE {spatial_predicate} AND {class_predicate} "
        f"AND {permanence_predicate}"
    )
    return pyogrio.read_dataframe(
        source_path,
        sql=sql,
        sql_dialect="SQLITE",
    )


def _write_chunk(path: Path, layer: str, chunk: gpd.GeoDataFrame) -> None:
    """Create or append one processed GeoPackage layer."""

    if path.exists():
        existing_layers = {name for name, _ in pyogrio.list_layers(path)}
        append = layer in existing_layers
    else:
        append = False
    pyogrio.write_dataframe(
        chunk,
        path,
        layer=layer,
        driver="GPKG",
        append=append,
        promote_to_multi=True,
    )


def _plot_preview(
    features: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    path: Path,
    title: str,
    geometry_family: str,
) -> None:
    """Write a static QA preview without requiring a web basemap."""

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(10, 10))
    boundary.boundary.plot(ax=axis, color="#222222", linewidth=0.8)
    if not features.empty:
        if geometry_family == "polygon":
            features.plot(
                ax=axis,
                column="feature_class",
                alpha=0.65,
                edgecolor="#204060",
                linewidth=0.15,
                legend=True,
            )
        else:
            features.plot(
                ax=axis,
                column="feature_class",
                linewidth=0.45,
                legend=True,
            )
    axis.set_title(title)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def _process_family(
    *,
    source_path: Path,
    source_id: str,
    source_crs: str,
    source_layer: str,
    output_layer: str,
    geometry_family: str,
    preserve_fields: list[str],
    primary_key_field: str,
    geometry_field: str,
    feature_id_field: str,
    class_field: str,
    class_codes: dict[str, int],
    permanency_field: str,
    permanency_codes: dict[str, int],
    thresholds: dict[str, float],
    boundary_target: gpd.GeoDataFrame,
    output_crs: str,
    study_area_label: str,
    output_path: Path,
) -> tuple[list[dict[str, Any]], gpd.GeoDataFrame]:
    """Stream one source geometry family through selection, clipping, and output."""

    boundary_source = boundary_target.to_crs(source_crs)
    boundary_source_geometry = boundary_source.geometry.union_all()
    boundary_target_geometry = boundary_target.to_crs(output_crs).geometry.union_all()
    code_to_class = {code: name for name, code in class_codes.items()}
    code_to_permanency = {code: name for name, code in permanency_codes.items()}
    seen_ids: set[str] = set()
    summaries: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"feature_count": 0.0, "source_measure": 0.0, "clipped_measure": 0.0}
    )
    preview_chunks: list[gpd.GeoDataFrame] = []
    preview_count = 0

    tile_bounds_list = list(_spatial_tiles(tuple(boundary_source.total_bounds)))
    for tile_bounds in tqdm(
        tile_bounds_list,
        desc=f"Filter {source_layer}",
        unit="tile",
        dynamic_ncols=True,
    ):
        tile = box(*tile_bounds)
        if not tile.intersects(boundary_source_geometry):
            continue
        chunk = _read_source_tile(
            source_path=source_path,
            source_layer=source_layer,
            primary_key_field=primary_key_field,
            geometry_field=geometry_field,
            preserve_fields=preserve_fields,
            class_field=class_field,
            class_codes=class_codes,
            permanency_field=permanency_field,
            permanency_codes=permanency_codes,
            thresholds=thresholds,
            geometry_family=geometry_family,
            tile_bounds=tile_bounds,
        )
        if chunk.empty:
            continue

        chunk = chunk[chunk[feature_id_field].notna()].copy()
        chunk[feature_id_field] = chunk[feature_id_field].astype(str)
        chunk = chunk[~chunk[feature_id_field].isin(seen_ids)].copy()
        if chunk.empty:
            continue

        chunk = chunk[chunk.geometry.notna() & ~chunk.geometry.is_empty].copy()
        chunk.geometry = chunk.geometry.make_valid()
        chunk["feature_class"] = chunk[class_field].map(code_to_class)
        chunk["permanency_class"] = chunk[permanency_field].map(code_to_permanency)
        chunk = chunk.to_crs(output_crs)

        if geometry_family == "polygon":
            chunk["source_area_km2"] = chunk.geometry.area / 1_000_000
            minimum = chunk["feature_class"].map(thresholds)
            chunk = chunk[chunk["source_area_km2"] >= minimum].copy()
        else:
            chunk["source_length_km"] = chunk.geometry.length / 1_000
            minimum = chunk["feature_class"].map(thresholds)
            chunk = chunk[chunk["source_length_km"] >= minimum].copy()
        if chunk.empty:
            continue

        # Apply the exact, highly detailed study-area boundary only after the
        # inexpensive R-tree and source-measure filters have reduced the chunk.
        min_x, min_y, max_x, max_y = chunk.total_bounds
        local_boundary = clip_by_rect(
            boundary_target_geometry,
            float(min_x),
            float(min_y),
            float(max_x),
            float(max_y),
        )
        chunk = chunk[chunk.geometry.intersects(local_boundary)].copy()
        if chunk.empty:
            continue
        seen_ids.update(chunk[feature_id_field])
        chunk.geometry = chunk.geometry.intersection(local_boundary)
        chunk = chunk[chunk.geometry.notna() & ~chunk.geometry.is_empty].copy()
        if geometry_family == "polygon":
            chunk = chunk[
                chunk.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
            ].copy()
            chunk["study_area_area_km2"] = chunk.geometry.area / 1_000_000
            source_measure_column = "source_area_km2"
            clipped_measure_column = "study_area_area_km2"
        else:
            chunk = chunk[
                chunk.geometry.geom_type.isin(("LineString", "MultiLineString"))
            ].copy()
            chunk["study_area_length_km"] = chunk.geometry.length / 1_000
            source_measure_column = "source_length_km"
            clipped_measure_column = "study_area_length_km"
        if chunk.empty:
            continue

        chunk["hydro_feature_id"] = (
            source_id + ":" + source_layer + ":" + chunk[feature_id_field]
        )
        chunk["source_id"] = source_id
        chunk["source_layer"] = source_layer
        chunk["source_feature_id"] = chunk[feature_id_field]
        chunk["study_area"] = study_area_label

        canonical_first = [
            "hydro_feature_id",
            "source_id",
            "source_layer",
            "source_feature_id",
            "feature_class",
            "permanency_class",
            "study_area",
            source_measure_column,
            clipped_measure_column,
        ]
        remaining = [
            column
            for column in chunk.columns
            if column not in canonical_first and column != "geometry"
        ]
        chunk = chunk[canonical_first + remaining + ["geometry"]]
        _write_chunk(output_path, output_layer, chunk)

        for (feature_class, permanency), group in chunk.groupby(
            ["feature_class", "permanency_class"],
            dropna=False,
        ):
            summary = summaries[(str(feature_class), str(permanency))]
            summary["feature_count"] += len(group)
            summary["source_measure"] += float(group[source_measure_column].sum())
            summary["clipped_measure"] += float(group[clipped_measure_column].sum())

        if preview_count < PREVIEW_FEATURE_LIMIT:
            sample = chunk.head(PREVIEW_FEATURE_LIMIT - preview_count).copy()
            preview_chunks.append(sample)
            preview_count += len(sample)

    summary_rows: list[dict[str, Any]] = []
    for (feature_class, permanency), values in sorted(summaries.items()):
        row: dict[str, Any] = {
            "geometry_family": geometry_family,
            "feature_class": feature_class,
            "permanency": permanency,
            "feature_count": int(values["feature_count"]),
        }
        if geometry_family == "polygon":
            row["source_area_km2"] = values["source_measure"]
            row["study_area_area_km2"] = values["clipped_measure"]
        else:
            row["source_length_km"] = values["source_measure"]
            row["study_area_length_km"] = values["clipped_measure"]
        summary_rows.append(row)

    if preview_chunks:
        preview = gpd.GeoDataFrame(
            pd.concat(preview_chunks, ignore_index=True),
            geometry="geometry",
            crs=output_crs,
        )
    else:
        preview = gpd.GeoDataFrame(
            {"feature_class": [], "geometry": []},
            geometry="geometry",
            crs=output_crs,
        )
    return summary_rows, preview


def run_hydrography_build(
    config: GeospatialBuildConfig,
    *,
    registry_path: Path | str | None = None,
    output_dir: Path = PROCESSED_NHN,
) -> dict[str, Path]:
    """Build selected, clipped, and documented Silver NHN feature layers."""

    if not config.hydrography.enabled:
        print("Hydrography stage disabled by the build profile.")
        return {}

    registry = (
        GeospatialSourceRegistry(registry_path)
        if registry_path is not None
        else GeospatialSourceRegistry()
    )
    source = registry.get(config.hydrography.source_id)
    source_path = registry.resolve_bronze_path(config.hydrography.source_id)
    metadata_path = registry.resolve_metadata_path(config.hydrography.source_id)
    acquisition_manifest_path = registry.resolve_acquisition_manifest_path(
        config.hydrography.source_id
    )
    if not source_path.is_file():
        raise FileNotFoundError(f"Registered Bronze hydrography not found: {source_path}")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Registered hydrography metadata not found: {metadata_path}")
    if not acquisition_manifest_path.is_file():
        raise FileNotFoundError(
            "Registered hydrography acquisition manifest not found: "
            f"{acquisition_manifest_path}"
        )

    boundary_path = find_study_area_boundary(config)
    boundary = gpd.read_file(boundary_path, engine="pyogrio").to_crs(
        config.hydrography.output_crs
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{config.study_area.label}_filtered_hydrography.gpkg"
    summary_path = output_dir / f"{config.study_area.label}_hydrography_summary.csv"
    manifest_path = output_dir / f"{config.study_area.label}_hydrography_manifest.json"
    output_path.unlink(missing_ok=True)

    water_domain = registry.domain(config.hydrography.source_id, "water_definition")
    permanence_domain = registry.domain(config.hydrography.source_id, "permanency")
    all_class_codes = water_domain["values"]
    all_permanency_codes = permanence_domain["values"]
    selected_permanency = {
        name: all_permanency_codes[name]
        for name in config.hydrography.permanency
    }
    source_crs = registry.artifact(
        config.hydrography.source_id,
        source["bronze"]["primary_artifact"],
    )["source_crs"]
    summary_rows: list[dict[str, Any]] = []
    outputs: dict[str, Path] = {}

    families = (
        (
            "polygons",
            config.hydrography.polygons,
            "waterbody_polygons",
            "polygon",
            "waterbody_polygons",
        ),
        (
            "lines",
            config.hydrography.lines,
            "watercourse_lines",
            "line",
            "watercourse_lines",
        ),
    )
    for family_name, family, registry_layer_name, geometry_family, output_layer in families:
        if not family.enabled:
            continue
        layer = registry.layer(config.hydrography.source_id, registry_layer_name)
        class_codes = {name: all_class_codes[name] for name in family.classes}
        rows, preview = _process_family(
            source_path=source_path,
            source_id=config.hydrography.source_id,
            source_crs=source_crs,
            source_layer=layer["source_layer"],
            output_layer=output_layer,
            geometry_family=geometry_family,
            preserve_fields=layer["preserve_fields"],
            primary_key_field=layer["primary_key_field"],
            geometry_field=layer["geometry_field"],
            feature_id_field=layer["feature_id_field"],
            class_field=water_domain["field"],
            class_codes=class_codes,
            permanency_field=permanence_domain["field"],
            permanency_codes=selected_permanency,
            thresholds=family.minimum_source_measure,
            boundary_target=boundary,
            output_crs=config.hydrography.output_crs,
            study_area_label=config.study_area.label,
            output_path=output_path,
        )
        summary_rows.extend(rows)
        preview_path = preview_dir / f"{config.study_area.label}_{output_layer}.png"
        _plot_preview(
            preview,
            boundary,
            preview_path,
            f"{config.study_area.label}: {output_layer}",
            geometry_family,
        )
        outputs[f"{family_name}_preview"] = preview_path

    summary_columns = [
        "geometry_family",
        "feature_class",
        "permanency",
        "feature_count",
        "source_area_km2",
        "study_area_area_km2",
        "source_length_km",
        "study_area_length_km",
    ]
    pd.DataFrame(summary_rows).reindex(columns=summary_columns).to_csv(
        summary_path,
        index=False,
    )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_profile": str(config.source_path),
        "build_id": config.build_id,
        "study_area": config.study_area.label,
        "source_registry": str(registry.path),
        "source_id": config.hydrography.source_id,
        "bronze_path": str(source_path),
        "bronze_size_bytes": source_path.stat().st_size,
        "metadata_path": str(metadata_path),
        "bronze_acquisition_manifest": str(acquisition_manifest_path),
        "output_crs": config.hydrography.output_crs,
        "permanency": list(config.hydrography.permanency),
        "polygons": {
            "enabled": config.hydrography.polygons.enabled,
            "classes": list(config.hydrography.polygons.classes),
            "minimum_source_area_km2": config.hydrography.polygons.minimum_source_measure,
        },
        "lines": {
            "enabled": config.hydrography.lines.enabled,
            "classes": list(config.hydrography.lines.classes),
            "minimum_source_length_km": config.hydrography.lines.minimum_source_measure,
        },
        "outputs": {
            "gpkg": str(output_path) if output_path.exists() else None,
            "summary": str(summary_path),
            "preview_dir": str(preview_dir),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    outputs.update({"summary": summary_path, "manifest": manifest_path})
    if output_path.exists():
        outputs["gpkg"] = output_path
    return outputs


__all__ = [
    "PROCESSED_NHN",
    "find_study_area_boundary",
    "run_hydrography_build",
]
