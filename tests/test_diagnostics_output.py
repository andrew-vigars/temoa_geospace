from __future__ import annotations

import sqlite3

import pandas as pd

from geocanoe.diagnostics.output import (
    check_commodity_balance,
    check_etl_defined_edge_flow_capacity,
    check_objective_cost_consistency,
)
from geocanoe.diagnostics.output.gate import read_optional_table, read_table, table_exists


def test_sqlite_helpers_distinguish_required_and_optional_tables() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE Example (value REAL)")
        connection.execute("INSERT INTO Example VALUES (1.5)")

        assert table_exists(connection, "Example")
        assert read_table(connection, "Example").iloc[0]["value"] == 1.5
        assert read_optional_table(connection, "Absent").empty


def test_commodity_balance_accepts_balanced_node() -> None:
    flow_in = pd.DataFrame(
        columns=["scenario", "region", "period", "input_comm", "flow"]
    )
    flow_out = pd.DataFrame(
        {
            "scenario": ["S"],
            "region": ["R1"],
            "period": [1],
            "output_comm": ["elc"],
            "flow": [10.0],
        }
    )
    demand = pd.DataFrame(
        {"region": ["R1"], "period": [1], "commodity": ["elc"], "demand": [10.0]}
    )
    commodity = pd.DataFrame({"name": ["elc"], "flag": ["wa"]})

    passed, balance, failures, excluded = check_commodity_balance(
        flow_in,
        flow_out,
        demand,
        commodity,
        excluded_flags={"s", "e"},
        abs_tol=1e-6,
        rel_tol=1e-6,
    )

    assert passed
    assert len(balance) == 1
    assert failures.empty
    assert excluded.empty


def test_edge_capacity_check_can_apply_optional_strict_comparison() -> None:
    flow_out = pd.DataFrame(
        {
            "scenario": ["S"],
            "region": ["R1-R2"],
            "period": [1],
            "tech": ["PIPE"],
            "vintage": [1],
            "flow": [10.0],
        }
    )
    capacity = pd.DataFrame(
        {
            "scenario": ["S"],
            "region": ["R1-R2"],
            "period": [1],
            "tech": ["PIPE"],
            "vintage": [1],
            "capacity": [5.0],
        }
    )

    ordinary_passed, ordinary_failures = check_etl_defined_edge_flow_capacity(
        flow_out,
        capacity,
        {"PIPE"},
        abs_tol=1e-6,
        strict_capacity_flow=False,
    )
    strict_passed, strict_failures = check_etl_defined_edge_flow_capacity(
        flow_out,
        capacity,
        {"PIPE"},
        abs_tol=1e-6,
        strict_capacity_flow=True,
    )

    assert ordinary_passed
    assert ordinary_failures.empty
    assert not strict_passed
    assert strict_failures.iloc[0]["failure_reason"] == (
        "ETLSegment-defined edge flow exceeds reported capacity"
    )


def test_objective_cost_consistency_reports_discounted_convention() -> None:
    objective = pd.DataFrame(
        {
            "scenario": ["S"],
            "objective_name": ["TotalCost"],
            "total_system_cost": [6.0],
        }
    )
    output_cost = pd.DataFrame(
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
    )

    passed, comparison = check_objective_cost_consistency(
        objective,
        output_cost,
        abs_tol=1e-6,
        rel_tol=1e-6,
        objective_cost_mode="discounted",
    )

    assert passed
    assert comparison.iloc[0]["objective_convention"] == "discounted"
