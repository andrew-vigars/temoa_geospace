from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from geocanoe.execution.batch import (
    BatchRun,
    normalize_schema_name,
    validate_scenario_database_match,
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


def test_batch_schema_name_normalization_and_mismatch() -> None:
    assert normalize_schema_name(
        "CANOE_geospatial_ON-QC_basemap_0.50deg"
    ) == ["on", "qc", "0.5deg"]

    run = BatchRun(
        sequence=0,
        config_path=Path("run.toml"),
        database_path=Path("CANOE_geospatial_national_25km.sqlite"),
        scenario="atlantic_25km",
        enabled=True,
    )
    with pytest.raises(ValueError, match="naming mismatch"):
        validate_scenario_database_match(run)
