"""Decode CANOE/TEMOA outputs and export an interactive Folium map.

This script preserves the run selection, database decoding, geospatial path
resolution, technology linkage, transport-edge decoding, and resolution-aware
parallel-corridor logic used by ``create_map.py``. Instead of writing static
Matplotlib figures, it writes a Leaflet/Folium HTML map with embedded geographic
data, layer controls, hover tooltips, and popups. Basemaps require no tile API;
Folium's JavaScript and CSS assets still load from CDNs.

The script is intended as a trial replacement renderer. It does not alter the
solved database or upstream geospatial products.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import json
import re
import sqlite3
import sys
import tomllib
from collections.abc import Sequence
from typing import TypeAlias
from urllib.error import URLError

import folium
from branca.element import Figure
from folium.plugins import Fullscreen, MeasureControl
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from rasterio.errors import RasterioError
from shapely.geometry import LineString
from xyzservices import providers

from geocanoe.paths import find_project_root
from geocanoe.schema import database

# =============================================================================
# Project import path
# =============================================================================

PROJECT_ROOT = find_project_root()


# =============================================================================
# Constants
# =============================================================================

MIN_TRANSPORT_FLOW = 1e-3
MIN_PROCESS_FLOW = 1e-3

PLOT_WEB_TILES = True
PLOT_ROAD_OVERLAY = False
PLOT_ROAD_EDGE_LAYER = False
PLOT_PARALLEL_TRANSPORT_ARCS = True

TechStyle: TypeAlias = tuple[pd.DataFrame, str, str, float]
PointStyle: TypeAlias = tuple[pd.DataFrame, str]


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class ProjectPaths:
    """Resolved filesystem paths for the project mapping workflow.

    Attributes
    ----------
    project_root : Path
        Root directory of the repository or active project workspace.
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model run outputs.
    data_files : Path
        Directory containing raw and processed model/geospatial input data.
    """

    project_root: Path
    output_root: Path
    data_files: Path


@dataclass(frozen=True)
class SelectedRun:
    """Selected CANOE/TEMOA output run and SQLite database.

    Attributes
    ----------
    run_dir : Path
        Timestamped model-run directory selected from ``output_files/``.
    db_path : Path
        SQLite database selected from within the model-run directory.
    """

    run_dir: Path
    db_path: Path


@dataclass(frozen=True)
class GeospatialPaths:
    """Resolved geospatial input and figure-output paths for a selected run.

    Attributes
    ----------
    basemap_stem : str
        Canonical basemap identifier inferred from the selected database or run
        folder name.
    node_path : Path
        Path to the graph-node polygon GeoPackage for the inferred basemap.
    edge_path : Path
        Path to the graph-edge CSV for the inferred basemap.
    basemap_path : Path
        Path to the processed basemap polygon GeoPackage.
    road_edge_gpkg_path : Path | None
        Optional path to the road-enabled graph-edge GeoPackage, if available.
    figure_dir : Path
        Directory where generated figures are saved.
    fig_stem : str
        Filename stem used for generated figure outputs.
    build_id : str | None
        Build-profile identity recorded in the schema manifest, if resolved
        from one.
    scenario_id : str | None
        Scenario identity recorded in the schema manifest, if resolved from
        one.
    fingerprint : str | None
        Schema fingerprint recorded in the schema manifest, if resolved from
        one.
    road_layer : str | None
        Road-network layer recorded in the schema manifest, if resolved from
        one.
    connection_method : str | None
        Road-connection method recorded in the schema manifest, if resolved
        from one.
    """

    basemap_stem: str
    node_path: Path
    edge_path: Path
    basemap_path: Path
    road_edge_gpkg_path: Path | None
    figure_dir: Path
    fig_stem: str
    build_id: str | None = None
    scenario_id: str | None = None
    fingerprint: str | None = None
    road_layer: str | None = None
    connection_method: str | None = None


@dataclass(frozen=True)
class PlotSpacing:
    """Resolution-aware plotting parameters for map readability.

    Attributes
    ----------
    diagnostic_mode : bool
        If ``True``, disables random node jitter so plotted locations remain
        exactly aligned with model-region coordinates.
    grid_res_deg : float
        Inferred spatial grid resolution in decimal degrees.
    jitter_deg : float
        Maximum random lon/lat offset applied to process-point markers.
    parallel_offset_m : float
        Offset distance, in metres, used to separate parallel transport arcs
        along the same corridor.
    parallel_max_offset_m : float
        Maximum allowed parallel transport-arc offset, in metres.
    """

    diagnostic_mode: bool
    grid_res_deg: float
    jitter_deg: float
    parallel_offset_m: float
    parallel_max_offset_m: float


@dataclass
class ModelTables:
    """CANOE/TEMOA tables required to build map layers.

    Attributes
    ----------
    flow_out : pd.DataFrame
        Output flow table used to derive process activity and transport flows.
    demand : pd.DataFrame
        Demand table used to identify and size gasoline demand markers.
    limit_capacity : pd.DataFrame
        Capacity-limit table used to locate available CO2 capture capacity.
    """

    flow_out: pd.DataFrame
    demand: pd.DataFrame
    limit_capacity: pd.DataFrame


@dataclass
class GeospatialData:
    """Loaded geospatial layers used to render model-output maps.

    Attributes
    ----------
    sites : gpd.GeoDataFrame
        Graph-node/model-region polygons indexed by region ID, with centroid
        longitude and latitude fields used for plotting.
    edges : pd.DataFrame
        Graph-edge table linking transport pseudo-regions to source and target
        model regions.
    basemap : gpd.GeoDataFrame
        Processed basemap polygons used as geographic context.
    road_edge_layer : gpd.GeoDataFrame | None
        Optional road-enabled graph-edge geometries used to show available
        road-connected corridors.
    lakes : gpd.GeoDataFrame | None
        Optional processed lake polygons associated with the active build.
    aboriginal_lands : gpd.GeoDataFrame | None
        Optional processed Aboriginal Lands polygons associated with the build.
    urban_centres : gpd.GeoDataFrame | None
        Optional processed population-centre footprints associated with the build.
    """

    sites: gpd.GeoDataFrame
    edges: pd.DataFrame
    basemap: gpd.GeoDataFrame
    road_edge_layer: gpd.GeoDataFrame | None
    lakes: gpd.GeoDataFrame | None = None
    aboriginal_lands: gpd.GeoDataFrame | None = None
    urban_centres: gpd.GeoDataFrame | None = None


@dataclass
class PlotLayers:
    """Prepared plotting layers derived from model and geospatial tables.

    Attributes
    ----------
    tech_points : dict[str, POINT_STYLE]
        Node-level process layers keyed by display name. Each value contains the
        point DataFrame and its plotting color.
    tech_links : dict[str, TECH_STYLE]
        Transport-flow layers keyed by display name. Each value contains the link
        DataFrame, plotting color, line style, and width factor.
    demand_pts : pd.DataFrame
        Demand-point table containing coordinates and demand magnitudes.
    size_demand : pd.Series
        Marker sizes derived from demand magnitudes for plotting.
    """

    tech_points: dict[str, PointStyle]
    tech_links: dict[str, TechStyle]
    demand_pts: pd.DataFrame
    size_demand: pd.Series


# =============================================================================
# Project and run selection
# =============================================================================

def resolve_project_paths() -> ProjectPaths:
    """Resolve repository paths required by the mapping workflow.

    Returns
    -------
    ProjectPaths
        Repository root, model-output directory, and project data directory.
    """

    project_root = find_project_root()

    return ProjectPaths(
        project_root=project_root,
        output_root=project_root / "output_files",
        data_files=project_root / "data_files",
    )


def select_model_run(output_root: Path) -> Path:
    """Prompt the user to select a model-run directory.

    Searches ``output_root`` for subdirectories containing at least one SQLite
    database, prints the valid options, and returns the directory selected by
    index.

    Parameters
    ----------
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model-run outputs.

    Returns
    -------
    Path
        Selected model-run directory.

    Raises
    ------
    SystemExit
        If no valid run directories are found or if the selected index is
        invalid.
    """
    runs = sorted(
        run_dir
        for run_dir in output_root.iterdir()
        if run_dir.is_dir() and any(run_dir.glob("*.sqlite"))
    )

    if not runs:
        print(f"No valid runs found in {output_root.resolve()}")
        sys.exit(1)

    print("\nAvailable model runs:")
    for index, run_dir in enumerate(runs):
        sqlite_files = sorted(run_dir.glob("*.sqlite"))
        print(f"  [{index}] {run_dir.name}  ({len(sqlite_files)} sqlite file(s))")

    selected_index = input("\nSelect run index: ").strip()

    try:
        return runs[int(selected_index)]
    except (ValueError, IndexError):
        print("Invalid selection.")
        sys.exit(1)


def select_sqlite_database(run_dir: Path) -> Path:
    """Select a SQLite database from a model-run directory.

    If the run directory contains one SQLite database, that file is selected
    automatically. If multiple SQLite databases are present, prints the
    available files with their sizes and prompts the user to select one by
    index.

    Parameters
    ----------
    run_dir : Path
        Model-run directory containing one or more SQLite database files.

    Returns
    -------
    Path
        Selected SQLite database path.

    Raises
    ------
    SystemExit
        If multiple database files are available and the selected index is
        invalid.
    """

    sqlite_files = sorted(run_dir.glob("*.sqlite"))

    if len(sqlite_files) == 1:
        db_path = sqlite_files[0]
    else:
        print("\nAvailable SQLite files:")
        for index, sqlite_path in enumerate(sqlite_files):
            size_mb = sqlite_path.stat().st_size / 1e6
            print(f"  [{index}] {sqlite_path.name}  ({size_mb:.1f} MB)")

        selected_index = input("\nSelect database index: ").strip()

        try:
            db_path = sqlite_files[int(selected_index)]
        except (ValueError, IndexError):
            print("Invalid database selection.")
            sys.exit(1)

    print(f"  Selected database: {db_path}")
    return db_path


def select_run_and_database(output_root: Path) -> SelectedRun:
    """Select a model run and SQLite database for mapping.

    Prompts the user to choose a model-run directory from ``output_root``,
    then selects the SQLite database to read from that run. The selected paths
    are returned as a ``SelectedRun`` container.

    Parameters
    ----------
    output_root : Path
        Directory containing timestamped CANOE/TEMOA model-run outputs.

    Returns
    -------
    SelectedRun
        Selected model-run directory and SQLite database path.

    Raises
    ------
    SystemExit
        If no valid run is found or if the user makes an invalid selection.
    """

    run_dir = select_model_run(output_root)
    db_path = select_sqlite_database(run_dir)
    return SelectedRun(run_dir=run_dir, db_path=db_path)


# =============================================================================
# Path inference and validation
# =============================================================================

def infer_basemap_stem(db_path: Path) -> str:
    """Infer the canonical basemap stem from a database or run-folder name.

    Searches the selected SQLite database filename first, then the parent run
    directory name, for a basemap identifier matching the expected CANOE
    geospatial naming convention. Common database prefixes such as
    ``input_``, ``solved_``, and ``CANOE_geospatial_`` are ignored before
    matching.

    Parameters
    ----------
    db_path : Path
        Path to the selected CANOE/TEMOA SQLite database.

    Returns
    -------
    str
        Matched basemap stem, such as
        ``provinces_only_basemap_75km_centroid`` or
        ``canada_basemap_0.5deg_intersects``.

    Raises
    ------
    SystemExit
        If no valid basemap stem can be inferred from the database filename or
        parent run-folder name.
    """

    search_texts = [
        db_path.stem,
        db_path.parent.name,
    ]

    for text in search_texts:
        text = re.sub(r"^(input_|solved_)", "", text)
        text = re.sub(r"^CANOE_geospatial_", "", text)

        match = re.search(
            r"[A-Za-z0-9_]+_basemap_"
            r"\d+(?:\.\d+)?(?:deg|km)_"
            r"(?:centroid|intersects)",
            text,
        )

        if match:
            return match.group(0)

    print("Could not infer basemap stem.")
    print(f"  Database filename: {db_path.stem}")
    print(f"  Run folder:        {db_path.parent.name}")
    sys.exit(1)


def sanitize_run_label(value: str, max_length: int = 48) -> str:
    """Return a short filesystem-safe run label.

    The label is normalized to lowercase snake_case and capped so map artifacts
    do not recreate Windows path-length problems when they are written inside an
    already descriptive timestamped run directory.
    """

    label = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower())
    label = re.sub(r"_+", "_", label).strip("_.-")
    return (label or "run")[:max_length].rstrip("_.-")


def infer_short_run_label_from_database(db_path: Path) -> str:
    """Infer a compact study-area/resolution label from a database filename.

    Example
    -------
    ``solved_CANOE_geospatial_on_qc_basemap_25km_centroid_freight_access_strong``
    becomes ``on_qc_25km``.
    """

    text = db_path.stem
    text = re.sub(r"^(?:input_|working_|solved_)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^CANOE_geospatial_", "", text, flags=re.IGNORECASE)

    match = re.search(
        r"(?P<study>[A-Za-z0-9_]+?)_basemap_"
        r"(?P<resolution>\d+(?:\.\d+)?(?:deg|km))_",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return sanitize_run_label(
            f"{match.group('study')}_{match.group('resolution')}"
        )

    return "run"


def build_figure_stem(selected_run: SelectedRun) -> str:
    """Build a concise map-artifact stem for the selected model run.

    Preference order:

    1. Read ``scenario`` from the archived effective TEMOA configuration in the
       selected run directory. This keeps map names aligned with the solver run
       name rather than the much longer encoded database filename.
    2. Fall back to ``<study_area>_<resolution>`` inferred from the database
       filename, for example ``on_qc_25km``.

    The final stem is sanitized and length-capped for Windows path safety.
    """

    effective_configs = sorted(selected_run.run_dir.glob("effective_*.toml"))

    for config_path in effective_configs:
        try:
            with config_path.open("rb") as config_file:
                config = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError):
            continue

        scenario = config.get("scenario")
        if isinstance(scenario, str) and scenario.strip():
            return sanitize_run_label(scenario)

    return infer_short_run_label_from_database(selected_run.db_path)


def find_schema_manifest(db_path: Path, data_files: Path) -> Path | None:
    """Locate the schema-build manifest for a selected database, if any.

    Gold schemas are built with a sidecar ``<schema>.manifest.json`` recording
    the exact basemap, road layer, and connection method used to build them
    (``write_schema_manifest`` in ``geocanoe.schema.build``). Model-run
    archiving copies only the ``.sqlite`` file into the timestamped output run
    directory, so the manifest is also looked up back in
    ``data_files/processed/schema`` when it isn't found beside the selected
    database.
    """

    candidate = db_path.with_suffix(".manifest.json")
    if candidate.exists():
        return candidate

    stripped = re.sub(r"^(input_|working_|solved_)", "", db_path.stem)
    candidate = data_files / "processed" / "schema" / f"{stripped}.manifest.json"
    return candidate if candidate.exists() else None


def is_gold_schema_stem(stem: str) -> bool:
    """Return whether a database stem uses the ``gold_*`` naming convention."""

    return re.match(r"^(?:input_|working_|solved_)?gold_", stem) is not None


def read_region_basemap_stem(db_path: Path) -> str | None:
    """Recover a basemap stem from a Gold database's ``Region.notes`` column.

    ``build_canonical_links`` in ``geocanoe.schema.build`` writes every
    ``Region.notes`` value as ``"{basemap_stem} CANOE geospatial graph
    node"``. This is a degraded fallback used only when a schema's manifest is
    missing, since the road layer and connection method cannot be recovered
    this way.
    """

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT notes FROM Region LIMIT 1;").fetchone()

    if not row or not row[0]:
        return None

    match = re.match(r"^(.+) CANOE geospatial graph node$", row[0])
    return match.group(1) if match else None


def build_geospatial_paths_from_manifest(
    manifest_path: Path,
    selected_run: SelectedRun,
) -> GeospatialPaths:
    """Construct and validate geospatial paths from a schema-build manifest."""

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved = payload["resolved_gold_configuration"]
    artifact = payload.get("artifact", {})

    basemap_stem = resolved["basemap_stem"]
    node_path = Path(resolved["graph_node_path"])
    edge_path = Path(resolved["graph_edge_path"])
    basemap_path = Path(resolved["basemap_path"])
    road_edge_gpkg_path = Path(resolved["road_edges_gpkg_path"])

    required_paths = {
        "Graph nodes": node_path,
        "Graph edges": edge_path,
        "Processed basemap": basemap_path,
    }
    missing_paths = {
        name: path for name, path in required_paths.items() if not path.exists()
    }
    if missing_paths:
        print(f"Missing required geospatial files recorded in manifest '{manifest_path}':")
        for name, path in missing_paths.items():
            print(f"  {name}: {path} (exists: {path.exists()})")
        sys.exit(1)

    if not road_edge_gpkg_path.exists():
        road_edge_gpkg_path = None

    figure_dir = selected_run.db_path.parent
    figure_dir.mkdir(parents=True, exist_ok=True)

    print(f"Resolved basemap from schema manifest: {manifest_path}")

    return GeospatialPaths(
        basemap_stem=basemap_stem,
        node_path=node_path,
        edge_path=edge_path,
        basemap_path=basemap_path,
        road_edge_gpkg_path=road_edge_gpkg_path,
        figure_dir=figure_dir,
        fig_stem=build_figure_stem(selected_run),
        build_id=artifact.get("build_id"),
        scenario_id=artifact.get("scenario_id"),
        fingerprint=artifact.get("fingerprint"),
        road_layer=resolved.get("road_layer"),
        connection_method=resolved.get("connection_method"),
    )


def build_geospatial_paths_from_basemap_stem(
    basemap_stem: str,
    data_files: Path,
    selected_run: SelectedRun,
) -> GeospatialPaths:
    """Construct and validate geospatial paths for a known basemap stem."""

    graph_dir = data_files / "processed" / "graph"
    basemap_dir = data_files / "processed" / "basemaps"
    road_connectivity_dir = data_files / "processed" / "road_connectivity"

    node_path = graph_dir / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = graph_dir / f"{basemap_stem}_graph_edges.csv"
    basemap_path = basemap_dir / f"{basemap_stem}.gpkg"

    road_edge_candidates = sorted(
        road_connectivity_dir.glob(
            f"{basemap_stem}_road_connectivity_*_road_edges.gpkg"
        )
    )

    road_edge_gpkg_path = road_edge_candidates[0] if road_edge_candidates else None

    required_paths = {
        "Graph nodes": node_path,
        "Graph edges": edge_path,
        "Processed basemap": basemap_path,
    }

    missing_paths = {
        name: path for name, path in required_paths.items() if not path.exists()
    }
    if missing_paths:
        print(f"Missing required geospatial files for basemap '{basemap_stem}':")
        for name, path in missing_paths.items():
            print(f"  {name}: {path} (exists: {path.exists()})")
        sys.exit(1)

    figure_dir = selected_run.db_path.parent
    figure_dir.mkdir(parents=True, exist_ok=True)

    return GeospatialPaths(
        basemap_stem=basemap_stem,
        node_path=node_path,
        edge_path=edge_path,
        basemap_path=basemap_path,
        road_edge_gpkg_path=road_edge_gpkg_path,
        figure_dir=figure_dir,
        fig_stem=build_figure_stem(selected_run),
    )


def infer_geospatial_paths(
    data_files: Path,
    selected_run: SelectedRun,
) -> GeospatialPaths:
    """Infer and validate geospatial paths for a selected model run.

    Resolution is tried in order of decreasing fidelity:

    1. A schema-build manifest (``find_schema_manifest``), which records the
       exact basemap, road layer, and connection method used to build a
       ``gold_*`` schema. This is the only tier that can distinguish between
       multiple basemap variants compatible with the same build profile.
    2. For a ``gold_*`` database with no manifest, the basemap stem recorded
       in its ``Region.notes`` column (``read_region_basemap_stem``) is used
       to reconstruct paths by convention.
    3. The legacy ``CANOE_geospatial_*`` filename convention
       (``infer_basemap_stem``), for schemas built before the manifest
       existed.

    Optional road-overlay and road-enabled edge layers are discovered when
    matching files are available. Figure outputs are saved beside the
    selected database.

    Parameters
    ----------
    data_files : Path
        Project ``data_files`` directory containing processed geospatial inputs.
    selected_run : SelectedRun
        Selected model-run directory and SQLite database path.

    Returns
    -------
    GeospatialPaths
        Resolved required geospatial input paths, optional road-context paths,
        and figure-output naming metadata.

    Raises
    ------
    SystemExit
        If the basemap stem cannot be inferred or if any required geospatial
        input file is missing.
    """

    manifest_path = find_schema_manifest(selected_run.db_path, data_files)
    if manifest_path is not None:
        paths = build_geospatial_paths_from_manifest(manifest_path, selected_run)
    else:
        basemap_stem = None
        if is_gold_schema_stem(selected_run.db_path.stem):
            print(
                f"No schema manifest found for '{selected_run.db_path.name}'; "
                "recovering basemap identity from the database's Region table."
            )
            basemap_stem = read_region_basemap_stem(selected_run.db_path)

        if basemap_stem is None:
            basemap_stem = infer_basemap_stem(selected_run.db_path)

        paths = build_geospatial_paths_from_basemap_stem(
            basemap_stem, data_files, selected_run
        )

    print(f"\nInferred basemap stem: {paths.basemap_stem}")
    print(f"  Graph nodes:       {paths.node_path.name}")
    print(f"  Graph edges:       {paths.edge_path.name}")
    print(f"  Basemap polygons:  {paths.basemap_path.name}")
    print(
        "  Road edge layer:   "
        f"{paths.road_edge_gpkg_path.name if paths.road_edge_gpkg_path else 'not found'}"
    )

    return paths


# =============================================================================
# Data loading and spacing
# =============================================================================

def load_model_tables(db_path: Path) -> ModelTables:
    """Load CANOE/TEMOA tables required by the mapping workflow.

    Reads the selected SQLite database into DataFrames and extracts the output
    flow, demand, and capacity-limit tables used to construct process-point,
    demand-point, and transport-flow map layers.

    Parameters
    ----------
    db_path : Path
        Path to the selected CANOE/TEMOA SQLite database.

    Returns
    -------
    ModelTables
        Copies of the database tables required for map-layer preparation.

    Raises
    ------
    KeyError
        If the database does not contain ``OutputFlowOut``, ``Demand``, or
        ``LimitCapacity``.
    """

    db_tables = database.sqlite_to_dfs(str(db_path))

    return ModelTables(
        flow_out=db_tables["OutputFlowOut"].copy(),
        demand=db_tables["Demand"].copy(),
        limit_capacity=db_tables["LimitCapacity"].copy(),
    )


def _load_processed_context_layers(
    paths: GeospatialPaths,
) -> tuple[
    gpd.GeoDataFrame | None,
    gpd.GeoDataFrame | None,
    gpd.GeoDataFrame | None,
]:
    """Load compact display layers from the active build's Silver evidence.

    The pipeline-impedance evidence directory is build-profile specific, so a
    ``prov-orbits`` result cannot accidentally be decorated with context from
    ``prov-popfuel``. Urban footprints come from the population-centre source
    but are restricted to centre IDs present in that build's processed evidence.
    """

    if paths.build_id is None:
        print(
            "Processed lake, Aboriginal Lands, and urban-centre context skipped: "
            "the selected run has no build_id manifest metadata."
        )
        return None, None, None

    processed_root = paths.basemap_path.parent.parent
    evidence_dir = processed_root / "pipeline_impedance" / paths.build_id / "evidence"
    lake_path = evidence_dir / "nhn_waterbodies.gpkg"
    lakes = None
    if lake_path.exists():
        lakes = gpd.read_file(lake_path, layer="nhn_waterbody_features")
        if lakes.crs is None:
            raise ValueError(f"Processed lakes context has no assigned CRS: {lake_path}")
        lake_columns = [
            column
            for column in (
                "hydro_feature_id",
                "lakename_1",
                "feature_class",
                "permanency_class",
                lakes.geometry.name,
            )
            if column in lakes.columns
        ]
        lakes = lakes[lake_columns].copy()
    else:
        print(f"Processed lakes context unavailable: {lake_path}")

    lands_evidence_path = evidence_dir / "aboriginal_lands.gpkg"
    lands_source_path = (
        processed_root.parent
        / "raw"
        / "aboriginal_lands"
        / "AL_TA_CA_2_188_eng.shp"
    )
    aboriginal_lands = None
    if lands_evidence_path.exists() and lands_source_path.exists():
        land_ids = gpd.read_file(
            lands_evidence_path,
            layer="aboriginal_lands_features",
            columns=["source_feature_id"],
            ignore_geometry=True,
        )["source_feature_id"].dropna().astype(str).unique()
        aboriginal_lands = gpd.read_file(lands_source_path)
        aboriginal_lands["NID"] = aboriginal_lands["NID"].astype(str)
        aboriginal_lands = aboriginal_lands.loc[
            aboriginal_lands["NID"].isin(land_ids),
            [
                column
                for column in ("NID", "NAME1", "JUR1", "ALTYPE", "geometry")
                if column in aboriginal_lands.columns
            ],
        ].copy()
    else:
        print(
            "Processed Aboriginal Lands context unavailable: expected both "
            f"{lands_evidence_path} and {lands_source_path}"
        )

    urban_evidence_path = evidence_dir / "population_exposure.gpkg"
    urban_source_path = (
        processed_root.parent
        / "raw"
        / "gasoline_demand"
        / "population_centres"
        / "lpc_000b21a_e.shp"
    )
    urban_centres = None
    if urban_evidence_path.exists() and urban_source_path.exists():
        centre_ids = gpd.read_file(
            urban_evidence_path,
            layer="population_exposure_features",
            columns=["PCUID"],
            ignore_geometry=True,
        )["PCUID"].dropna().astype(str).unique()
        urban_centres = gpd.read_file(urban_source_path)
        urban_centres["PCUID"] = urban_centres["PCUID"].astype(str)
        urban_centres = urban_centres.loc[
            urban_centres["PCUID"].isin(centre_ids),
            [
                column
                for column in ("PCUID", "PCNAME", "PCTYPE", "PCCLASS", "geometry")
                if column in urban_centres.columns
            ],
        ].copy()
    else:
        print(
            "Processed urban-centre context unavailable: expected both "
            f"{urban_evidence_path} and {urban_source_path}"
        )

    return lakes, aboriginal_lands, urban_centres


def load_geospatial_data(paths: GeospatialPaths) -> GeospatialData:
    """Load and normalize geospatial layers for mapping.

    Reads graph-node polygons, graph-edge records, processed basemap polygons,
    and the optional road-enabled edge layer from the resolved paths. Region
    identifiers are coerced to strings, graph nodes are indexed by ``region``,
    and all geospatial context layers are reprojected to match the graph-node
    CRS.

    Parameters
    ----------
    paths : GeospatialPaths
        Resolved geospatial input paths and optional road-context paths.

    Returns
    -------
    GeospatialData
        Loaded graph nodes, graph edges, basemap polygons, and optional
        road-enabled edge geometry prepared for downstream plotting.

    Raises
    ------
    FileNotFoundError
        If a required graph-node, graph-edge, or basemap file is missing.
    KeyError
        If required identifier columns are missing from the graph-node or
        graph-edge inputs.
    ValueError
        If any loaded geospatial layer has no assigned coordinate reference
        system.
    """

    sites = gpd.read_file(paths.node_path)
    edges = pd.read_csv(paths.edge_path)
    basemap = gpd.read_file(paths.basemap_path)

    road_edge_layer = None
    if (
        paths.road_edge_gpkg_path is not None
        and paths.road_edge_gpkg_path.exists()
    ):
        road_edge_layer = gpd.read_file(paths.road_edge_gpkg_path)

    if sites.crs is None:
        raise ValueError(
            f"Graph-node layer has no assigned CRS: {paths.node_path}"
        )

    if basemap.crs is None:
        raise ValueError(
            f"Basemap layer has no assigned CRS: {paths.basemap_path}"
        )

    if road_edge_layer is not None and road_edge_layer.crs is None:
        raise ValueError(
            "Road-enabled edge layer has no assigned CRS: "
            f"{paths.road_edge_gpkg_path}"
        )

    target_crs = sites.crs

    sites["region"] = sites["region"].astype(str)
    sites["site_id"] = sites["region"]
    sites = sites.set_index("region", drop=True)

    edges["edge_region"] = edges["edge_region"].astype(str)
    edges["region_from"] = edges["region_from"].astype(str)
    edges["region_to"] = edges["region_to"].astype(str)

    if basemap.crs != target_crs:
        basemap = basemap.to_crs(target_crs)

    if (
        road_edge_layer is not None
        and road_edge_layer.crs != target_crs
    ):
        road_edge_layer = road_edge_layer.to_crs(target_crs)

    lakes, aboriginal_lands, urban_centres = _load_processed_context_layers(paths)

    return GeospatialData(
        sites=sites,
        edges=edges,
        basemap=basemap,
        road_edge_layer=road_edge_layer,
        lakes=lakes,
        aboriginal_lands=aboriginal_lands,
        urban_centres=urban_centres,
    )


def build_point_geodataframe(
    frame: pd.DataFrame,
    target_crs,
    lon_col: str = "lon",
    lat_col: str = "lat",
) -> gpd.GeoDataFrame:
    """Create point geometries from longitude and latitude coordinates.

    The input table is copied, the configured coordinate columns are converted to
    numeric values, and rows with missing or non-numeric coordinates are removed.
    The remaining coordinates are interpreted as WGS84 longitude and latitude,
    converted to point geometry, and reprojected to ``target_crs``.

    Parameters
    ----------
    frame : pd.DataFrame
        Source table containing longitude and latitude columns.
    target_crs
        Coordinate reference system to which the point geometries are reprojected.
        Accepts any CRS representation supported by GeoPandas.
    lon_col : str, default="lon"
        Name of the longitude column in ``frame``.
    lat_col : str, default="lat"
        Name of the latitude column in ``frame``.

    Returns
    -------
    gpd.GeoDataFrame
        Copy of the valid input rows with point geometry in ``target_crs``.
    """

    points = frame.copy()
    points[lon_col] = pd.to_numeric(points[lon_col], errors="coerce")
    points[lat_col] = pd.to_numeric(points[lat_col], errors="coerce")
    points = points.dropna(subset=[lon_col, lat_col]).copy()

    return gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points[lon_col], points[lat_col]),
        crs="EPSG:4326",
    ).to_crs(target_crs)


def native_plot_crs(geodata: GeospatialData):
    """Return the coordinate reference system used for native-coordinate plotting.

    The graph-node layer defines the native spatial reference for model-region
    geometry and associated map layers. This function validates that the node layer
    has an assigned coordinate reference system before returning it.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded geospatial layers containing the graph-node GeoDataFrame.

    Returns
    -------
    object
        Coordinate reference system assigned to ``geodata.sites``.

    Raises
    ------
    ValueError
        If the graph-node layer has no assigned coordinate reference system.
    """

    if geodata.sites.crs is None:
        raise ValueError("Graph-node layer has no CRS.")
    return geodata.sites.crs


def infer_degree_resolution(basemap_stem: str) -> float | None:
    """Infer grid resolution in decimal degrees from a basemap stem.

    Parses basemap names containing tokens such as ``_1deg_`` or
    ``_0.5deg_`` and returns the numeric grid resolution. If no degree
    resolution token is found, returns ``None`` so callers can use a fallback
    spacing method.

    Parameters
    ----------
    basemap_stem : str
        Basemap identifier, such as ``canada_basemap_1deg_intersects`` or
        ``canada_basemap_0.5deg_centroid``.

    Returns
    -------
    float | None
        Parsed grid resolution in decimal degrees, or ``None`` if the naming
        pattern does not contain a degree-resolution token.
    """

    match = re.search(r"_(\d+(?:\.\d+)?)deg(?:_|$)", basemap_stem)
    return float(match.group(1)) if match else None


def infer_centroid_spacing_deg(sites_gdf: gpd.GeoDataFrame) -> float:
    """Estimate representative graph-centroid spacing in decimal degrees.

    The function uses existing ``lon`` and ``lat`` columns when available.
    Otherwise, it computes geometry centroids in the layer's native CRS and
    reprojects them to WGS84. Representative longitudinal and latitudinal spacing
    is calculated from the median positive difference between unique centroid
    coordinates, and the smaller valid spacing is returned.

    Parameters
    ----------
    sites_gdf : gpd.GeoDataFrame
        Graph-node layer containing centroid coordinates or polygon geometries.

    Returns
    -------
    float
        Estimated representative centroid spacing in decimal degrees. Returns
        ``1.0`` when no positive finite spacing can be inferred.

    Raises
    ------
    ValueError
        If ``sites_gdf`` has no assigned coordinate reference system.
    """

    if sites_gdf.crs is None:
        raise ValueError(
            "Cannot infer plot spacing from a layer without a CRS."
        )

    if {"lon", "lat"}.issubset(sites_gdf.columns):
        lon_vals = np.sort(
            pd.to_numeric(
                sites_gdf["lon"],
                errors="coerce",
            ).dropna().unique()
        )
        lat_vals = np.sort(
            pd.to_numeric(
                sites_gdf["lat"],
                errors="coerce",
            ).dropna().unique()
        )
    else:
        centroids = sites_gdf.geometry.centroid
        centroid_gdf = gpd.GeoDataFrame(
            geometry=centroids,
            crs=sites_gdf.crs,
        ).to_crs(epsg=4326)

        lon_vals = np.sort(centroid_gdf.geometry.x.unique())
        lat_vals = np.sort(centroid_gdf.geometry.y.unique())

    lon_diffs = np.diff(lon_vals)
    lat_diffs = np.diff(lat_vals)

    lon_step = (
        np.median(lon_diffs[lon_diffs > 1e-9])
        if np.any(lon_diffs > 1e-9)
        else np.nan
    )
    lat_step = (
        np.median(lat_diffs[lat_diffs > 1e-9])
        if np.any(lat_diffs > 1e-9)
        else np.nan
    )

    candidates = [
        float(abs(step))
        for step in (lon_step, lat_step)
        if np.isfinite(step) and abs(step) > 0
    ]

    return min(candidates) if candidates else 1.0


def configure_resolution_aware_spacing(
    basemap_stem: str,
    sites_gdf: gpd.GeoDataFrame,
    diagnostic_mode: bool = False,
) -> PlotSpacing:
    """Configure plotting offsets from the inferred spatial resolution.

    Infers the model grid resolution from the basemap stem, falling back to
    centroid-coordinate spacing when needed. The resulting resolution is used
    to scale point jitter and parallel transport-arc offsets so figures remain
    readable across different grid sizes.

    Parameters
    ----------
    basemap_stem : str
        Basemap identifier used to parse degree resolution when available.
    sites_gdf : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame containing centroid coordinate
        columns used for fallback spacing inference.
    diagnostic_mode : bool, default=False
        If ``True``, disables point jitter so plotted process nodes remain
        exactly aligned with their model-region coordinates.

    Returns
    -------
    PlotSpacing
        Resolution-aware plotting parameters for point jitter and parallel
        transport-arc offsets.
    """

    grid_res_deg = infer_degree_resolution(basemap_stem)
    if grid_res_deg is None:
        grid_res_deg = infer_centroid_spacing_deg(sites_gdf)

    if diagnostic_mode:
        jitter_deg = 0.0
    else:
        jitter_deg = 0.12 * grid_res_deg

    parallel_offset_m = 0.06 * grid_res_deg * 111_000
    parallel_max_offset_m = 0.25 * grid_res_deg * 111_000

    return PlotSpacing(
        diagnostic_mode=diagnostic_mode,
        grid_res_deg=grid_res_deg,
        jitter_deg=jitter_deg,
        parallel_offset_m=parallel_offset_m,
        parallel_max_offset_m=parallel_max_offset_m,
    )


def print_loaded_data_summary(
    tables: ModelTables,
    geodata: GeospatialData,
    spacing: PlotSpacing,
) -> None:
    """Print summary diagnostics for loaded mapping inputs.

    Reports resolution-aware plotting settings, model-output table size, graph
    and basemap layer sizes, and optional road-context layer counts. This is a
    console-only diagnostic used to confirm that the selected run and inferred
    geospatial inputs loaded as expected.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables used by the mapping workflow.
    geodata : GeospatialData
        Loaded graph, basemap, and optional road-context layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for jitter and transport-line
        offsets.

    Returns
    -------
    None
        This function only prints diagnostics to the console.
    """

    print("\nResolution-aware plot spacing:")
    print(f"  DIAGNOSTIC_MODE:        {spacing.diagnostic_mode}")
    print(f"  GRID_RES_DEG:           {spacing.grid_res_deg:,.4f}")
    print(f"  JITTER_DEG:             {spacing.jitter_deg:,.4f}")
    print(f"  PARALLEL_OFFSET_M:      {spacing.parallel_offset_m:,.0f}")
    print(f"  PARALLEL_MAX_OFFSET_M:  {spacing.parallel_max_offset_m:,.0f}")

    print(f"\nOutputFlowOut rows: {len(tables.flow_out):,}")
    print(f"Graph-node polygons: {len(geodata.sites):,}")
    print(f"Graph edges: {len(geodata.edges):,}")
    print(f"Basemap polygons: {len(geodata.basemap):,}")
    print(f"Native map CRS: {geodata.sites.crs}")
    print("Web map CRS: EPSG:3857")
    if geodata.road_edge_layer is not None:
        print(f"Road-enabled edge geometries: {len(geodata.road_edge_layer):,}")


# =============================================================================
# Layer preparation
# =============================================================================

def add_site_coords(
    frame: pd.DataFrame,
    sites: gpd.GeoDataFrame,
    idx_col: str = "region",
) -> pd.DataFrame:
    """Attach model-region centroid coordinates to a DataFrame.

    Copies the input table, indexes it by ``idx_col`` when needed, and joins
    ``lon`` and ``lat`` from the graph-node GeoDataFrame. Rows whose region IDs
    are not present in ``sites`` are retained with missing coordinate values.

    Parameters
    ----------
    frame : pd.DataFrame
        Table containing a model-region identifier column.
    sites : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame indexed by region ID and
        containing ``lon`` and ``lat`` columns.
    idx_col : str, default="region"
        Column in ``frame`` used to align rows with the ``sites`` index.

    Returns
    -------
    pd.DataFrame
        Copy of ``frame`` with joined ``lon`` and ``lat`` columns.
    """

    output_frame = frame.copy()
    if idx_col != output_frame.index.name:
        output_frame = output_frame.set_index(idx_col, drop=False)
    return output_frame.join(sites[["lon", "lat"]], how="left")


def slice_with_coords(
    flow_out: pd.DataFrame,
    sites: gpd.GeoDataFrame,
    tech_name: str,
) -> pd.DataFrame:
    """Build a coordinate-enriched process-flow layer for one technology.

    Filters ``OutputFlowOut`` to the requested node-level process technology,
    coerces flow values to numeric, removes flows below ``MIN_PROCESS_FLOW``,
    aggregates remaining flow by region and technology, and joins model-region
    centroid coordinates for plotting.

    Parameters
    ----------
    flow_out : pd.DataFrame
        CANOE/TEMOA ``OutputFlowOut`` table.
    sites : gpd.GeoDataFrame
        Graph-node/model-region GeoDataFrame indexed by region ID and
        containing ``lon`` and ``lat`` columns.
    tech_name : str
        Process technology name to extract from ``flow_out``.

    Returns
    -------
    pd.DataFrame
        Aggregated process-flow table with ``region``, ``tech``, ``flow``,
        ``lon``, and ``lat`` columns.
    """

    tech_flow = flow_out.loc[flow_out["tech"] == tech_name].copy()
    tech_flow["flow"] = pd.to_numeric(tech_flow["flow"], errors="coerce")
    tech_flow = tech_flow.loc[tech_flow["flow"] > MIN_PROCESS_FLOW].copy()

    tech_flow = (
        tech_flow
        .groupby(["region", "tech"], as_index=False)
        .agg(flow=("flow", "sum"))
    )

    return add_site_coords(tech_flow, sites, idx_col="region")


def add_from_to_coords(
    flow_links: pd.DataFrame,
    edges: pd.DataFrame,
) -> pd.DataFrame:
    """Attach source/target graph coordinates to transport-flow records.

    Filters transport-flow records to positive flows above ``MIN_TRANSPORT_FLOW``,
    joins each transport pseudo-region to the graph-edge table, drops links
    without valid endpoint coordinates, and aggregates duplicate flow records by
    edge and technology.

    Parameters
    ----------
    flow_links : pd.DataFrame
        Transport subset of ``OutputFlowOut`` where ``region`` identifies a
        graph-edge pseudo-region.
    edges : pd.DataFrame
        Graph-edge table containing ``edge_region``, endpoint region IDs, and
        endpoint coordinates.

    Returns
    -------
    pd.DataFrame
        Aggregated transport-flow table with source/target region IDs,
        endpoint coordinates, technology name, and total flow.
    """

    link_flow = flow_links.copy()
    link_flow["flow"] = pd.to_numeric(link_flow["flow"], errors="coerce")
    link_flow = link_flow.loc[link_flow["flow"] > MIN_TRANSPORT_FLOW].copy()

    link_flow = link_flow.merge(
        edges[[
            "edge_region",
            "region_from",
            "region_to",
            "lon_from",
            "lat_from",
            "lon_to",
            "lat_to",
        ]],
        left_on="region",
        right_on="edge_region",
        how="left",
        validate="many_to_one",
    )

    link_flow = link_flow.dropna(
        subset=["lon_from", "lat_from", "lon_to", "lat_to"]
    ).copy()
    if link_flow.empty:
        return link_flow

    group_cols = [
        "region",
        "tech",
        "edge_region",
        "region_from",
        "region_to",
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
    ]

    return (
        link_flow
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )


def build_node_layers(tables: ModelTables, geodata: GeospatialData) -> dict[str, PointStyle]:
    """Build node-level process and capacity layers for plotting.

    Creates coordinate-enriched point layers for electricity generation,
    hydrogen production, methanol production, gasoline production, and
    geological CO2 injection from ``OutputFlowOut``. CO2 capture is handled
    separately using positive ``CO2_CAP`` entries from ``LimitCapacity`` and is
    treated as a plotted capacity layer.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables containing output flows and capacity limits.
    geodata : GeospatialData
        Loaded graph-node geospatial data used to attach centroid coordinates.

    Returns
    -------
    dict[str, POINT_STYLE]
        Dictionary of point layers keyed by display name. Each value contains
        a coordinate-enriched DataFrame and its plotting color.
    """

    elc_gen = slice_with_coords(tables.flow_out, geodata.sites, "ELC_GEN")
    h2_plant = slice_with_coords(tables.flow_out, geodata.sites, "H2_PLANT")
    metoh_plant = slice_with_coords(tables.flow_out, geodata.sites, "METOH_PLANT")
    gsl_plant = slice_with_coords(tables.flow_out, geodata.sites, "GSL_PLANT")
    co2_storage = slice_with_coords(
        tables.flow_out,
        geodata.sites,
        "CO2_INJECT",
    )

    co2_cap = tables.limit_capacity.loc[
        tables.limit_capacity["tech_or_group"] == "CO2_CAP"
    ].copy()
    co2_cap["flow"] = pd.to_numeric(co2_cap["capacity"], errors="coerce")
    co2_cap = co2_cap.loc[co2_cap["flow"] > 0].copy()
    co2_cap = co2_cap.groupby("region", as_index=False).agg(flow=("flow", "sum"))
    co2_cap = add_site_coords(co2_cap, geodata.sites, idx_col="region")

    return {
        "Electricity": (elc_gen, "#0072B2"),
        "H2": (h2_plant, "#009E73"),
        "CO2": (co2_cap, "#777777"),
        "CO2 storage": (co2_storage, "#CC79A7"),
        "Methanol": (metoh_plant, "#E69F00"),
        "Gasoline": (gsl_plant, "#D55E00"),
    }


def build_transport_layers(
    flow_out: pd.DataFrame,
    edges: pd.DataFrame,
) -> dict[str, TechStyle]:
    """Build transport-flow layers and plotting styles.

    Extracts supported pipeline, truck, and electricity-transmission
    technologies from ``OutputFlowOut``, attaches graph-edge endpoint
    coordinates, and packages each layer with its display style. Solid lines
    represent pipeline/transmission modes, while dashed lines represent truck
    transport modes.

    Parameters
    ----------
    flow_out : pd.DataFrame
        CANOE/TEMOA ``OutputFlowOut`` table containing transport-flow records.
    edges : pd.DataFrame
        Graph-edge table used to map transport pseudo-regions to source and
        target model-region coordinates.

    Returns
    -------
    dict[str, TECH_STYLE]
        Dictionary of transport layers keyed by display name. Each value
        contains the transport-flow DataFrame, plotting color, line style, and
        width factor.
    """

    h2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_PIPE"], edges)
    h2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "H2_TRUCK"], edges)

    gsl_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_PIPE"], edges)
    gsl_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "GSL_TRUCK"], edges)

    meth_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_PIPE"], edges)
    meth_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "METOH_TRUCK"], edges)

    co2_pipe = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_PIPE"], edges)
    co2_truck = add_from_to_coords(flow_out.loc[flow_out.tech == "CO2_TRUCK"], edges)

    elc_trans = add_from_to_coords(flow_out.loc[flow_out.tech == "ELC_TRANS"], edges)

    return {
        "H2 pipeline": (h2_pipe, "#009E73", "-", 2.5),
        "H2 truck": (h2_truck, "#009E73", "--", 1.0),
        "CO2 pipeline": (co2_pipe, "#777777", "-", 2.5),
        "CO2 truck": (co2_truck, "#777777", "--", 1.0),
        "Methanol pipeline": (meth_pipe, "#E69F00", "-", 2.5),
        "Methanol truck": (meth_truck, "#E69F00", "--", 1.0),
        "Gasoline pipeline": (gsl_pipe, "#D55E00", "-", 2.5),
        "Gasoline truck": (gsl_truck, "#D55E00", "--", 1.0),
        "Electricity transmission": (elc_trans, "#0072B2", "-", 2.0),
    }


def build_demand_layer(tables: ModelTables, geodata: GeospatialData) -> tuple[pd.DataFrame, pd.Series]:
    """Build gasoline demand-point markers for plotting.

    Filters the ``Demand`` table to positive gasoline demand records
    identified by ``d_gsl``, attaches model-region centroid coordinates, and
    computes square-root-scaled marker sizes for map rendering.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables containing the demand table.
    geodata : GeospatialData
        Loaded graph-node geospatial data used to attach centroid coordinates.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series]
        Demand-point table with ``lon``, ``lat``, and ``demand`` columns, plus
        the corresponding marker-size series used for plotting.
    """

    demand_pts = tables.demand.loc[tables.demand["commodity"] == "d_gsl"].copy()
    demand_pts["demand"] = pd.to_numeric(demand_pts["demand"], errors="coerce")
    demand_pts = demand_pts.loc[demand_pts["demand"] > 0].copy()
    demand_pts = add_site_coords(demand_pts, geodata.sites, idx_col="region")

    # Preserve the model-region identifier for interactive Folium tooltips and
    # popups. The original static Matplotlib renderer only needed coordinates
    # and demand magnitude, but the interactive renderer also displays region.
    demand_columns = [
        column
        for column in ["region", "period", "lon", "lat", "demand"]
        if column in demand_pts.columns
    ]
    demand_pts = demand_pts[demand_columns].dropna(
        subset=["lon", "lat", "demand"]
    ).copy()

    if not demand_pts.empty:
        demand_pts["demand"] = demand_pts["demand"].astype(float)
        size_demand = np.sqrt(demand_pts["demand"] / demand_pts["demand"].max()) * 500
    else:
        size_demand = pd.Series(dtype=float)

    return demand_pts, size_demand


def build_plot_layers(tables: ModelTables, geodata: GeospatialData) -> PlotLayers:
    """Build all render-ready plotting layers.

    Orchestrates node-layer, transport-layer, and demand-layer construction
    from the loaded model tables and geospatial inputs. The returned container
    is used by downstream diagnostics and figure-building functions.

    Parameters
    ----------
    tables : ModelTables
        Loaded CANOE/TEMOA tables used to derive process, transport, and demand
        layers.
    geodata : GeospatialData
        Loaded geospatial graph and context data used to attach coordinates and
        edge topology.

    Returns
    -------
    PlotLayers
        Prepared node, transport, and demand layers ready for plotting.
    """

    tech_points = build_node_layers(tables, geodata)
    tech_links = build_transport_layers(tables.flow_out, geodata.edges)
    demand_pts, size_demand = build_demand_layer(tables, geodata)

    return PlotLayers(
        tech_points=tech_points,
        tech_links=tech_links,
        demand_pts=demand_pts,
        size_demand=size_demand,
    )


def print_layer_diagnostics(layers: PlotLayers) -> None:
    """Print summary diagnostics for prepared plotting layers.

    Reports the number of node-layer records, transport links, total transport
    flow by transport layer, and demand nodes. This is a console-only diagnostic
    used to check whether layer construction produced plausible plotting inputs.

    Parameters
    ----------
    layers : PlotLayers
        Prepared node, transport, and demand plotting layers.

    Returns
    -------
    None
        This function only prints diagnostics to the console.
    """

    print("\nNode layers:")
    for name, (points_df, _) in layers.tech_points.items():
        print(f"{name}: {len(points_df):,}")

    print("\nTransport layers:")
    for name, values in layers.tech_links.items():
        links = values[0]
        total_flow = links["flow"].sum() if "flow" in links.columns else 0
        print(f"{name}: {len(links):,} links, total flow = {total_flow:,.2f}")

    print(f"\nDemand nodes: {len(layers.demand_pts):,}")


# =============================================================================
# Context plotting
# =============================================================================

def plot_context_layers_schematic(
    ax: Axes,
    geodata: GeospatialData,
) -> None:
    """Plot context layers in the selected graph's native CRS.

    Draws only processed Canada/province boundaries in the graph-node CRS.
    This supports both geographic degree grids and projected kilometre grids
    without mixing coordinate units.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the context layers are drawn.
    geodata : GeospatialData
        Loaded basemap, model-region, and optional road-context layers.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    geodata.basemap.plot(
        ax=ax,
        color="none",
        edgecolor="0.70",
        linewidth=0.5,
        alpha=0.7,
        zorder=0,
    )



