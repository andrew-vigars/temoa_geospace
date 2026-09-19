"""Strict numeric validation for encoded model tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from geocanoe.diagnostics.models import DiagnosticResult, DiagnosticStage


NumericRule = Literal["positive", "non_negative"]


@dataclass(frozen=True)
class NumericColumnRule:
    """Validation rule for one required numeric table column."""

    table: str
    column: str
    rule: NumericRule
    severity: str = "ERROR"


def check_numeric_columns(
    tables: dict[str, pd.DataFrame],
    rules: list[NumericColumnRule],
) -> list[DiagnosticResult]:
    """Check presence, parseability, nulls, finiteness, and allowed range.

    Numeric coercion is used only to assess values. Unlike the former gate,
    values that coerce to ``NaN`` are explicit failures rather than silently
    passing the subsequent range comparison.
    """

    results: list[DiagnosticResult] = []

    for spec in rules:
        check_label = f"{spec.table}.{spec.column}"
        if spec.table not in tables:
            results.append(
                DiagnosticResult(
                    name=f"{check_label} is valid numeric data",
                    passed=False,
                    severity=spec.severity,
                    detail=f"{spec.table} table missing",
                    ran=False,
                    check_id=f"INPUT.NUMERIC.{spec.table.upper()}.{spec.column.upper()}",
                    stage=DiagnosticStage.INPUT,
                )
            )
            continue

        table = tables[spec.table]
        if spec.column not in table.columns:
            results.append(
                DiagnosticResult(
                    name=f"{check_label} is valid numeric data",
                    passed=False,
                    severity=spec.severity,
                    detail=f"missing column {spec.column}",
                    ran=False,
                    check_id=f"INPUT.NUMERIC.{spec.table.upper()}.{spec.column.upper()}",
                    stage=DiagnosticStage.INPUT,
                )
            )
            continue

        raw = table[spec.column]
        numeric = pd.to_numeric(raw, errors="coerce")
        invalid_numeric = numeric.isna() | ~pd.Series(
            np.isfinite(numeric),
            index=numeric.index,
        )

        if spec.rule == "positive":
            invalid_range = numeric.notna() & (numeric <= 0)
            range_label = "greater than zero"
        else:
            invalid_range = numeric.notna() & (numeric < 0)
            range_label = "non-negative"

        invalid = invalid_numeric | invalid_range
        failures = table.loc[invalid].copy()
        if not failures.empty:
            failures.insert(0, "source_row", failures.index)
            failures["failure_reason"] = ""
            failures.loc[invalid_numeric.loc[invalid], "failure_reason"] = (
                "null, non-numeric, or non-finite value"
            )
            failures.loc[invalid_range.loc[invalid], "failure_reason"] = (
                f"value is not {range_label}"
            )

        results.append(
            DiagnosticResult(
                name=f"{check_label} is numeric and {range_label}",
                passed=failures.empty,
                severity=spec.severity,
                detail=(
                    f"rows={len(table)}, invalid_numeric={int(invalid_numeric.sum())}, "
                    f"invalid_range={int(invalid_range.sum())}"
                ),
                failures=failures if not failures.empty else None,
                check_id=f"INPUT.NUMERIC.{spec.table.upper()}.{spec.column.upper()}",
                stage=DiagnosticStage.INPUT,
                remediation=(
                    f"Replace null or non-numeric {check_label} values and ensure "
                    f"all values are {range_label}."
                ),
            )
        )

    return results
