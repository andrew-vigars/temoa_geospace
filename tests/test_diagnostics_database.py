from __future__ import annotations

from pathlib import Path

import pandas as pd

from geocanoe.diagnostics.input.database import run_schema_database_checks
from geocanoe.diagnostics.output.gate import run_output_database_checks


def _write_tables(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    for index, (name, table) in enumerate(tables.items()):
        table.to_sql(
            name,
            f"sqlite:///{path}",
            if_exists="replace" if index else "fail",
            index=False,
        )


def _valid_input_tables() -> dict[str, pd.DataFrame]:
    return {
        "Technology": pd.DataFrame(
            {"tech": ["READY"], "exchange": [0], "unlim_cap": [0]}
        ),
        "Commodity": pd.DataFrame(
            {"name": ["feed", "product"], "flag": ["s", "d"]}
        ),
        "Efficiency": pd.DataFrame(
            {
                "tech": ["READY"],
                "input_comm": ["feed"],
                "output_comm": ["product"],
                "efficiency": [1.0],
            }
        ),
        "CostVariable": pd.DataFrame({"tech": ["READY"], "cost": [1.0]}),
        "CostInvest": pd.DataFrame({"tech": ["READY"], "cost": [1.0]}),
        "Demand": pd.DataFrame({"demand": [1.0]}),
        "ETLSegment": pd.DataFrame(
            {
                "tech_or_group": ["READY"],
                "cap_lower": [0.0],
                "cap_upper": [1.0],
                "cost_lower": [0.0],
                "cost_upper": [1.0],
            }
        ),
    }


def _valid_output_tables() -> dict[str, pd.DataFrame]:
    return {
        "OutputFlowIn": pd.DataFrame(
            columns=["scenario", "region", "period", "tech", "vintage", "input_comm", "flow"]
        ),
        "OutputFlowOut": pd.DataFrame(
            {
                "scenario": ["S"],
                "region": ["R1"],
                "period": [1],
                "tech": ["GEN"],
                "vintage": [1],
                "output_comm": ["elc"],
                "flow": [10.0],
            }
        ),
        "Demand": pd.DataFrame(
            {"region": ["R1"], "period": [1], "commodity": ["elc"], "demand": [10.0]}
        ),
        "Commodity": pd.DataFrame({"name": ["elc"], "flag": ["d"]}),
        "OutputObjective": pd.DataFrame(
            {"scenario": ["S"], "objective_name": ["TotalCost"], "total_system_cost": [6.0]}
        ),
        "OutputCost": pd.DataFrame(
            {
                "scenario": ["S"],
                "d_invest": [1.0],
                "d_fixed": [2.0],
                "d_var": [3.0],
                "d_emiss": [0.0],
                "invest": [10.0],
                "fixed": [20.0],
                "var": [30.0],
                "emiss": [0.0],
            }
        ),
    }


def test_schema_database_adapter_runs_complete_check_set(tmp_path: Path) -> None:
    database = tmp_path / "inputs.sqlite"
    _write_tables(database, _valid_input_tables())

    run = run_schema_database_checks(database)

    assert not run.report.has_errors
    assert not run.technology_readiness.empty
    assert run.report.metadata["database"] == str(database.resolve())


def test_schema_database_adapter_reports_missing_required_table(
    tmp_path: Path,
) -> None:
    database = tmp_path / "inputs.sqlite"
    tables = _valid_input_tables()
    del tables["Technology"]
    _write_tables(database, tables)

    run = run_schema_database_checks(database)

    assert run.report.has_errors
    assert any(not result.ran for result in run.report.results)


def test_output_database_adapter_accepts_balanced_accounting(
    tmp_path: Path,
) -> None:
    database = tmp_path / "solved.sqlite"
    _write_tables(database, _valid_output_tables())

    run = run_output_database_checks(database)

    assert not run.report.has_errors
    assert run.balance_failures.empty
    assert run.objective_comparison["passed"].all()


def test_output_database_adapter_reports_physical_and_cost_failures(
    tmp_path: Path,
) -> None:
    database = tmp_path / "solved.sqlite"
    tables = _valid_output_tables()
    tables["Demand"]["demand"] = 12.0
    tables["OutputObjective"]["total_system_cost"] = 7.0
    _write_tables(database, tables)

    run = run_output_database_checks(database)

    assert run.report.has_errors
    assert len(run.balance_failures) == 1
    assert not run.objective_comparison["passed"].any()
