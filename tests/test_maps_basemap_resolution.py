from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from geocanoe.analysis.maps import (
    SelectedRun,
    build_geospatial_paths_from_manifest,
    find_schema_manifest,
    infer_geospatial_paths,
    is_gold_schema_stem,
    read_region_basemap_stem,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _write_manifest(
    manifest_path: Path,
    *,
    basemap_stem: str,
    graph_node_path: Path,
    graph_edge_path: Path,
    basemap_path: Path,
    road_edges_gpkg_path: Path,
    build_id: str = "national",
    scenario_id: str = "baseline",
    fingerprint: str = "a1b2c3d4",
    road_layer: str = "freight_access",
    connection_method: str = "strong",
) -> None:
    payload = {
        "artifact": {
            "database": manifest_path.with_suffix("").name,
            "build_id": build_id,
            "scenario_id": scenario_id,
            "fingerprint": fingerprint,
        },
        "resolved_gold_configuration": {
            "basemap_stem": basemap_stem,
            "road_layer": road_layer,
            "connection_method": connection_method,
            "basemap_path": str(basemap_path),
            "graph_node_path": str(graph_node_path),
            "graph_edge_path": str(graph_edge_path),
            "road_edges_gpkg_path": str(road_edges_gpkg_path),
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


def _make_region_db(db_path: Path, notes: str | None) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE Region (region TEXT, notes TEXT);")
        if notes is not None:
            conn.execute("INSERT INTO Region VALUES ('R1', ?);", (notes,))
        conn.commit()


# =============================================================================
# is_gold_schema_stem
# =============================================================================

@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("gold_national_baseline_a1b2c3d4", True),
        ("input_gold_national_baseline_a1b2c3d4", True),
        ("working_gold_national_baseline_a1b2c3d4", True),
        ("solved_gold_national_baseline_a1b2c3d4", True),
        ("CANOE_geospatial_national_basemap_50km_centroid_freight_access_strong", False),
        ("solved_CANOE_geospatial_on_qc_basemap_25km_centroid_freight_access_strong", False),
    ],
)
def test_is_gold_schema_stem(stem: str, expected: bool) -> None:
    assert is_gold_schema_stem(stem) is expected


# =============================================================================
# find_schema_manifest
# =============================================================================

def test_find_schema_manifest_prefers_sidecar(tmp_path: Path) -> None:
    data_files = tmp_path / "data_files"
    db_path = tmp_path / "run" / "gold_national_baseline_a1b2c3d4.sqlite"
    sidecar = db_path.with_suffix(".manifest.json")
    _touch(sidecar)

    found = find_schema_manifest(db_path, data_files)

    assert found == sidecar


def test_find_schema_manifest_falls_back_to_processed_schema(tmp_path: Path) -> None:
    data_files = tmp_path / "data_files"
    db_path = tmp_path / "run" / "input_gold_national_baseline_a1b2c3d4.sqlite"
    original_manifest = (
        data_files / "processed" / "schema" / "gold_national_baseline_a1b2c3d4.manifest.json"
    )
    _touch(original_manifest)

    found = find_schema_manifest(db_path, data_files)

    assert found == original_manifest


def test_find_schema_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    data_files = tmp_path / "data_files"
    db_path = tmp_path / "run" / "gold_national_baseline_a1b2c3d4.sqlite"

    assert find_schema_manifest(db_path, data_files) is None


# =============================================================================
# read_region_basemap_stem
# =============================================================================

def test_read_region_basemap_stem_parses_notes(tmp_path: Path) -> None:
    db_path = tmp_path / "gold_national_baseline_a1b2c3d4.sqlite"
    _make_region_db(
        db_path, "national_basemap_50km_centroid CANOE geospatial graph node"
    )

    assert read_region_basemap_stem(db_path) == "national_basemap_50km_centroid"


def test_read_region_basemap_stem_returns_none_when_unrecognized(tmp_path: Path) -> None:
    db_path = tmp_path / "gold_national_baseline_a1b2c3d4.sqlite"
    _make_region_db(db_path, "some unrelated note")

    assert read_region_basemap_stem(db_path) is None


def test_read_region_basemap_stem_returns_none_when_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "gold_national_baseline_a1b2c3d4.sqlite"
    _make_region_db(db_path, None)

    assert read_region_basemap_stem(db_path) is None


# =============================================================================
# build_geospatial_paths_from_manifest
# =============================================================================

def test_build_geospatial_paths_from_manifest_resolves_exact_paths(
    tmp_path: Path,
) -> None:
    data_files = tmp_path / "data_files"
    processed = data_files / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    road_edges_path = (
        processed
        / "road_connectivity"
        / f"{basemap_stem}_freight_access_road_connectivity_strong_road_edges.gpkg"
    )
    for path in (node_path, edge_path, basemap_path, road_edges_path):
        _touch(path)

    schema_path = processed / "schema" / "gold_national_baseline_a1b2c3d4.sqlite"
    _touch(schema_path)
    manifest_path = schema_path.with_suffix(".manifest.json")
    _write_manifest(
        manifest_path,
        basemap_stem=basemap_stem,
        graph_node_path=node_path,
        graph_edge_path=edge_path,
        basemap_path=basemap_path,
        road_edges_gpkg_path=road_edges_path,
    )

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_gold_national_baseline_a1b2c3d4.sqlite"
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    paths = build_geospatial_paths_from_manifest(manifest_path, selected_run)

    assert paths.basemap_stem == basemap_stem
    assert paths.node_path == node_path
    assert paths.edge_path == edge_path
    assert paths.basemap_path == basemap_path
    assert paths.road_edge_gpkg_path == road_edges_path
    assert paths.build_id == "national"
    assert paths.scenario_id == "baseline"
    assert paths.fingerprint == "a1b2c3d4"
    assert paths.road_layer == "freight_access"
    assert paths.connection_method == "strong"


def test_build_geospatial_paths_from_manifest_tolerates_missing_road_edges(
    tmp_path: Path,
) -> None:
    data_files = tmp_path / "data_files"
    processed = data_files / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    road_edges_path = processed / "road_connectivity" / "missing_road_edges.gpkg"
    for path in (node_path, edge_path, basemap_path):
        _touch(path)

    schema_path = processed / "schema" / "gold_national_baseline_a1b2c3d4.sqlite"
    manifest_path = schema_path.with_suffix(".manifest.json")
    _write_manifest(
        manifest_path,
        basemap_stem=basemap_stem,
        graph_node_path=node_path,
        graph_edge_path=edge_path,
        basemap_path=basemap_path,
        road_edges_gpkg_path=road_edges_path,
    )

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_gold_national_baseline_a1b2c3d4.sqlite"
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    paths = build_geospatial_paths_from_manifest(manifest_path, selected_run)

    assert paths.road_edge_gpkg_path is None


def test_build_geospatial_paths_from_manifest_exits_on_missing_required_file(
    tmp_path: Path,
) -> None:
    processed = tmp_path / "data_files" / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    road_edges_path = processed / "road_connectivity" / "road_edges.gpkg"
    # Intentionally do not create node_path, simulating a deleted input.

    schema_path = processed / "schema" / "gold_national_baseline_a1b2c3d4.sqlite"
    manifest_path = schema_path.with_suffix(".manifest.json")
    _write_manifest(
        manifest_path,
        basemap_stem=basemap_stem,
        graph_node_path=node_path,
        graph_edge_path=edge_path,
        basemap_path=basemap_path,
        road_edges_gpkg_path=road_edges_path,
    )

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_gold_national_baseline_a1b2c3d4.sqlite"
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    with pytest.raises(SystemExit):
        build_geospatial_paths_from_manifest(manifest_path, selected_run)


# =============================================================================
# infer_geospatial_paths (integration across tiers)
# =============================================================================

def test_infer_geospatial_paths_uses_manifest_when_available(tmp_path: Path) -> None:
    data_files = tmp_path / "data_files"
    processed = data_files / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    road_edges_path = (
        processed
        / "road_connectivity"
        / f"{basemap_stem}_freight_access_road_connectivity_strong_road_edges.gpkg"
    )
    for path in (node_path, edge_path, basemap_path, road_edges_path):
        _touch(path)

    schema_path = processed / "schema" / "gold_national_baseline_a1b2c3d4.sqlite"
    manifest_path = schema_path.with_suffix(".manifest.json")
    _write_manifest(
        manifest_path,
        basemap_stem=basemap_stem,
        graph_node_path=node_path,
        graph_edge_path=edge_path,
        basemap_path=basemap_path,
        road_edges_gpkg_path=road_edges_path,
    )

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_gold_national_baseline_a1b2c3d4.sqlite"
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    paths = infer_geospatial_paths(data_files, selected_run)

    assert paths.basemap_stem == basemap_stem
    assert paths.fingerprint == "a1b2c3d4"


def test_infer_geospatial_paths_recovers_from_region_notes_without_manifest(
    tmp_path: Path,
) -> None:
    data_files = tmp_path / "data_files"
    processed = data_files / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    for path in (node_path, edge_path, basemap_path):
        _touch(path)

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_gold_national_baseline_a1b2c3d4.sqlite"
    _make_region_db(db_path, f"{basemap_stem} CANOE geospatial graph node")
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    paths = infer_geospatial_paths(data_files, selected_run)

    assert paths.basemap_stem == basemap_stem
    assert paths.build_id is None
    assert paths.road_edge_gpkg_path is None


def test_infer_geospatial_paths_falls_back_to_legacy_filename_regex(
    tmp_path: Path,
) -> None:
    data_files = tmp_path / "data_files"
    processed = data_files / "processed"
    basemap_stem = "national_basemap_50km_centroid"

    node_path = processed / "graph" / f"{basemap_stem}_graph_nodes.gpkg"
    edge_path = processed / "graph" / f"{basemap_stem}_graph_edges.csv"
    basemap_path = processed / "basemaps" / f"{basemap_stem}.gpkg"
    for path in (node_path, edge_path, basemap_path):
        _touch(path)

    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = (
        run_dir
        / "solved_CANOE_geospatial_national_basemap_50km_centroid_"
        "freight_access_strong.sqlite"
    )
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    paths = infer_geospatial_paths(data_files, selected_run)

    assert paths.basemap_stem == basemap_stem


def test_infer_geospatial_paths_exits_when_nothing_resolves(tmp_path: Path) -> None:
    data_files = tmp_path / "data_files"
    run_dir = tmp_path / "output_files" / "2026-01-01_run"
    db_path = run_dir / "solved_unrecognized_name.sqlite"
    _touch(db_path)
    selected_run = SelectedRun(run_dir=run_dir, db_path=db_path)

    with pytest.raises(SystemExit):
        infer_geospatial_paths(data_files, selected_run)
