"""Aggregate Silver impedance evidence onto resolution-specific graph edges."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pyogrio

from geocanoe.config import GeospatialBuildConfig
from geocanoe.geospatial.pipeline_impedance import (
    ScalarPenaltyLayer,
    calculate_pipeline_effective_distance,
)
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
PROCESSED_GRAPH = PROJECT_ROOT / "data_files" / "processed" / "graph"
PROCESSED_PIPELINE_IMPEDANCE = (
    PROJECT_ROOT / "data_files" / "processed" / "pipeline_impedance"
)
EVIDENCE_LAYER = "pipeline_impedance_polygons"
OUTPUT_LAYER = "pipeline_edge_impedance"
EVIDENCE_SPECS = {
    "aboriginal_lands": ("aboriginal_lands.gpkg", "applied_scalar"),
    "nhn_waterbodies": ("nhn_waterbodies.gpkg", "applied_scalar"),
    "population_exposure": ("population_exposure.gpkg", "applied_factor"),
}


def find_graph_edge_files(config: GeospatialBuildConfig) -> list[Path]:
    """Return graph-edge GeoPackages belonging to the configured study area."""

    pattern = f"{config.study_area.label}_basemap_*_graph_edges.gpkg"
    paths = sorted(PROCESSED_GRAPH.glob(pattern))
    if not paths:
        raise FileNotFoundError(
            f"No adjacency edge GeoPackages match {PROCESSED_GRAPH / pattern}."
        )
    return paths


def find_impedance_evidence(config: GeospatialBuildConfig) -> dict[str, Path]:
    """Resolve the three profile-specific Silver evidence GeoPackages."""

    evidence_dir = PROCESSED_PIPELINE_IMPEDANCE / config.build_id / "evidence"
    paths = {
        name: evidence_dir / filename
        for name, (filename, _factor_column) in EVIDENCE_SPECS.items()
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Pipeline edge impedance requires all three Silver evidence files; "
            f"missing: {missing}"
        )
    return paths


def load_impedance_layers(
    evidence_paths: dict[str, Path],
    *,
    build_id: str,
    study_area: str,
    penalty_profile: str,
) -> list[ScalarPenaltyLayer]:
    """Load and validate the three factor-bearing Silver polygon layers."""

    layers: list[ScalarPenaltyLayer] = []
    for name, (_filename, factor_column) in EVIDENCE_SPECS.items():
        try:
            path = evidence_paths[name]
        except KeyError as exc:
            raise ValueError(f"Missing evidence path for {name!r}.") from exc
        available_layers = {layer for layer, _kind in pyogrio.list_layers(path)}
        if EVIDENCE_LAYER not in available_layers:
            raise ValueError(
                f"Evidence file {path} has no {EVIDENCE_LAYER!r} layer."
            )
        features = gpd.read_file(path, layer=EVIDENCE_LAYER, engine="pyogrio")
        required = {
            factor_column,
            "build_id",
            "study_area",
            "penalty_profile",
            features.geometry.name,
        }
        missing = sorted(required - set(features.columns))
        if missing:
            raise ValueError(f"Evidence layer {name!r} is missing fields: {missing}")
        expected_values = {
            "build_id": build_id,
            "study_area": study_area,
            "penalty_profile": penalty_profile,
        }
        for field, expected in expected_values.items():
            values = set(features[field].dropna().astype(str))
            if values != {expected}:
                raise ValueError(
                    f"Evidence layer {name!r} {field} must be {expected!r}; "
                    f"found {sorted(values)}."
                )
        layers.append(
            ScalarPenaltyLayer(
                name=name,
                features=features,
                factor_column=factor_column,
            )
        )
    return layers


def calculate_edge_impedance(
    directed_edges: gpd.GeoDataFrame,
    layers: list[ScalarPenaltyLayer],
) -> gpd.GeoDataFrame:
    """Calculate one route per undirected pair and restore directed edge rows."""

    required = {
        "edge_region",
        "region_from",
        "region_to",
        "distance_km",
        directed_edges.geometry.name,
    }
    missing = sorted(required - set(directed_edges.columns))
    if missing:
        raise ValueError(f"Graph edges are missing required fields: {missing}")
    if directed_edges["edge_region"].duplicated().any():
        raise ValueError("Graph edge_region identifiers must be unique.")

    edges = directed_edges.copy()
    edges["undirected_edge"] = [
        "--".join(sorted((str(left), str(right))))
        for left, right in zip(
            edges["region_from"], edges["region_to"], strict=True
        )
    ]
    representatives = edges.drop_duplicates("undirected_edge", keep="first").copy()
    weighted = calculate_pipeline_effective_distance(
        representatives,
        layers,
        edge_id_column="undirected_edge",
        physical_distance_column="distance_km",
    )
    audit_columns = [
        column
        for column in weighted.columns
        if column.endswith("_intersection_km")
        or column.endswith("_penalty_km")
        or column in {
            "undirected_edge",
            "physical_distance_km",
            "total_penalty_km",
            "effective_distance_km",
        }
    ]
    audit = pd.DataFrame(weighted[audit_columns].drop(columns="geometry", errors="ignore"))
    output = edges.merge(
        audit,
        on="undirected_edge",
        how="left",
        validate="many_to_one",
    )
    if output["effective_distance_km"].isna().any():
        raise RuntimeError("One or more directed edges lack impedance results.")
    output["cost_distance_multiplier"] = (
        output["effective_distance_km"] / output["physical_distance_km"]
    )
    output["penalty_distance_fraction"] = (
        output["total_penalty_km"] / output["physical_distance_km"]
    )
    return gpd.GeoDataFrame(
        output,
        geometry=directed_edges.geometry.name,
        crs=directed_edges.crs,
    )


def _plot_preview(edges: gpd.GeoDataFrame, path: Path, *, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(11, 9))
    edges.plot(
        ax=axis,
        column="cost_distance_multiplier",
        cmap="magma_r",
        linewidth=0.45,
        legend=True,
        legend_kwds={"label": "Pipeline cost-distance multiplier"},
    )
    axis.set_title(title)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def run_pipeline_edge_impedance_build(
    config: GeospatialBuildConfig,
    *,
    graph_paths: list[Path] | None = None,
    evidence_paths: dict[str, Path] | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, object]:
    """Build resolution-dependent pipeline cost-distance weights for Ri-Rj edges."""

    if not config.pipeline_impedance.enabled:
        print("Pipeline edge impedance disabled by the build profile.")
        return {}
    resolved_graph_paths = graph_paths or find_graph_edge_files(config)
    resolved_evidence = evidence_paths or find_impedance_evidence(config)
    layers = load_impedance_layers(
        resolved_evidence,
        build_id=config.build_id,
        study_area=config.study_area.label,
        penalty_profile=config.pipeline_impedance.penalty_profile,
    )
    root = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else PROCESSED_PIPELINE_IMPEDANCE / config.build_id / "edges"
    )
    root.mkdir(parents=True, exist_ok=True)
    preview_dir = root / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, dict[str, str]] = {}
    summaries: list[dict[str, object]] = []
    for graph_path in resolved_graph_paths:
        edges = gpd.read_file(graph_path, layer="edges", engine="pyogrio")
        weighted = calculate_edge_impedance(edges, layers)
        prefix = graph_path.name.removesuffix("_graph_edges.gpkg")
        gpkg_path = root / f"{prefix}_pipeline_impedance_edges.gpkg"
        csv_path = root / f"{prefix}_pipeline_impedance_edges.csv"
        preview_path = preview_dir / f"{prefix}_pipeline_impedance_edges.png"
        gpkg_path.unlink(missing_ok=True)
        pyogrio.write_dataframe(weighted, gpkg_path, layer=OUTPUT_LAYER, driver="GPKG")
        weighted.drop(columns="geometry").to_csv(csv_path, index=False)
        _plot_preview(
            weighted,
            preview_path,
            title=(
                f"{config.study_area.label}: pipeline edge impedance "
                f"({float(weighted['resolution'].iloc[0]):g} "
                f"{weighted['resolution_unit'].iloc[0]})"
            ),
        )

        summary: dict[str, object] = {
            "graph_file": graph_path.name,
            "resolution": float(weighted["resolution"].iloc[0]),
            "resolution_unit": str(weighted["resolution_unit"].iloc[0]),
            "directed_edge_count": int(len(weighted)),
            "undirected_edge_count": int(weighted["undirected_edge"].nunique()),
            "physical_distance_km": float(weighted["physical_distance_km"].sum()),
            "total_penalty_km": float(weighted["total_penalty_km"].sum()),
            "effective_distance_km": float(weighted["effective_distance_km"].sum()),
            "mean_cost_distance_multiplier": float(
                weighted["cost_distance_multiplier"].mean()
            ),
            "maximum_cost_distance_multiplier": float(
                weighted["cost_distance_multiplier"].max()
            ),
            "penalized_edge_count": int(weighted["total_penalty_km"].gt(0).sum()),
        }
        for name in EVIDENCE_SPECS:
            summary[f"{name}_intersection_km"] = float(
                weighted[f"{name}_intersection_km"].sum()
            )
            summary[f"{name}_penalty_km"] = float(
                weighted[f"{name}_penalty_km"].sum()
            )
        summaries.append(summary)
        outputs[prefix] = {
            "gpkg": str(gpkg_path),
            "csv": str(csv_path),
            "preview": str(preview_path),
        }

    summary_path = root / "pipeline_edge_impedance_summary.csv"
    manifest_path = root / "pipeline_edge_impedance_manifest.json"
    pd.DataFrame(summaries).to_csv(summary_path, index=False)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_profile": str(config.source_path),
        "build_id": config.build_id,
        "study_area": config.study_area.label,
        "penalty_profile": config.pipeline_impedance.penalty_profile,
        "method": {
            "edge_geometry": "straight line between adjacent region centroids",
            "physical_distance": "existing graph distance_km (geodesic)",
            "intersection_distance": "projected line-polygon intersection length",
            "layer_combination": "additive equivalent-distance penalties",
            "cost_distance_multiplier": (
                "(physical_distance_km + total_penalty_km) / physical_distance_km"
            ),
            "directed_edge_treatment": (
                "calculate once per undirected Ri-Rj geometry and mirror to both "
                "directed graph edges"
            ),
        },
        "inputs": {
            "graph_edges": [str(path) for path in resolved_graph_paths],
            "evidence": {name: str(path) for name, path in resolved_evidence.items()},
        },
        "outputs": outputs,
        "summary": str(summary_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "outputs": outputs,
        "summary": summary_path,
        "manifest": manifest_path,
    }


__all__ = [
    "EVIDENCE_SPECS",
    "OUTPUT_LAYER",
    "calculate_edge_impedance",
    "find_graph_edge_files",
    "find_impedance_evidence",
    "load_impedance_layers",
    "run_pipeline_edge_impedance_build",
]
