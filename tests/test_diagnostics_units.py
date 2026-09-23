from __future__ import annotations

import pandas as pd
from pytest import MonkeyPatch

from geocanoe.diagnostics.cli import main as diagnostics_main
from geocanoe.diagnostics.input.units import check_table_units, classify_unit
from geocanoe.diagnostics.models import DiagnosticReport
from geocanoe.diagnostics.selection import select_numbered
from geocanoe.execution.run import parse_args as parse_run_args


def test_classify_project_and_standard_units() -> None:
    assert classify_unit("M$/MWh") == (
        "currency_per_energy",
        "known_project_unit",
    )
    assert classify_unit("kg") == ("mass", "parsed_standard_unit")
    assert classify_unit(None) == (None, "missing")
    assert classify_unit("definitely_not_a_unit") == (None, "unknown")


def test_unit_checks_report_missing_unknown_and_incompatible_groups() -> None:
    tables = {
        "Demand": pd.DataFrame(
            {"units": [None, "definitely_not_a_unit", "M$/MWh"]}
        )
    }

    inventory, results = check_table_units(tables)
    report = DiagnosticReport(results=results)

    assert len(inventory) == 3
    assert [result.passed for result in results] == [False, False, False]
    assert report.has_warnings
    assert not report.has_errors
    assert report.exit_code() == 0


def test_strict_units_promote_findings_to_errors() -> None:
    _, results = check_table_units(
        {"CostInvest": pd.DataFrame({"units": [None]})},
        strict=True,
    )
    report = DiagnosticReport(results=results)

    assert report.has_errors
    assert report.exit_code() == 1


def test_central_cli_help_and_unknown_command() -> None:
    assert diagnostics_main(["--help"]) == 0
    assert diagnostics_main(["not-a-command"]) == 2


def test_numbered_selection_retries_invalid_input(monkeypatch: MonkeyPatch) -> None:
    responses = iter(["not-a-number", "9", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    assert select_numbered(["inputs", "outputs"], "workflow") == "outputs"


def test_model_runner_diagnostic_policy_defaults_to_report() -> None:
    assert parse_run_args([]).diagnostics == "report"
    assert parse_run_args(["--diagnostics", "off"]).diagnostics == "off"
