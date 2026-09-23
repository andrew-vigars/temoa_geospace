"""Unit presence, parsing, and conservative compatibility diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from iam_units import registry
from pint.errors import PintError

from geocanoe.diagnostics.models import DiagnosticResult, DiagnosticStage


MISSING_UNIT = "<missing>"

# Project-native spellings that either include currency vintages or tokens that
# Pint interprets differently from CANOE/TEMOA's model convention.
KNOWN_UNIT_CATEGORIES = {
    "t": "mass",
    "M$/MWh": "currency_per_energy",
    "M$/t": "currency_per_mass",
    "M$/unit": "currency_per_capacity_unit",
    "CAD2020/t": "currency_per_mass",
    "CAD2020/(t capacity/year)": "currency_time_per_mass",
}


@dataclass(frozen=True)
class UnitExpectation:
    """Expected unit presence and accepted categories for one model table."""

    table: str
    allowed_categories: frozenset[str]


UNIT_EXPECTATIONS = {
    expectation.table: expectation
    for expectation in [
        UnitExpectation(
            "Demand",
            frozenset({"mass", "energy", "mass_per_time", "energy_per_time"}),
        ),
        UnitExpectation(
            "CostVariable",
            frozenset(
                {
                    "currency_per_energy",
                    "currency_per_mass",
                    "currency_per_capacity_unit",
                }
            ),
        ),
        UnitExpectation(
            "CostInvest",
            frozenset({"currency_per_capacity_unit"}),
        ),
        UnitExpectation(
            "CostFixed",
            frozenset({"currency_time_per_mass"}),
        ),
        UnitExpectation(
            "LimitCapacity",
            frozenset({"mass", "energy", "mass_per_time", "energy_per_time"}),
        ),
        UnitExpectation(
            "ExistingCapacity",
            frozenset({"mass", "energy", "mass_per_time", "energy_per_time"}),
        ),
    ]
}


def _standard_category(unit_text: str) -> str | None:
    """Classify standard physical units supported by ``iam-units``."""

    try:
        dimensionality = str(registry.parse_units(unit_text).dimensionality)
    except (PintError, TypeError, ValueError):
        return None

    return {
        "[mass]": "mass",
        "[mass] / [time]": "mass_per_time",
        "[mass] * [length] ** 2 / [time] ** 2": "energy",
        "[mass] * [length] ** 2 / [time] ** 3": "energy_per_time",
    }.get(dimensionality, "parsed_other")


def classify_unit(unit: object) -> tuple[str | None, str]:
    """Return a semantic category and parse status for a unit value."""

    if unit is None or pd.isna(unit) or not str(unit).strip():
        return None, "missing"

    unit_text = str(unit).strip()
    if unit_text in KNOWN_UNIT_CATEGORIES:
        return KNOWN_UNIT_CATEGORIES[unit_text], "known_project_unit"

    category = _standard_category(unit_text)
    if category is None:
        return None, "unknown"
    return category, "parsed_standard_unit"


def check_table_units(
    tables: dict[str, pd.DataFrame],
    *,
    strict: bool = False,
) -> tuple[pd.DataFrame, list[DiagnosticResult]]:
    """Inventory unit-bearing rows and check presence, parsing, and category.

    Missing, unknown, and incompatible units are warnings by default so legacy
    schemas can be diagnosed without being blocked. ``strict=True`` promotes
    these findings to errors.
    """

    rows: list[dict[str, object]] = []
    for table_name, table in sorted(tables.items()):
        if table.empty or "units" not in table.columns:
            continue

        unit_values = table["units"].map(
            lambda value: (
                MISSING_UNIT
                if value is None or pd.isna(value) or not str(value).strip()
                else str(value).strip()
            )
        )
        for unit_text, count in unit_values.value_counts(dropna=False).items():
            group = table.loc[unit_values.eq(unit_text)]
            tech_column = next(
                (
                    column
                    for column in ("tech", "tech_or_group")
                    if column in group.columns
                ),
                None,
            )
            example_regions = (
                ";".join(group["region"].dropna().astype(str).unique()[:5])
                if "region" in group.columns
                else ""
            )
            example_technologies = (
                ";".join(group[tech_column].dropna().astype(str).unique()[:5])
                if tech_column is not None
                else ""
            )
            category, parse_status = classify_unit(
                None if unit_text == MISSING_UNIT else unit_text
            )
            expectation = UNIT_EXPECTATIONS.get(table_name)
            compatible = (
                None
                if expectation is None or category is None
                else category in expectation.allowed_categories
            )
            rows.append(
                {
                    "table": table_name,
                    "units": unit_text,
                    "rows": int(count),
                    "category": category,
                    "parse_status": parse_status,
                    "compatible": compatible,
                    "example_regions": example_regions,
                    "example_technologies": example_technologies,
                }
            )

    inventory = pd.DataFrame(
        rows,
        columns=[
            "table",
            "units",
            "rows",
            "category",
            "parse_status",
            "compatible",
            "example_regions",
            "example_technologies",
        ],
    )
    severity = "ERROR" if strict else "WARNING"

    findings = [
        (
            "INPUT.UNIT.MISSING",
            "Unit-bearing model rows declare units",
            inventory["parse_status"].eq("missing") if not inventory.empty else pd.Series(dtype=bool),
            "Populate the units column using a documented model unit.",
        ),
        (
            "INPUT.UNIT.UNKNOWN",
            "Declared model units are recognized",
            inventory["parse_status"].eq("unknown") if not inventory.empty else pd.Series(dtype=bool),
            "Add a supported unit spelling or register the project-specific unit explicitly.",
        ),
        (
            "INPUT.UNIT.INCOMPATIBLE",
            "Declared model units match table expectations",
            inventory["compatible"].eq(False) if not inventory.empty else pd.Series(dtype=bool),
            "Correct the unit or revise the table-specific unit expectation with model documentation.",
        ),
    ]

    results: list[DiagnosticResult] = []
    for check_id, name, mask, remediation in findings:
        failures = inventory.loc[mask].copy() if not inventory.empty else inventory.copy()
        results.append(
            DiagnosticResult(
                name=name,
                passed=failures.empty,
                severity=severity,
                detail=f"unit_groups={len(inventory)}, failures={len(failures)}",
                failures=failures if not failures.empty else None,
                check_id=check_id,
                stage=DiagnosticStage.INPUT,
                remediation=remediation,
            )
        )

    return inventory, results