def plot_context_layers_web(
    ax: Axes,
    geodata: GeospatialData,
) -> None:
    """Plot geographic context layers in Web Mercator coordinates.

    Reprojects processed Canada/province polygons to EPSG:3857 before
    drawing them on an existing
    Matplotlib axis. This prepares the context layers to align with optional
    web-tile basemaps added by Contextily.

    Parameters
    ----------
    ax : Axes
        Matplotlib axis on which projected context layers are drawn.
    geodata : GeospatialData
        Loaded basemap, model-region, and optional road-context layers.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    basemap_web = geodata.basemap.to_crs(epsg=3857)
    basemap_web.plot(
        ax=ax,
        color="none",
        edgecolor="0.55",
        linewidth=0.5,
        alpha=0.75,
        zorder=1,
    )



# =============================================================================
# Transport plotting
# =============================================================================

def combined_transport_links(
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Combine transport layers and offset parallel corridor geometries.

    Concatenates positive transport-flow layers, validates required endpoint
    coordinate columns, aggregates duplicate edge/technology records, and
    assigns a canonical undirected corridor key for each source-target pair.
    Where multiple transport technologies use the same corridor, parallel
    offsets are assigned so overlapping routes can be visually separated.

    Line geometries are first constructed in EPSG:4326 from endpoint
    lon/lat coordinates, then reprojected to EPSG:3857 before metre-scale
    offsets are applied.

    Parameters
    ----------
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters controlling parallel route offsets.

    Returns
    -------
    gpd.GeoDataFrame
        Combined transport-flow GeoDataFrame in EPSG:3857 with offset line
        geometries and plotting metadata. Returns an empty GeoDataFrame if no
        valid positive transport links are available.
    """

    frames = []

    for display_name, values in tech_links.items():
        links, color, linestyle, width_factor = values
        if links.empty:
            continue

        required_cols = [
            "region",
            "tech",
            "region_from",
            "region_to",
            "lon_from",
            "lat_from",
            "lon_to",
            "lat_to",
            "flow",
        ]
        missing_cols = [col for col in required_cols if col not in links.columns]
        if missing_cols:
            print(f"Skipping {display_name}; missing columns: {missing_cols}")
            continue

        layer = links[required_cols].dropna().copy()
        layer["flow"] = pd.to_numeric(layer["flow"], errors="coerce")
        layer = layer.loc[layer["flow"] > 0].copy()
        if layer.empty:
            continue

        layer["display_name"] = display_name
        layer["color"] = color
        layer["linestyle"] = linestyle
        layer["width_factor"] = width_factor

        layer["route_a"] = layer[["region_from", "region_to"]].min(axis=1)
        layer["route_b"] = layer[["region_from", "region_to"]].max(axis=1)
        layer["route_key"] = layer["route_a"] + "__" + layer["route_b"]
        layer["is_reversed_from_canonical"] = layer["region_from"] != layer["route_a"]

        frames.append(layer)

    if not frames:
        return gpd.GeoDataFrame(columns=[])

    combined = pd.concat(frames, ignore_index=True)

    group_cols = [
        "region",
        "tech",
        "display_name",
        "color",
        "linestyle",
        "width_factor",
        "region_from",
        "region_to",
        "route_a",
        "route_b",
        "route_key",
        "is_reversed_from_canonical",
        "lon_from",
        "lat_from",
        "lon_to",
        "lat_to",
    ]
    combined = (
        combined
        .groupby(group_cols, as_index=False)
        .agg(flow=("flow", "sum"))
    )

    tech_order = {
        "Electricity transmission": 0,
        "H2 pipeline": 1,
        "CO2 pipeline": 2,
        "Methanol pipeline": 3,
        "Gasoline pipeline": 4,
        "H2 truck": 5,
        "CO2 truck": 6,
        "Methanol truck": 7,
        "Gasoline truck": 8,
    }
    combined["plot_order"] = combined["display_name"].map(tech_order).fillna(999)
    combined = combined.sort_values(
        ["route_key", "plot_order", "display_name"]
    ).copy()

    combined["parallel_rank"] = combined.groupby("route_key").cumcount()
    combined["parallel_count"] = combined.groupby("route_key")["display_name"].transform("count")

    raw_offset = (
        combined["parallel_rank"] - (combined["parallel_count"] - 1) / 2.0
    ) * spacing.parallel_offset_m
    combined["offset_m"] = raw_offset.clip(
        -spacing.parallel_max_offset_m,
        spacing.parallel_max_offset_m,
    )

    combined.loc[combined["is_reversed_from_canonical"], "offset_m"] *= -1

    base_geom = [
        LineString([(row.lon_from, row.lat_from), (row.lon_to, row.lat_to)])
        for row in combined.itertuples(index=False)
    ]

    transport_gdf = gpd.GeoDataFrame(combined, geometry=base_geom, crs="EPSG:4326")
    transport_gdf_3857 = transport_gdf.to_crs(epsg=3857)

    offset_geoms = []
    for row in transport_gdf_3857.itertuples(index=False):
        coords = list(row.geometry.coords)
        x1, y1 = coords[0]
        x2, y2 = coords[-1]
        dx = x2 - x1
        dy = y2 - y1
        length = float(np.hypot(dx, dy))

        if length == 0 or not np.isfinite(length):
            offset_geoms.append(row.geometry)
            continue

        ux = -dy / length
        uy = dx / length

        offset = float(row.offset_m)
        offset_geoms.append(
            LineString([
                (x1 + ux * offset, y1 + uy * offset),
                (x2 + ux * offset, y2 + uy * offset),
            ])
        )

    transport_gdf_3857 = transport_gdf_3857.copy()
    transport_gdf_3857["geometry"] = offset_geoms

    return transport_gdf_3857


