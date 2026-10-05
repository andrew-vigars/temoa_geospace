from __future__ import annotations

import numpy as np
import pandas as pd

from geocanoe.diagnostics.input.numeric import (
    NumericColumnRule,
    check_numeric_columns,
)
from geocanoe.diagnostics.input.gate import check_eos_cost_curves
from geocanoe.diagnostics.input.readiness import check_technology_readiness


def test_numeric_check_rejects_null_text_infinity_and_negative_values() -> None:
    tables = {
        "CostVariable": pd.DataFrame(
            {"cost": [1.0, None, "not-a-number", np.inf, -0.5]}
        )
    }

    result = check_numeric_columns(
        tables,
        [NumericColumnRule("CostVariable", "cost", "non_negative")],
    )[0]

    assert not result.passed
    assert result.failures is not None
    assert len(result.failures) == 4
    assert "invalid_numeric=3" in result.detail
    assert "invalid_range=1" in result.detail


def test_numeric_check_accepts_valid_positive_values() -> None:
    result = check_numeric_columns(
        {"Efficiency": pd.DataFrame({"efficiency": [0.1, 1, "2.5"]})},
        [NumericColumnRule("Efficiency", "efficiency", "positive")],
    )[0]

    assert result.passed
    assert result.failures is None


def test_readiness_matrix_applies_role_specific_cost_requirements() -> None:
    tables = {
        "technology": pd.DataFrame(
            {
                "tech": ["READY", "DEMAND", "NO_CAP_COST", "NO_COST", "NO_EFF"],
                "exchange": [0, 0, 0, 0, 1],
                "unlim_cap": [0, 1, 0, 1, 0],
            }
        ),
        "commodity": pd.DataFrame(
            {
                "name": ["feed", "product", "demand", "source"],
                "flag": ["wa", "wa", "d", "s"],
            }
        ),
        "efficiency": pd.DataFrame(
            {
                "tech": ["READY", "DEMAND", "NO_CAP_COST", "NO_COST"],
                "input_comm": ["feed", "product", "feed", "source"],
                "output_comm": ["product", "demand", "product", "product"],
            }
        ),
        "cost_variable": pd.DataFrame(
            {"tech": ["READY", "NO_CAP_COST"]}
        ),
        "cost_invest": pd.DataFrame({"tech": ["READY"]}),
        "cost_invest_eos": pd.DataFrame({"tech_or_group": ["NO_EFF"]}),
    }

    matrix, results = check_technology_readiness(tables)
    readiness = matrix.set_index("tech")

    assert bool(readiness.loc["READY", "ready"])
    assert bool(readiness.loc["DEMAND", "is_demand_sink"])
    assert bool(readiness.loc["DEMAND", "ready"])
    assert bool(readiness.loc["NO_CAP_COST", "missing_capacity_cost"])
    assert bool(readiness.loc["NO_COST", "missing_cost_representation"])
    assert bool(readiness.loc["NO_EFF", "missing_efficiency"])
    assert [result.passed for result in results] == [False, False, False]


def test_readiness_reports_missing_technology_table() -> None:
    matrix, results = check_technology_readiness({})

    assert matrix.empty
    assert len(results) == 1
    assert not results[0].ran
    assert results[0].check_id == "INPUT.TECH.READINESS_AVAILABLE"


def test_eos_curve_check_covers_all_three_cost_tables() -> None:
    tables = {
        "cost_invest_eos": pd.DataFrame(
            {
                "region": ["R1", "R1"],
                "tech_or_group": ["T", "T"],
                "segment": [0, 1],
                "capacity_lower": [0.0, 1.0],
                "capacity_upper": [1.0, 2.0],
                "cost_lower": [0.0, 10.0],
                "cost_upper": [10.0, 20.0],
            }
        ),
        "cost_fixed_eos": pd.DataFrame(
            columns=[
                "region", "period", "tech_or_group", "segment",
                "capacity_lower", "capacity_upper", "cost_lower", "cost_upper",
            ]
        ),
        "cost_variable_eos": pd.DataFrame(
            {
                "region": ["R1", "R1"],
                "period": [2025, 2025],
                "tech_or_group": ["T", "T"],
                "segment": [0, 2],
                "activity_lower": [0.0, 2.0],
                "activity_upper": [1.0, 3.0],
                "cost_lower": [0.0, 10.0],
                "cost_upper": [10.0, 20.0],
            }
        ),
    }

    results = check_eos_cost_curves(tables)

    assert len(results) == 3
    assert [result.passed for result in results] == [True, True, False]
