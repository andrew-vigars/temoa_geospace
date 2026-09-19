"""Technology-level readiness checks for encoded model inputs."""

from __future__ import annotations

import pandas as pd

from geocanoe.diagnostics.models import DiagnosticResult, DiagnosticStage


TECH_PARAMETER_COLUMNS = {
    "Efficiency": "tech",
    "CostVariable": "tech",
    "CostInvest": "tech",
    "ETLSegment": "tech_or_group",
    "LimitCapacity": "tech_or_group",
    "ExistingCapacity": "tech",
}


def _technology_members(table: pd.DataFrame, column: str) -> set[str]:
    if table.empty or column not in table.columns:
        return set()
    return set(table[column].dropna().astype(str))


def _demand_sink_technologies(
    efficiency: pd.DataFrame,
    commodity: pd.DataFrame,
) -> set[str]:
    """Infer zero-cost demand-delivery technologies from commodity flags.

    A demand sink converts a non-source commodity exclusively into demand
    commodities. This separates a delivery link such as ``GSL_DEMAND`` from a
    source-backed technology such as ``GSL_BACKUP`` without hard-coding names.
    """

    required_eff = {"tech", "input_comm", "output_comm"}
    if efficiency.empty or not required_eff <= set(efficiency.columns):
        return set()
    if commodity.empty or not {"name", "flag"} <= set(commodity.columns):
        return set()

    flags = dict(
        zip(
            commodity["name"].astype(str),
            commodity["flag"].astype(str),
            strict=False,
        )
    )
    sinks: set[str] = set()
    for tech, rows in efficiency.groupby("tech", dropna=True):
        input_flags = {flags.get(str(value)) for value in rows["input_comm"].dropna()}
        output_flags = {flags.get(str(value)) for value in rows["output_comm"].dropna()}
        if output_flags == {"d"} and "s" not in input_flags:
            sinks.add(str(tech))
    return sinks


def check_technology_readiness(
    tables: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[DiagnosticResult]]:
    """Build a technology coverage matrix and evaluate minimum readiness.

    All declared technologies require an efficiency representation. Technologies
    with bounded capacity require either ordinary investment-cost rows or an ETL
    cost curve. Every technology except an inferred zero-cost demand sink requires
    at least one cost representation in ``CostVariable``, ``CostInvest``, or
    ``ETLSegment``.
    """

    technology = tables.get("Technology", pd.DataFrame())
    if technology.empty or "tech" not in technology.columns:
        failure = DiagnosticResult(
            name="Technology readiness matrix can be built",
            passed=False,
            severity="ERROR",
            detail="Technology table or Technology.tech column is missing",
            ran=False,
            check_id="INPUT.TECH.READINESS_AVAILABLE",
            stage=DiagnosticStage.INPUT,
        )
        return pd.DataFrame(), [failure]

    coverage = {
        table_name: _technology_members(tables.get(table_name, pd.DataFrame()), tech_col)
        for table_name, tech_col in TECH_PARAMETER_COLUMNS.items()
    }
    demand_sinks = _demand_sink_technologies(
        tables.get("Efficiency", pd.DataFrame()),
        tables.get("Commodity", pd.DataFrame()),
    )

    matrix = technology[
        [
            column
            for column in ["tech", "exchange", "unlim_cap"]
            if column in technology.columns
        ]
    ].copy()
    matrix["tech"] = matrix["tech"].astype(str)
    matrix = matrix.drop_duplicates(subset="tech", keep="first")
    for table_name, members in coverage.items():
        matrix[f"has_{table_name.lower()}"] = matrix["tech"].isin(members)

    if "unlim_cap" in matrix.columns:
        unlimited = pd.to_numeric(matrix["unlim_cap"], errors="coerce").eq(1)
    else:
        unlimited = pd.Series(False, index=matrix.index)

    matrix["is_demand_sink"] = matrix["tech"].isin(demand_sinks)
    matrix["requires_capacity_cost"] = ~unlimited
    matrix["has_any_cost"] = (
        matrix["has_costvariable"]
        | matrix["has_costinvest"]
        | matrix["has_etlsegment"]
    )
    matrix["has_capacity_cost"] = (
        matrix["has_costinvest"] | matrix["has_etlsegment"]
    )
    matrix["missing_efficiency"] = ~matrix["has_efficiency"]
    matrix["missing_cost_representation"] = (
        ~matrix["is_demand_sink"] & ~matrix["has_any_cost"]
    )
    matrix["missing_capacity_cost"] = (
        matrix["requires_capacity_cost"] & ~matrix["has_capacity_cost"]
    )
    failure_columns = [
        "missing_efficiency",
        "missing_cost_representation",
        "missing_capacity_cost",
    ]
    matrix["ready"] = ~matrix[failure_columns].any(axis=1)
    matrix = matrix.sort_values("tech").reset_index(drop=True)

    checks = [
        (
            "INPUT.TECH.EFFICIENCY_MISSING",
            "Every technology has an efficiency representation",
            "missing_efficiency",
            "Add at least one valid Efficiency row for each declared technology.",
        ),
        (
            "INPUT.TECH.COST_REPRESENTATION_MISSING",
            "Every non-demand technology has a cost representation",
            "missing_cost_representation",
            "Represent technology costs in CostVariable, CostInvest, or ETLSegment.",
        ),
        (
            "INPUT.TECH.CAPACITY_COST_MISSING",
            "Every bounded technology has a capacity-cost representation",
            "missing_capacity_cost",
            "Add CostInvest rows or an ETLSegment curve, or explicitly mark the technology as unlimited capacity.",
        ),
    ]

    results: list[DiagnosticResult] = []
    for check_id, name, failure_column, remediation in checks:
        failures = matrix.loc[matrix[failure_column]].copy()
        results.append(
            DiagnosticResult(
                name=name,
                passed=failures.empty,
                severity="ERROR",
                detail=f"technologies_checked={len(matrix)}, failures={len(failures)}",
                failures=failures if not failures.empty else None,
                check_id=check_id,
                stage=DiagnosticStage.INPUT,
                remediation=remediation,
            )
        )

    return matrix, results
