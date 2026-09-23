from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from geocanoe.execution.batch import (
    BatchRun,
    load_solver_config,
    validate_batch_runs,
)
from geocanoe.execution.run import (
    file_record,
    parse_args,
    resolve_run_inputs,
    safe_name,
)
from geocanoe.schema.database import update_db_paths


@pytest.mark.parametrize("mode", ["off", "report", "strict"])
def test_runner_accepts_each_diagnostic_policy(mode: str) -> None:
    args = parse_args(["--diagnostics", mode])

    assert args.diagnostics == mode


def test_noninteractive_runner_requires_both_inputs() -> None:
    args = argparse.Namespace(
        database=Path("model.sqlite"),
        config=None,
        non_interactive=True,
    )

    with pytest.raises(ValueError, match="requires both"):
        resolve_run_inputs(args)


def test_file_record_captures_stable_provenance(tmp_path: Path) -> None:
    path = tmp_path / "input.sqlite"
    path.write_bytes(b"geocanoe")

    record = file_record(path)

    assert record["name"] == "input.sqlite"
    assert record["size_bytes"] == 8
    assert len(record["sha256"]) == 64
    assert safe_name(Path("CANOE_geospatial_on qc.sqlite")) == "on_qc"


def test_update_db_paths_updates_both_keys_and_preserves_comment(
    tmp_path: Path,
) -> None:
    config = tmp_path / "run.toml"
    config.write_text(
        'input_database = "old.sqlite" # source\n'
        'output_database = "old.sqlite"\n',
        encoding="utf-8",
    )

    update_db_paths(config, "new.sqlite", create_backup=False)
    updated = config.read_text(encoding="utf-8")

    assert 'input_database = "new.sqlite" # source' in updated
    assert 'output_database = "new.sqlite"' in updated


@pytest.mark.parametrize("scenario", ["provinces_25km_baseline", "standard_run"])
def test_batch_scenario_label_is_independent_of_database(
    tmp_path: Path, scenario: str,
) -> None:
    database = tmp_path / "gold_provinces_baseline-25km_70bac04d.sqlite"
    database.touch()
    config = tmp_path / "run.toml"
    config.write_text(
        f'scenario = "{scenario}"\n'
        f'input_database = "{database.as_posix()}"\n',
        encoding="utf-8",
    )
    label, database_path = load_solver_config(config)
    assert label == scenario
    assert database_path == database
    validate_batch_runs([BatchRun(0, config, database_path, label, True)])


@pytest.mark.parametrize("missing", ["config", "database"])
def test_batch_still_rejects_missing_files(tmp_path: Path, missing: str) -> None:
    config = tmp_path / "run.toml"
    database = tmp_path / "model.sqlite"
    if missing != "config":
        config.touch()
    if missing != "database":
        database.touch()
    with pytest.raises(FileNotFoundError, match=f"Run {missing} not found"):
        validate_batch_runs([BatchRun(0, config, database, "standard_run", True)])