def summarize_parallel_corridors(
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> gpd.GeoDataFrame:
    """Print diagnostics for corridors with parallel transport layers.

    Builds the combined transport GeoDataFrame, summarizes active corridors by
    canonical route key, and reports how many corridors carry more than one
    transport technology or mode. The returned GeoDataFrame can be reused for
    plotting or further corridor diagnostics.

    Parameters
    ----------
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used to construct offset transport
        geometries.

    Returns
    -------
    gpd.GeoDataFrame
        Combined transport-flow GeoDataFrame returned by
        ``combined_transport_links``. Returns an empty GeoDataFrame if no
        positive transport links are available.
    """

    transport_gdf = combined_transport_links(tech_links, spacing)
    if transport_gdf.empty:
        print("\nParallel corridor diagnostics: no positive transport links.")
        return transport_gdf

    corridor_summary = (
        transport_gdf
        .groupby("route_key", as_index=False)
        .agg(
            n_parallel_layers=("display_name", "count"),
            technologies=("display_name", lambda x: ", ".join(sorted(set(x)))),
            total_flow=("flow", "sum"),
        )
        .sort_values(["n_parallel_layers", "total_flow"], ascending=[False, False])
    )

    multiplex = corridor_summary.loc[
        corridor_summary["n_parallel_layers"] > 1
    ].copy()

    print("\nParallel corridor diagnostics:")
    print(f"Active transport corridor-tech rows: {len(transport_gdf):,}")
    print(f"Unique active corridors: {len(corridor_summary):,}")
    print(f"Corridors with >1 active transport layer: {len(multiplex):,}")

    if not multiplex.empty:
        print("\nTop multiplex corridors:")
        print(multiplex.head(10).to_string(index=False))

    return transport_gdf


def plot_transport_lines_schematic(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
    target_crs,
) -> None:
    """Plot transport-flow lines in the graph layer's native coordinate system.

    Transport links are first combined into a Web Mercator GeoDataFrame so any
    configured parallel-corridor offsets can be applied consistently in metres.
    The resulting geometries are reprojected to ``target_crs`` and plotted by
    display layer. Line widths are scaled within each layer using the square root
    of flow relative to that layer's maximum positive flow.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the transport lines are drawn.
    tech_links : dict[str, TECH_STYLE]
        Transport-flow layers keyed by display name. Each value contains the link
        DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting settings used when combining and offsetting
        parallel transport links.
    target_crs
        Coordinate reference system used by the destination plot axes. Accepts
        any CRS representation supported by GeoPandas.

    Returns
    -------
    None
    """

    transport_gdf_3857 = combined_transport_links(
        tech_links,
        spacing,
    )

    if transport_gdf_3857.empty:
        return

    transport_gdf = transport_gdf_3857.to_crs(target_crs)

    for _, group in transport_gdf.groupby(
        "display_name",
        sort=False,
    ):
        flow_values = pd.to_numeric(
            group["flow"],
            errors="coerce",
        ).to_numpy(dtype=float)

        valid_flows = flow_values[
            np.isfinite(flow_values) & (flow_values > 0)
        ]

        if valid_flows.size == 0:
            continue

        max_flow = float(valid_flows.max())

        for row in group.itertuples(index=False):
            geometry = row.geometry

            if not isinstance(geometry, LineString):
                raise TypeError(
                    "Transport layers must contain LineString geometries, "
                    f"not {type(geometry).__name__}."
                )

            flow_value = pd.to_numeric(
                row.flow,
                errors="coerce",
            )

            if pd.isna(flow_value):
                continue

            flow = float(np.asarray(flow_value, dtype=float).item())

            if not np.isfinite(flow) or flow <= 0:
                continue

            width_factor_value = pd.to_numeric(
                row.width_factor,
                errors="coerce",
            )

            if pd.isna(width_factor_value):
                continue

            width_factor = float(
                np.asarray(
                    width_factor_value,
                    dtype=float,
                ).item()
            )

            x_vals, y_vals = geometry.xy

            linewidth = width_factor * (
                0.5
                + 2.5
                * np.sqrt(flow / max_flow)
            )

            ax.plot(
                x_vals,
                y_vals,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=linewidth,
                alpha=0.78,
                zorder=20,
            )


def plot_transport_lines_web(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    spacing: PlotSpacing,
) -> None:
    """Plot transport-flow lines in EPSG:3857 Web Mercator.

    Builds combined offset transport geometries and draws each transport layer
    directly in Web Mercator coordinates. Line widths are scaled within each
    displayed transport layer using the square root of relative positive flow.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the transport lines are drawn.
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value contains a
        transport-flow DataFrame, plotting color, line style, and width factor.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used to separate parallel
        transport arcs.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    transport_gdf_3857 = combined_transport_links(
        tech_links,
        spacing,
    )

    if transport_gdf_3857.empty:
        return

    for _, group in transport_gdf_3857.groupby(
        "display_name",
        sort=False,
    ):
        flow_values = pd.to_numeric(
            group["flow"],
            errors="coerce",
        ).to_numpy(dtype=float)

        valid_flows = flow_values[
            np.isfinite(flow_values) & (flow_values > 0)
        ]

        if valid_flows.size == 0:
            continue

        max_flow = float(valid_flows.max())

        for row in group.itertuples(index=False):
            geometry = row.geometry

            if not isinstance(geometry, LineString):
                raise TypeError(
                    "Transport layers must contain LineString geometries, "
                    f"not {type(geometry).__name__}."
                )

            flow_value = pd.to_numeric(
                row.flow,
                errors="coerce",
            )

            if pd.isna(flow_value):
                continue

            flow = float(
                np.asarray(
                    flow_value,
                    dtype=float,
                ).item()
            )

            if not np.isfinite(flow) or flow <= 0:
                continue

            width_factor_value = pd.to_numeric(
                row.width_factor,
                errors="coerce",
            )

            if pd.isna(width_factor_value):
                continue

            width_factor = float(
                np.asarray(
                    width_factor_value,
                    dtype=float,
                ).item()
            )

            if not np.isfinite(width_factor) or width_factor <= 0:
                continue

            x_vals, y_vals = geometry.xy

            linewidth = width_factor * (
                0.5
                + 2.5
                * np.sqrt(flow / max_flow)
            )

            ax.plot(
                x_vals,
                y_vals,
                color=row.color,
                linestyle=row.linestyle,
                linewidth=linewidth,
                alpha=0.72,
                zorder=20,
            )


# =============================================================================
# Point and legend plotting
# =============================================================================

def build_legend(
    ax: Axes,
    tech_links: dict[str, TechStyle],
    bbox_to_anchor: tuple[float, float],
    fontsize: int | None = None,
    loc: str = "upper right",
) -> None:
    """Build a combined legend for context, point, and transport layers.

    Collects existing legend handles from the axis, adds proxy handles for
    transport technologies, and adds proxy handles for geographic context
    layers. Optional road-context legend entries are included only when the
    corresponding geospatial layers are available.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes whose legend is updated.
    tech_links : dict[str, TECH_STYLE]
        Transport layers keyed by display name. Each value provides the color,
        line style, and width factor used to create legend proxies.
    geodata : GeospatialData
        Loaded geospatial context used to determine whether optional road-layer
        legend entries should be included.
    bbox_to_anchor : tuple[float, float]
        Anchor position passed to ``ax.legend`` for legend placement.
    fontsize : int | None, default=None
        Optional legend font size.
    loc : str, default="upper right"
        Legend location passed to ``ax.legend``.

    Returns
    -------
    None
        This function updates the legend on ``ax``.
    """

    handles, labels = ax.get_legend_handles_labels()

    labelled_handles: dict[str, object] = {
        str(label): handle
        for label, handle in zip(labels, handles)
        if str(label)
    }

    transport_proxies = [
        Line2D(
            [0],
            [0],
            color=values[1],
            lw=1.5 * values[3],
            linestyle=values[2],
            label=name,
        )
        for name, values in tech_links.items()
    ]

    context_proxies = [
        Line2D(
            [0],
            [0],
            color="0.70",
            lw=0.8,
            label="Canada/province polygons",
        ),
    ]

    combined_handles = [
        *context_proxies,
        *labelled_handles.values(),
        *transport_proxies,
    ]

    combined_labels = [
        str(proxy.get_label())
        for proxy in context_proxies
    ] + [
        *labelled_handles.keys(),
    ] + [
        str(proxy.get_label())
        for proxy in transport_proxies
    ]

    unique_legend_entries = dict(
        zip(
            combined_labels,
            combined_handles,
        )
    )

    legend_labels: list[str] = list(unique_legend_entries.keys())
    legend_handles = list(unique_legend_entries.values())

    ax.legend(
        legend_handles,
        legend_labels,
        title="Legend",
        frameon=False,
        loc=loc,
        bbox_to_anchor=bbox_to_anchor,
        fontsize=fontsize,
    )


def plot_points_native(
    ax: Axes,
    tech_points: dict[str, PointStyle],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
    target_crs,
) -> None:
    """Plot demand and process-point layers in the graph layer's native CRS.

    Demand coordinates are converted from WGS84 longitude and latitude to
    ``target_crs`` and plotted using marker sizes supplied by ``size_demand``.
    Process points are filtered to positive finite flows, assigned deterministic
    coordinate jitter to reduce marker overlap, reprojected to ``target_crs``, and
    scaled relative to the maximum flow within each technology layer.

    Parameters
    ----------
    ax : Axes
        Matplotlib axes on which the point layers are drawn.
    tech_points : dict[str, POINT_STYLE]
        Process-point layers keyed by display name. Each value contains a point
        DataFrame and plotting color.
    demand_pts : pd.DataFrame
        Demand-point table containing longitude and latitude coordinates.
    size_demand : pd.Series
        Demand marker sizes indexed consistently with ``demand_pts``.
    spacing : PlotSpacing
        Resolution-aware plotting settings containing the process-point jitter
        distance in decimal degrees.
    target_crs
        Coordinate reference system used by the destination plot axes. Accepts any
        CRS representation supported by GeoPandas.

    Returns
    -------
    None
    """

    if not demand_pts.empty:
        demand_gdf = build_point_geodataframe(
            demand_pts,
            target_crs=target_crs,
        )
        ax.scatter(
            demand_gdf.geometry.x,
            demand_gdf.geometry.y,
            s=size_demand.loc[demand_gdf.index] * 0.3,
            linewidth=1.5,
            alpha=1,
            marker="x",
            label="Demand",
            c="black",
            zorder=30,
        )

    rng = np.random.default_rng(42)

    for name, (points_df, color) in tech_points.items():
        if points_df.empty or "flow" not in points_df.columns:
            continue

        points_plot = points_df[["lon", "lat", "flow"]].dropna().copy()
        points_plot["flow"] = pd.to_numeric(
            points_plot["flow"],
            errors="coerce",
        )
        points_plot = points_plot.loc[
            points_plot["flow"] > 0
        ].dropna(subset=["flow"])

        if points_plot.empty:
            continue

        points_plot["lon_plot"] = points_plot["lon"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_plot),
        )
        points_plot["lat_plot"] = points_plot["lat"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_plot),
        )

        points_gdf = build_point_geodataframe(
            points_plot,
            target_crs=target_crs,
            lon_col="lon_plot",
            lat_col="lat_plot",
        )
        sizes = np.sqrt(
            points_gdf["flow"] / points_gdf["flow"].max()
        ) * 150

        ax.scatter(
            points_gdf.geometry.x,
            points_gdf.geometry.y,
            s=sizes,
            alpha=0.65,
            label=name,
            c=color,
            zorder=30,
        )


def plot_points_web(
    ax: Axes,
    tech_points: dict[str, PointStyle],
    demand_pts: pd.DataFrame,
    size_demand: pd.Series,
    spacing: PlotSpacing,
) -> None:
    """Plot demand and process-point layers in Web Mercator coordinates.

    Converts demand and process-point coordinates from EPSG:4326 to EPSG:3857
    before drawing them on a web-map axis. Demand markers are plotted at their
    model-region centroid coordinates, while process markers receive
    reproducible random jitter before reprojection to reduce visual overlap.
    Process marker sizes are square-root-scaled within each technology layer
    using relative positive flow magnitude.

    Parameters
    ----------
    ax : Axes
        Matplotlib axis on which projected point layers are drawn.
    tech_points : dict[str, POINT_STYLE]
        Process-point layers keyed by display name. Each value contains a
        coordinate-enriched DataFrame and plotting color.
    demand_pts : pd.DataFrame
        Demand-point table containing ``lon``, ``lat``, and ``demand`` columns.
    size_demand : pd.Series
        Precomputed demand marker sizes.
    spacing : PlotSpacing
        Resolution-aware plotting parameters controlling process-point jitter.

    Returns
    -------
    None
        This function draws directly onto ``ax``.
    """

    if not demand_pts.empty:
        demand_pts_gdf = gpd.GeoDataFrame(
            demand_pts,
            geometry=gpd.points_from_xy(demand_pts["lon"], demand_pts["lat"]),
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        ax.scatter(
            demand_pts_gdf.geometry.x,
            demand_pts_gdf.geometry.y,
            s=size_demand * 0.3,
            color="black",
            alpha=1,
            marker="x",
            linewidths=1.2,
            label="Demand",
            zorder=30,
        )

    rng = np.random.default_rng(42)

    for name, (points_df, color) in tech_points.items():
        if points_df.empty or "flow" not in points_df.columns:
            continue

        points_df = points_df.dropna(subset=["lon", "lat", "flow"]).copy()
        if points_df.empty:
            continue

        points_df["flow"] = pd.to_numeric(points_df["flow"], errors="coerce")
        points_df = points_df.dropna(subset=["flow"])
        points_df = points_df.loc[points_df["flow"] > 0].copy()
        if points_df.empty:
            continue

        points_df["lon_plot"] = points_df["lon"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_df),
        )
        points_df["lat_plot"] = points_df["lat"] + rng.uniform(
            -spacing.jitter_deg,
            spacing.jitter_deg,
            size=len(points_df),
        )

        points_gdf = gpd.GeoDataFrame(
            points_df,
            geometry=gpd.points_from_xy(points_df["lon_plot"], points_df["lat_plot"]),
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        sizes = np.sqrt(points_df["flow"] / points_df["flow"].max()) * 90

        ax.scatter(
            points_gdf.geometry.x,
            points_gdf.geometry.y,
            s=sizes,
            color=color,
            alpha=0.65,
            label=name,
            zorder=30,
        )


# =============================================================================
# Figure builders
# =============================================================================

def save_polygon_context_figure(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build, display, and save the polygon-context schematic figure.

    Creates a native-CRS schematic map showing Canada/province boundaries,
    region centroids, process and demand points, and transport-flow lines. The
    figure is saved as a PNG in the selected run's figure directory.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded basemap, model-region, graph, and optional road-context layers.
    layers : PlotLayers
        Prepared process-point, demand-point, and transport-flow plotting
        layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for point jitter and
        transport-line offsets.
    paths : GeospatialPaths
        Resolved geospatial paths and figure-output naming metadata.

    Returns
    -------
    Path
        Path to the saved polygon-context PNG figure.
    """

    fig, ax = plt.subplots(figsize=(10, 6))

    plot_context_layers_schematic(ax, geodata)

    target_crs = native_plot_crs(geodata)
    site_centroids = geodata.sites.geometry.centroid

    ax.scatter(
        site_centroids.x,
        site_centroids.y,
        s=3,
        alpha=0.12,
        label="Region centroid",
        c="#777777",
        zorder=10,
    )

    plot_points_native(
        ax,
        layers.tech_points,
        layers.demand_pts,
        layers.size_demand,
        spacing,
        target_crs,
    )

    plot_transport_lines_schematic(
        ax,
        layers.tech_links,
        spacing,
        target_crs,
    )

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
    )

    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")

    sns.despine(
        ax=ax,
        top=True,
        right=True,
        bottom=True,
        left=True,
    )

    fig.tight_layout()

    epsg_code = target_crs.to_epsg()
    crs_tag = (
        f"epsg{epsg_code}"
        if epsg_code is not None
        else "native_crs"
    )

    fig_path = (
        paths.figure_dir
        / f"{paths.fig_stem}_polygon_context_{crs_tag}.png"
    )

    fig.savefig(
        fig_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)

    print(f"Saved polygon-context figure: {fig_path}")

    return fig_path


