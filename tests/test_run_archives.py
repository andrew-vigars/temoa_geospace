import json
from pathlib import Path

from geocanoe.execution.provenance import archive_run_configs, configuration_snapshot
from geocanoe.execution.run import generate_output_map, parse_args


def test_archive_uses_build_snapshot_when_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "build.toml"
    source.write_text('id = "original"\n', encoding="utf-8")
    snapshot = configuration_snapshot(source)
    source.write_text('id = "changed"\n', encoding="utf-8")
    database = tmp_path / "model.sqlite"
    database.with_suffix(".manifest.json").write_text(
        json.dumps({"configuration_sources": {"build": snapshot}}), encoding="utf-8",
    )
    solver = tmp_path / "run.toml"
    solver.write_text('scenario = "test"\n', encoding="utf-8")
    target = tmp_path / "configs"
    records = archive_run_configs(database, solver, target)
    assert (target / "build.toml").read_text() == 'id = "original"\n'
    assert records["build"]["origin"] == "schema-build snapshot"
    assert records["temoav4"]["sha256"] == configuration_snapshot(solver)["sha256"]
    assert records["schema"]["status"] == "unavailable"


def test_maps_default_enabled_and_can_be_disabled(tmp_path: Path) -> None:
    assert not parse_args([]).no_map
    assert parse_args(["--no-map"]).no_map
    assert generate_output_map(tmp_path / "model.sqlite", tmp_path, enabled=False) == {"status": "disabled"}
    assert not (tmp_path / "map_generation.log").exists()


def test_map_result_and_failure_are_logged(tmp_path: Path, monkeypatch) -> None:
    import geocanoe.analysis.maps as maps

    output = tmp_path / "result.html"
    output.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(maps, "generate_run_map", lambda run, db: output)
    result = generate_output_map(tmp_path / "solved.sqlite", tmp_path)
    assert result["status"] == "completed"
    assert result["interactive_map"]["sha256"]

    def fail(run, db):
        print("Missing map layer")
        raise SystemExit(1)

    monkeypatch.setattr(maps, "generate_run_map", fail)
    result = generate_output_map(tmp_path / "solved.sqlite", tmp_path)
    assert result["status"] == "failed"
    assert "Missing map layer" in (tmp_path / "map_generation.log").read_text()
