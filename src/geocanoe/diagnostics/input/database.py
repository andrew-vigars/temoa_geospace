"""Database-level orchestration for pre-solve schema diagnostics."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from geocanoe.diagnostics.input.numeric import (
    DEFAULT_NUMERIC_RULES,
    check_numeric_columns,
)
from geocanoe.diagnostics.input.readiness import check_technology_readiness
from geocanoe.diagnostics.input.units import check_table_units
from geocanoe.diagnostics.models import DiagnosticReport


@dataclass(frozen=True)
class SchemaDiagnosticRun:
    """Structured pre-solve report and its detailed tabular artifacts."""

    report: DiagnosticReport
    technology_readiness: pd.DataFrame
    unit_inventory: pd.DataFrame


def read_all_tables(database: Path) -> dict[str, pd.DataFrame]:
    """Read all tables from an existing SQLite database."""

    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")

    try:
        with sqlite3.connect(database) as connection:
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            tables: dict[str, pd.DataFrame] = {}
            for name in names:
                identifier = '"' + name.replace('"', '""') + '"'
                tables[name] = pd.read_sql_query(
                    f"SELECT * FROM {identifier}",
                    connection,
                )
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise ValueError(f"Could not read SQLite database {database}: {exc}") from exc
    return tables


def run_schema_database_checks(
    database: Path,
    *,
    strict_units: bool = False,
) -> SchemaDiagnosticRun:
    """Run readiness, numeric, and unit checks on an encoded database."""

    resolved = database.resolve()
    tables = read_all_tables(resolved)
    readiness, readiness_results = check_technology_readiness(tables)
    numeric_results = check_numeric_columns(tables, DEFAULT_NUMERIC_RULES)
    unit_inventory, unit_results = check_table_units(tables, strict=strict_units)
    report = DiagnosticReport(
        results=[*readiness_results, *numeric_results, *unit_results],
        metadata={"database": str(resolved), "strict_units": strict_units},
    )
    return SchemaDiagnosticRun(
        report=report,
        technology_readiness=readiness,
        unit_inventory=unit_inventory,
    )
