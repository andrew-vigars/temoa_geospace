"""Pre-solve input and encoded-schema diagnostics."""

from geocanoe.diagnostics.input.numeric import (
    DEFAULT_NUMERIC_RULES,
    NumericColumnRule,
    check_numeric_columns,
)
from geocanoe.diagnostics.input.readiness import check_technology_readiness
from geocanoe.diagnostics.input.units import check_table_units, classify_unit
from geocanoe.diagnostics.input.database import (
    SchemaDiagnosticRun,
    read_all_tables,
    run_schema_database_checks,
)

__all__ = [
    "NumericColumnRule",
    "DEFAULT_NUMERIC_RULES",
    "check_numeric_columns",
    "check_technology_readiness",
    "check_table_units",
    "classify_unit",
    "SchemaDiagnosticRun",
    "read_all_tables",
    "run_schema_database_checks",
]
