"""Scalar spatial-impedance calculations for candidate pipeline edges.

This module contains the source-agnostic core used to convert intersections
between candidate pipeline edges and polygon factor layers into equivalent
distance penalties.  Acquisition, source provenance, configuration parsing,
and file export intentionally remain outside this module.

The calculation preserves physical edge distance and adds one pair of audit
columns for every configured layer::

    <layer>_intersection_km
    <layer>_penalty_km

The final effective distance is defined as::

    effective_distance_km = physical_distance_km + total_penalty_km

where each layer penalty is the sum of intersected kilometres multiplied by
that layer's non-negative additional-distance factor.  A factor of 9 therefore
makes one kilometre through a feature contribute nine penalty kilometres and
ten effective kilometres in total.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS


LAYER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ScalarPenaltyLayer:
    """One polygon layer contributing scalar penalties to pipeline edges.

    Exactly one of ``additional_distance_factor`` and ``factor_column`` must be
    provided.  A constant factor is applied uniformly after dissolving the
    layer, which prevents overlapping features within that layer from being
    counted more than once.  A factor column supports feature-specific values,
    such as population-density penalty classes; overlapping features in that
    form are intentionally additive.

    Parameters
    ----------
    name : str
        Stable lowercase identifier used to name output audit columns.
    features : geopandas.GeoDataFrame
        Polygon or multipolygon features with a defined CRS.
    additional_distance_factor : float | None
        Uniform additional-distance factor for the complete layer.
    factor_column : str | None
        Name of a numeric feature column containing non-negative factors.
    """

    name: str
    features: gpd.GeoDataFrame
    additional_distance_factor: float | None = None
    factor_column: str | None = None


def _validate_edges(
    edges: gpd.GeoDataFrame,
    edge_id_column: str,
    physical_distance_column: str,
) -> None:
    """Validate the candidate-edge contract required by the calculation."""

    if edges.crs is None:
        raise ValueError("Candidate pipeline edges must have a defined CRS.")
    if not edges.crs.is_projected:
        raise ValueError(
            "Candidate pipeline edges must use a projected CRS so intersection "
            "lengths can be measured."
        )

    missing_columns = {
        edge_id_column,
        physical_distance_column,
        edges.geometry.name,
    } - set(edges.columns)
    if missing_columns:
        raise ValueError(
            "Candidate pipeline edges are missing required columns: "
            f"{sorted(missing_columns)}"
        )

    if edges[edge_id_column].isna().any():
        raise ValueError(f"Edge identifier column {edge_id_column!r} contains nulls.")
    if edges[edge_id_column].duplicated().any():
        raise ValueError(f"Edge identifier column {edge_id_column!r} must be unique.")
    if edges.geometry.isna().any() or edges.geometry.is_empty.any():
        raise ValueError("Candidate pipeline edges contain missing or empty geometry.")
    if not edges.geometry.geom_type.isin(["LineString", "MultiLineString"]).all():
        raise ValueError(
            "Candidate pipeline edges must contain only LineString or "
            "MultiLineString geometry."
        )

    physical_distance = pd.to_numeric(
        edges[physical_distance_column],
        errors="coerce",
    ).to_numpy(dtype=float)
    if not np.isfinite(physical_distance).all() or (physical_distance <= 0).any():
        raise ValueError(
            f"Edge distance column {physical_distance_column!r} must contain "
            "finite positive values."
        )


def _validate_layer(layer: ScalarPenaltyLayer) -> None:
    """Validate one scalar penalty layer before spatial intersection."""

    if not LAYER_NAME_PATTERN.fullmatch(layer.name):
        raise ValueError(
            "Penalty layer names must start with a lowercase letter and contain "
            f"only lowercase letters, digits, or underscores: {layer.name!r}."
        )
    if layer.features.crs is None:
        raise ValueError(f"Penalty layer {layer.name!r} must have a defined CRS.")

    constant_factor = layer.additional_distance_factor
    factor_column_name = layer.factor_column
    has_constant = constant_factor is not None
    has_column = factor_column_name is not None
    if has_constant == has_column:
        raise ValueError(
            f"Penalty layer {layer.name!r} must define exactly one of "
            "additional_distance_factor and factor_column."
        )

    if constant_factor is not None:
        factor = float(constant_factor)
        if not np.isfinite(factor) or factor < 0:
            raise ValueError(
                f"Penalty layer {layer.name!r} has an invalid constant factor."
            )
    else:
        factor_column = str(factor_column_name)
        if factor_column not in layer.features.columns:
            raise ValueError(
                f"Penalty layer {layer.name!r} is missing factor column "
                f"{factor_column!r}."
            )
        factors = pd.to_numeric(
            layer.features[factor_column],
            errors="coerce",
        ).to_numpy(dtype=float)
        if not np.isfinite(factors).all() or (factors < 0).any():
            raise ValueError(
                f"Penalty layer {layer.name!r} factor column must contain "
                "finite non-negative values."
            )

    nonempty = layer.features.loc[
        layer.features.geometry.notna() & ~layer.features.geometry.is_empty
    ]
    if not nonempty.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError(
            f"Penalty layer {layer.name!r} must contain only Polygon or "
            "MultiPolygon geometry."
        )


def _crs_units_to_km(crs: CRS) -> float:
    """Return the multiplier converting one projected CRS unit to kilometres."""

    axis_info = getattr(crs, "axis_info", ())
    if not axis_info:
        raise ValueError("Projected CRS does not expose linear-unit metadata.")
    unit_to_metres = float(axis_info[0].unit_conversion_factor)
    if not np.isfinite(unit_to_metres) or unit_to_metres <= 0:
        raise ValueError("Projected CRS has an invalid linear-unit conversion.")
    return unit_to_metres / 1000.0


def _prepare_factor_features(
    layer: ScalarPenaltyLayer,
    target_crs: CRS,
) -> gpd.GeoDataFrame:
    """Project and normalize one layer to geometry plus a scalar factor."""

    features = layer.features.to_crs(target_crs)
    features = features.loc[
        features.geometry.notna() & ~features.geometry.is_empty
    ].copy()

    if features.empty:
        return gpd.GeoDataFrame(
            {"_factor": pd.Series(dtype=float)},
            geometry=gpd.GeoSeries([], crs=target_crs),
            crs=target_crs,
        )

    if layer.additional_distance_factor is not None:
        dissolved_geometry = features.geometry.union_all()
        return gpd.GeoDataFrame(
            {"_factor": [float(layer.additional_distance_factor)]},
            geometry=[dissolved_geometry],
            crs=target_crs,
        )

    factor_column = str(layer.factor_column)
    prepared = features[[factor_column, features.geometry.name]].copy()
    prepared = prepared.rename(columns={factor_column: "_factor"})
    prepared["_factor"] = pd.to_numeric(prepared["_factor"], errors="raise")
    return gpd.GeoDataFrame(
        prepared,
        geometry=features.geometry.name,
        crs=target_crs,
    )


def calculate_pipeline_effective_distance(
    edges: gpd.GeoDataFrame,
    layers: Sequence[ScalarPenaltyLayer],
    *,
    edge_id_column: str = "edge_region",
    physical_distance_column: str = "distance_km",
) -> gpd.GeoDataFrame:
    """Apply additive scalar polygon penalties to candidate pipeline edges.

    Parameters
    ----------
    edges : geopandas.GeoDataFrame
        Unique candidate edges with line geometry in a projected CRS and a
        positive physical-distance column expressed in kilometres.
    layers : Sequence[ScalarPenaltyLayer]
        Ordered polygon layers whose penalties are accumulated additively.
    edge_id_column : str, default="edge_region"
        Unique edge identifier used to aggregate overlay fragments.
    physical_distance_column : str, default="distance_km"
        Existing physical-distance column expressed in kilometres.

    Returns
    -------
    geopandas.GeoDataFrame
        Copy of ``edges`` with per-layer intersection and penalty columns plus
        ``physical_distance_km``, ``total_penalty_km``, and
        ``effective_distance_km``.  Input order and geometry are preserved.

    Notes
    -----
    Constant-factor layers are dissolved before intersection so overlapping
    polygons within one dataset do not double count exposure. Feature-specific
    factor layers remain additive, making any overlap explicit in the result.
    """

    _validate_edges(edges, edge_id_column, physical_distance_column)

    layer_names = [layer.name for layer in layers]
    if len(layer_names) != len(set(layer_names)):
        raise ValueError("Penalty layer names must be unique.")
    for layer in layers:
        _validate_layer(layer)

    output_columns = {
        "physical_distance_km",
        "total_penalty_km",
        "effective_distance_km",
    }
    for name in layer_names:
        output_columns.update(
            {f"{name}_intersection_km", f"{name}_penalty_km"}
        )
    conflicts = sorted(output_columns & set(edges.columns))
    if conflicts:
        raise ValueError(
            "Candidate pipeline edges already contain generated output columns: "
            f"{conflicts}"
        )

    result = edges.copy()
    result["physical_distance_km"] = pd.to_numeric(
        result[physical_distance_column],
        errors="raise",
    ).astype(float)
    result["total_penalty_km"] = 0.0

    edge_geometry = edges[[edge_id_column, edges.geometry.name]].copy()
    edge_crs = edges.crs
    if edge_crs is None:  # Defensive narrowing after _validate_edges.
        raise ValueError("Candidate pipeline edges must have a defined CRS.")
    unit_to_km = _crs_units_to_km(edge_crs)

    for layer in layers:
        intersection_column = f"{layer.name}_intersection_km"
        penalty_column = f"{layer.name}_penalty_km"
        result[intersection_column] = 0.0
        result[penalty_column] = 0.0

        factor_features = _prepare_factor_features(layer, edge_crs)
        if factor_features.empty:
            continue

        intersections = gpd.overlay(
            edge_geometry,
            factor_features,
            how="intersection",
            keep_geom_type=True,
        )
        if intersections.empty:
            continue

        intersections["_intersection_km"] = (
            intersections.geometry.length * unit_to_km
        )
        intersections["_penalty_km"] = (
            intersections["_intersection_km"] * intersections["_factor"]
        )
        summary = intersections.groupby(edge_id_column, sort=False).agg(
            _intersection_km=("_intersection_km", "sum"),
            _penalty_km=("_penalty_km", "sum"),
        )

        result[intersection_column] = (
            result[edge_id_column].map(summary["_intersection_km"]).fillna(0.0)
        )
        result[penalty_column] = (
            result[edge_id_column].map(summary["_penalty_km"]).fillna(0.0)
        )
        result["total_penalty_km"] += result[penalty_column]

    result["effective_distance_km"] = (
        result["physical_distance_km"] + result["total_penalty_km"]
    )
    return gpd.GeoDataFrame(result, geometry=edges.geometry.name, crs=edges.crs)
