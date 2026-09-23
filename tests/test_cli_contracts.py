"""Contracts for installed commands and compatibility CLI adapters."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

from geocanoe.execution import bronze, silver


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SCRIPTS = {
    "geocanoe-build-bronze": "geocanoe.execution.bronze:main",
    "geocanoe-build-silver": "geocanoe.execution.silver:main",
    "geocanoe-build-schema": "geocanoe.schema.build:main",
    "geocanoe-run": "geocanoe.execution.run:main",
    "geocanoe-batch": "geocanoe.execution.batch:main",
    "geocanoe-diagnostics": "geocanoe.diagnostics.cli:main",
    "geocanoe-export": "geocanoe.analysis.exports:main",
    "geocanoe-map": "geocanoe.analysis.maps:main",
}


def test_pyproject_registers_expected_console_scripts() -> None:
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["scripts"] == EXPECTED_SCRIPTS


@pytest.mark.parametrize("target", EXPECTED_SCRIPTS.values())
def test_console_script_target_is_callable(target: str) -> None:
    module_name, attribute = target.split(":", maxsplit=1)
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute))


def test_bronze_parser_preserves_stage_and_path_options() -> None:
    args = bronze.parse_args(
        [
            "--stages",
            "nhn",
            "co2_storage",
            "--overwrite",
            "--raw-nhn-dir",
            "raw-nhn",
            "--co2-source-repo",
            "canco2",
        ]
    )

    assert args.stages == ["nhn", "co2_storage"]
    assert args.overwrite is True
    assert args.raw_nhn_dir == Path("raw-nhn")
    assert args.co2_source_repo == Path("canco2")


def test_silver_parser_defaults_to_committed_sample_profile() -> None:
    args = silver.parse_args([])
    expected = PROJECT_ROOT / "config" / "build_profiles" / "sample_build_profile.toml"

    assert args.config == expected
    assert args.config.is_file()
    assert args.stages is None
    assert args.validate_only is False


@pytest.mark.parametrize("module", (bronze, silver))
def test_workflow_module_help_exits_without_running(module: object) -> None:
    with pytest.raises(SystemExit, match="0"):
        module.main(["--help"])  # type: ignore[attr-defined]