def save_basemap_overlay_figure(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build, display, and save the web-map basemap overlay figure.

    Creates an EPSG:3857 map showing projected basemap/context layers,
    optional Contextily web tiles, model-region centroids, process and demand
    points, and transport-flow lines. If web-tile loading fails, the figure is
    still generated using local geospatial layers only. The figure is saved as
    an SVG in the selected run's figure directory.

    Parameters
    ----------
    geodata : GeospatialData
        Loaded basemap, model-region, graph, and optional road-context layers.
    layers : PlotLayers
        Prepared process-point, demand-point, and transport-flow plotting
        layers.
    spacing : PlotSpacing
        Resolution-aware plotting parameters used for point jitter and
        transport-line offsets.
    paths : GeospatialPaths
        Resolved geospatial paths and figure-output naming metadata.

    Returns
    -------
    Path
        Path to the saved basemap polygon-overlay SVG figure.
    """

    fig, ax = plt.subplots(figsize=(9, 10))

    plot_context_layers_web(ax, geodata)

    if PLOT_WEB_TILES:
        try:
            import contextily as ctx

            osm_provider = providers.query_name(
                "OpenStreetMap.Mapnik"
            )

            ctx.add_basemap(
                ax,
                source=osm_provider,
                zorder=0,
            )
        except (
            requests.RequestException,
            URLError,
            RasterioError,
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover - web service dependent
            print(
                "Contextily basemap failed; continuing with local layers only. "
                f"Reason: {exc}"
            )

    sites_web_points = gpd.GeoDataFrame(
        geodata.sites.copy(),
        geometry=gpd.points_from_xy(
            geodata.sites["lon"],
            geodata.sites["lat"],
        ),
        crs="EPSG:4326",
    ).to_crs(epsg=3857)

    sites_web_points.plot(
        ax=ax,
        color="#777777",
        markersize=2,
        alpha=0.12,
        label="Region centroid",
        zorder=10,
    )

    plot_points_web(
        ax,
        layers.tech_points,
        layers.demand_pts,
        layers.size_demand,
        spacing,
    )

    plot_transport_lines_web(
        ax,
        layers.tech_links,
        spacing,
    )

    ax.set_title("", fontsize=14, pad=12)
    ax.grid(False)

    for spine in ax.spines.values():
        spine.set_visible(False)

    build_legend(
        ax=ax,
        tech_links=layers.tech_links,
        bbox_to_anchor=(1.35, 1.0),
        fontsize=10,
    )

    ax.set_xticks([])
    ax.set_yticks([])

    fig.tight_layout()

    fig_path = (
        paths.figure_dir
        / f"{paths.fig_stem}_basemap_polygon_overlay.svg"
    )

    fig.savefig(
        fig_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)

    print(f"Saved basemap polygon-overlay figure: {fig_path}")

    return fig_path


# =============================================================================
# Main workflow
# =============================================================================


# =============================================================================
# Folium rendering
# =============================================================================

FOLIUM_BASEMAP_PATH: Path | None = PROJECT_ROOT / "data_files/raw/basemaps/lpr_000b21a_e.shp"
FOLIUM_SHOW_GRID = True
FOLIUM_SHOW_ROAD_EDGES = False
FOLIUM_SHOW_PROCESSED_CONTEXT = True
FOLIUM_OPEN_BROWSER = False
FOLIUM_MAX_GRID_FEATURES = 25_000


def _html_escape(value: object) -> str:
    """Return a minimally escaped string for Folium popup HTML."""

    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def format_map_value(value: float, layer: str) -> str:
    """Format Gold model tonnes/MWh without scientific notation.

    Preserve the upstream aggregation; do not imply an annual rate for flows
    summed across periods. CO2 capture is an available capacity, not output.
    """
    value = float(value)
    if not np.isfinite(value):
        return "Unavailable"
    units = ("MWh", "GWh", "TWh") if layer.startswith("Electricity") else ("t", "kt", "Mt")
    scale = 2 if abs(value) >= 1e6 else 1 if abs(value) >= 1e3 else 0
    scaled = value / (1000 ** scale)
    number = f"{scaled:,.3f}".rstrip("0").rstrip(".")
    if value != 0 and abs(scaled) < 0.001:
        number = "<0.001" if value > 0 else ">-0.001"
    suffix = " CO2e/year capacity" if layer == "CO2" else ""
    if layer == "Gasoline demand":
        suffix = "/year"
    return f"{number} {units[scale]}{suffix}"


def add_map_legend(
    model_map: folium.Map,
    layers: PlotLayers,
    geodata: GeospatialData | None = None,
) -> None:
    """Explain categorical colour and symbol encodings, including hidden layers."""
    rows = []
    for name, (points, color) in layers.tech_points.items():
        if not points.empty:
            label = "CO2 capture capacity" if name == "CO2" else name
            rows.append(f'<div><span style="color:{color}">●</span> {_html_escape(label)}</div>')
    for name, (links, color, style, _) in layers.tech_links.items():
        if not links.empty:
            border = "dashed" if style == "--" else "solid"
            rows.append(f'<div><span style="display:inline-block;width:24px;border-top:3px {border} {color}"></span> {_html_escape(name)}</div>')
    if not layers.demand_pts.empty:
        rows.append('<div>○ Gasoline demand</div>')
    context_rows = []
    if geodata is not None:
        if geodata.lakes is not None and not geodata.lakes.empty:
            context_rows.append('<div><span style="color:#6baed6">■</span> Lakes</div>')
        if geodata.aboriginal_lands is not None and not geodata.aboriginal_lands.empty:
            context_rows.append('<div><span style="color:#8c6bb1">■</span> Aboriginal Lands</div>')
        if geodata.urban_centres is not None and not geodata.urban_centres.empty:
            context_rows.append('<div><span style="color:#d89000">■</span> Urban centres</div>')
    model_map.get_root().html.add_child(folium.Element(
        '<aside aria-label="Map legend" style="position:fixed;bottom:25px;left:12px;'
        'z-index:1000;background:white;padding:12px;border:1px solid #777;'
        'font:12px Arial;max-height:45vh;overflow:auto;max-width:280px">'
        '<b>Legend · all available layers</b>' + ''.join(rows) +
        ('<hr><b>Geographic context</b>' + ''.join(context_rows) if context_rows else '') +
        '<hr>Colour identifies technology (Okabe–Ito palette).<br>'
        'Marker radius: square-root scale within each layer.<br>'
        'Line width: square-root scale across links, weighted by mode.<br>'
        'Hover for values; click for details. Toggle layers at top right.<br>'
        'Flows retain the source aggregation across model periods.<br>'
        'Mt = million tonnes; TWh = million MWh.<br>'
        'Local vector basemap; no tile service required.</aside>'
    ))


def _popup_table(rows: Sequence[tuple[str, object]]) -> folium.Popup:
    """Build a compact HTML popup table from label-value rows."""

    body = "".join(
        "<tr>"
        f"<th style='text-align:left;padding:2px 8px 2px 0'>{_html_escape(label)}</th>"
        f"<td style='padding:2px 0'>{_html_escape(value)}</td>"
        "</tr>"
        for label, value in rows
    )
    html = (
        "<div style='font-family:Arial,sans-serif;font-size:12px'>"
        f"<table>{body}</table>"
        "</div>"
    )
    return folium.Popup(html, max_width=420)


def _finite_extent(geodata: GeospatialData) -> list[list[float]]:
    """Return Leaflet bounds as [[south, west], [north, east]]."""

    context = geodata.basemap.to_crs(epsg=4326)
    minx, miny, maxx, maxy = context.total_bounds
    bounds = [[float(miny), float(minx)], [float(maxy), float(maxx)]]

    if not np.isfinite(np.asarray(bounds, dtype=float)).all():
        raise ValueError("Could not calculate finite map bounds from the basemap.")

    return bounds


def create_folium_base_map(geodata: GeospatialData) -> folium.Map:
    """Create the interactive map and fit it to the selected study area."""

    bounds = _finite_extent(geodata)
    center_lat = (bounds[0][0] + bounds[1][0]) / 2.0
    center_lon = (bounds[0][1] + bounds[1][1]) / 2.0

    model_map = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=4,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )

    folium.map.CustomPane('land', z_index=200, pointer_events=False).add_to(model_map)
    folium.map.CustomPane('reference', z_index=225, pointer_events=False).add_to(model_map)
    folium.map.CustomPane('context', z_index=250, pointer_events=False).add_to(model_map)

    model_map.fit_bounds(bounds, padding=(12, 12))
    Fullscreen(position="topright").add_to(model_map)
    MeasureControl(position="topright", primary_length_unit="kilometers").add_to(model_map)


    return model_map


def add_context_layers_folium(
    model_map: folium.Map,
    geodata: GeospatialData,
) -> None:
    """Add study-area polygons and optional road-enabled graph edges."""

    basemap_wgs84 = geodata.basemap.to_crs(epsg=4326).copy()

    # Embed once at export time: no tile requests or map-provider API keys.
    # The existing Statistics Canada province file is preferred; a local
    # Natural Earth GeoPackage/GeoJSON can also be supplied via this setting.
    if FOLIUM_BASEMAP_PATH is not None and FOLIUM_BASEMAP_PATH.exists():
        context = gpd.read_file(FOLIUM_BASEMAP_PATH).to_crs(epsg=3347)
        # Display-only simplification drops sub-500 m coastal details. Avoid
        # topology-preserving simplification of the full national coastline,
        # which is prohibitively slow; model geometries remain untouched.
        context.geometry = context.geometry.simplify(500, preserve_topology=False)
        folium.GeoJson(
            context[["geometry"]].to_crs(epsg=4326).to_json(),
            name="Local geographic context", pane="land", interactive=False,
            style_function=lambda _: {
                "color": "#a5aaa9", "weight": 0.7,
                "fillColor": "#f0f1ed", "fillOpacity": 1,
            },
        ).add_to(model_map)
    else:
        print("Local geographic context unavailable; using embedded model grid only.")

    if FOLIUM_SHOW_PROCESSED_CONTEXT:
        processed_context = (
            (
                "Lakes",
                geodata.lakes,
                500.0,
                {
                    "color": "#6baed6", "weight": 0.65, "opacity": 0.9,
                    "fillColor": "#9ecae1", "fillOpacity": 0.72,
                },
            ),
            (
                "Aboriginal Lands",
                geodata.aboriginal_lands,
                500.0,
                {
                    "color": "#8c6bb1", "weight": 0.55, "opacity": 0.75,
                    "fillColor": "#b39ac8", "fillOpacity": 0.24,
                },
            ),
            (
                "Urban centres",
                geodata.urban_centres,
                250.0,
                {
                    "color": "#d89000", "weight": 0.55, "opacity": 0.8,
                    "fillColor": "#f6c85f", "fillOpacity": 0.28,
                },
            ),
        )
        for display_name, frame, tolerance, style in processed_context:
            if frame is None or frame.empty:
                continue
            display = frame[[frame.geometry.name]].copy()
            # Evidence is stored in a metric CRS. Simplifying before WGS84
            # conversion keeps the standalone HTML compact without changing
            # any source or model geometry.
            if display.crs is not None:
                if not display.crs.is_projected:
                    display = display.to_crs(epsg=3347)
                display.geometry = display.geometry.simplify(
                    tolerance, preserve_topology=False
                )
            display = display.to_crs(epsg=4326)
            group = folium.FeatureGroup(
                name=f"{display_name} ({len(display):,})",
                show=True,
            )
            folium.GeoJson(
                data=display.to_json(drop_id=True),
                pane="reference",
                interactive=False,
                style_function=lambda _feature, layer_style=style: layer_style,
                smooth_factor=1.0,
            ).add_to(group)
            group.add_to(model_map)

    if FOLIUM_SHOW_GRID and len(basemap_wgs84) <= FOLIUM_MAX_GRID_FEATURES:
        grid_group = folium.FeatureGroup(
            name=f"Model grid ({len(basemap_wgs84):,} regions)",
            show=True,
        )
        folium.GeoJson(
            pane="context", interactive=False,
            data=basemap_wgs84[["geometry"]].to_json(),
            name="Model grid",
            style_function=lambda _feature: {
                "color": "#666666",
                "weight": 0.35,
                "fillColor": "#ffffff",
                "fillOpacity": 0.02,
            },
            smooth_factor=1.0,
        ).add_to(grid_group)
        grid_group.add_to(model_map)
    elif FOLIUM_SHOW_GRID:
        print(
            "Skipping model-grid polygons because the layer contains "
            f"{len(basemap_wgs84):,} features, above "
            f"FOLIUM_MAX_GRID_FEATURES={FOLIUM_MAX_GRID_FEATURES:,}."
        )

    boundary = basemap_wgs84[["geometry"]].dissolve()
    boundary_group = folium.FeatureGroup(name="Study-area boundary", show=True)
    folium.GeoJson(
        data=boundary.to_json(), pane="context", interactive=False,
        style_function=lambda _feature: {
            "color": "#444444",
            "weight": 1.5,
            "fillColor": "#ffffff",
            "fillOpacity": 0.0,
        },
    ).add_to(boundary_group)
    boundary_group.add_to(model_map)

    if FOLIUM_SHOW_ROAD_EDGES and geodata.road_edge_layer is not None:
        roads = geodata.road_edge_layer.to_crs(epsg=4326)
        road_group = folium.FeatureGroup(
            name=f"Road-enabled candidate edges ({len(roads):,})",
            show=False,
        )
        folium.GeoJson(
            data=roads[["geometry"]].to_json(), pane="context", interactive=False,
            style_function=lambda _feature: {
                "color": "#777777",
                "weight": 0.8,
                "opacity": 0.35,
            },
            smooth_factor=1.0,
        ).add_to(road_group)
        road_group.add_to(model_map)


def _flow_widths(
    flow: pd.Series,
    minimum: float,
    maximum: float,
) -> pd.Series:
    """Scale positive flows to bounded Leaflet line or marker widths."""

    numeric = pd.to_numeric(flow, errors="coerce").fillna(0.0).clip(lower=0.0)
    if numeric.empty:
        return numeric

    transformed = np.sqrt(numeric)
    low = float(transformed.min())
    high = float(transformed.max())

    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return pd.Series((minimum + maximum) / 2.0, index=numeric.index)

    return minimum + (transformed - low) * (maximum - minimum) / (high - low)


def add_transport_layers_folium(
    model_map: folium.Map,
    layers: PlotLayers,
    spacing: PlotSpacing,
) -> None:
    """Add one toggleable Folium layer for each active transport technology."""

    transport = combined_transport_links(layers.tech_links, spacing)
    if transport.empty:
        print("No active transport links available for Folium rendering.")
        return

    transport = transport.to_crs(epsg=4326).copy()
    transport["leaflet_width"] = _flow_widths(
        transport["flow"],
        minimum=1.5,
        maximum=8.0,
    ) * pd.to_numeric(transport["width_factor"], errors="coerce").fillna(1.0)

    show_defaults = {
        "Electricity transmission",
        "H2 pipeline",
        "CO2 pipeline",
        "Methanol pipeline",
        "Gasoline pipeline",
    }

    for display_name, subset in transport.groupby("display_name", sort=False):
        feature_group = folium.FeatureGroup(
            name=f"{display_name} ({len(subset):,} links)",
            show=display_name in show_defaults,
        )

        for row in subset.itertuples(index=False):
            coordinates = [
                [float(lat), float(lon)]
                for lon, lat in row.geometry.coords
            ]
            dash_array = "7 7" if str(row.linestyle) == "--" else None
            tooltip = (
                f"{_html_escape(row.display_name)} | "
                f"Flow: {format_map_value(row.flow, display_name)} | "
                f"{_html_escape(row.region_from)} → {_html_escape(row.region_to)}"
            )
            popup = _popup_table([
                ("Layer", row.display_name),
                ("Technology", row.tech),
                ("Transport region", row.region),
                ("From", row.region_from),
                ("To", row.region_to),
                ("Flow", format_map_value(row.flow, display_name)),
                ("Parallel layers", int(row.parallel_count)),
                ("Parallel offset (m)", f"{float(row.offset_m):,.0f}"),
            ])

            folium.PolyLine(
                locations=coordinates,
                color=str(row.color),
                weight=float(row.leaflet_width),
                opacity=0.82,
                dash_array=dash_array,
                tooltip=tooltip,
                popup=popup,
            ).add_to(feature_group)

        feature_group.add_to(model_map)


def add_process_layers_folium(
    model_map: folium.Map,
    layers: PlotLayers,
    spacing: PlotSpacing,
) -> None:
    """Add process and capture nodes as independently toggleable point layers."""

    rng = np.random.default_rng(seed=42)

    for display_name, (points, color) in layers.tech_points.items():
        valid = points.dropna(subset=["lon", "lat", "flow"]).copy()
        if valid.empty:
            continue

        valid["radius"] = _flow_widths(valid["flow"], minimum=4.0, maximum=13.0)
        group = folium.FeatureGroup(
            name=f"{display_name} nodes ({len(valid):,})",
            show=True,
        )

        for row in valid.itertuples(index=False):
            lon = float(row.lon)
            lat = float(row.lat)
            if spacing.jitter_deg > 0:
                lon += float(rng.uniform(-spacing.jitter_deg, spacing.jitter_deg))
                lat += float(rng.uniform(-spacing.jitter_deg, spacing.jitter_deg))

            technology = getattr(row, "tech", display_name)
            popup = _popup_table([
                ("Layer", display_name),
                ("Technology", technology),
                ("Region", row.region),
                ("Flow/capacity", format_map_value(row.flow, display_name)),
                ("Longitude", f"{lon:.5f}"),
                ("Latitude", f"{lat:.5f}"),
            ])

            folium.CircleMarker(
                location=[lat, lon],
                radius=float(row.radius),
                color=str(color),
                weight=1.5,
                fill=True,
                fill_color=str(color),
                fill_opacity=0.72,
                tooltip=(
                    f"{_html_escape(display_name)} | "
                    f"{_html_escape(row.region)} | "
                    f"{format_map_value(row.flow, display_name)}"
                ),
                popup=popup,
            ).add_to(group)

        group.add_to(model_map)


def add_demand_layer_folium(
    model_map: folium.Map,
    layers: PlotLayers,
) -> None:
    """Add gasoline demand nodes as hollow black markers."""

    demand = layers.demand_pts.dropna(subset=["lon", "lat", "demand"]).copy()
    if demand.empty:
        return

    demand["radius"] = _flow_widths(demand["demand"], minimum=3.5, maximum=10.0)
    group = folium.FeatureGroup(
        name=f"Gasoline demand ({len(demand):,} nodes)",
        show=True,
    )

    for row in demand.itertuples(index=False):
        # Region and period are useful popup metadata but are not required for
        # rendering. ``getattr`` keeps the map export robust when an upstream
        # table or older preprocessing product omits either optional column.
        region = getattr(row, "region", None)
        period = getattr(row, "period", None)

        region_label = (
            str(region)
            if region is not None and pd.notna(region)
            else "Unknown region"
        )

        popup_rows = [
            ("Layer", "Gasoline demand"),
            ("Demand", format_map_value(row.demand, "Gasoline demand")),
        ]

        if region is not None and pd.notna(region):
            popup_rows.insert(1, ("Region", region_label))

        if period is not None and pd.notna(period):
            popup_rows.append(("Period", period))

        folium.CircleMarker(
            location=[float(row.lat), float(row.lon)],
            radius=float(row.radius),
            color="#111111",
            weight=1.4,
            fill=True,
            fill_color="#ffffff",
            fill_opacity=0.55,
            tooltip=(
                f"Gasoline demand | {_html_escape(region_label)} | "
                f"{format_map_value(row.demand, 'Gasoline demand')}"
            ),
            popup=_popup_table(popup_rows),
        ).add_to(group)

    group.add_to(model_map)


def add_map_title(model_map: folium.Map, title: str) -> None:
    """Add a fixed title panel to the exported map."""

    safe_title = _html_escape(title)
    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50px; z-index: 9999;
                background: rgba(255,255,255,0.92); border: 1px solid #777;
                border-radius: 4px; padding: 7px 11px; font-family: Arial,
                sans-serif; font-size: 14px; font-weight: 600;">
        {safe_title}
    </div>
    """
    root = model_map.get_root()
    if not isinstance(root, Figure):
        raise TypeError("Folium map root is not an HTML figure.")
    root.html.add_child(folium.Element(title_html))


def save_folium_map(
    geodata: GeospatialData,
    layers: PlotLayers,
    spacing: PlotSpacing,
    paths: GeospatialPaths,
) -> Path:
    """Build and save the embedded-data interactive Folium HTML map."""

    model_map = create_folium_base_map(geodata)
    add_context_layers_folium(model_map, geodata)
    add_transport_layers_folium(model_map, layers, spacing)
    add_process_layers_folium(model_map, layers, spacing)
    add_demand_layer_folium(model_map, layers)
    add_map_legend(model_map, layers, geodata)
    add_map_title(model_map, paths.fig_stem.replace("_", " "))

    folium.LayerControl(
        position="topright",
        collapsed=False,
        auto_z_index=True,
    ).add_to(model_map)

    output_path = paths.figure_dir / f"{paths.fig_stem}_interactive_folium.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model_map.save(str(output_path))

    print(f"\nSaved interactive Folium map: {output_path}")
    print(f"File size: {output_path.stat().st_size / 1e6:,.1f} MB")

    if FOLIUM_OPEN_BROWSER:
        import webbrowser
        webbrowser.open(output_path.resolve().as_uri())

    return output_path


def main(argv: Sequence[str] | None = None) -> None:
    """Run the interactive CANOE/TEMOA Folium map-generation workflow."""

    parser = argparse.ArgumentParser(
        description="Generate an interactive map from a solved CANOE/TEMOA run."
    )
    parser.parse_args(argv)

    project_paths = resolve_project_paths()
    selected_run = select_run_and_database(project_paths.output_root)
    geospatial_paths = infer_geospatial_paths(project_paths.data_files, selected_run)

    tables = load_model_tables(selected_run.db_path)
    geodata = load_geospatial_data(geospatial_paths)
    spacing = configure_resolution_aware_spacing(
        geospatial_paths.basemap_stem,
        geodata.sites,
        diagnostic_mode=False,
    )

    print_loaded_data_summary(tables, geodata, spacing)

    layers = build_plot_layers(tables, geodata)
    print_layer_diagnostics(layers)
    summarize_parallel_corridors(layers.tech_links, spacing)

    save_folium_map(
        geodata=geodata,
        layers=layers,
        spacing=spacing,
        paths=geospatial_paths,
    )


if __name__ == "__main__":
    main()
