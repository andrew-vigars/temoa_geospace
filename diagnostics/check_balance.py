"""
Post-solve physical and accounting sanity checks for CANOE/TEMOA databases.

This script is intentionally independent of solver status. A MILP solver can
return "optimal" while solving the wrong equations. This diagnostic checks
whether a solved database passes basic physical and cost-accounting invariants.

Checks
------
1. Commodity balance:
   For conserved node commodities, verify that:

       production + imports = consumption + exports + demand

   Source and emission commodities are excluded by default because they are not
   ordinary conserved node commodities.

2. Edge-technology diagnostics:
   Infer all edge technologies directly from the solved SQLite database instead
   of hard-coding transport technology names.

   The script reports:
       * all encoded edge technologies
       * edge technologies with positive solved flow
       * edge technologies with reported OutputNetCapacity
       * edge technologies with edge-region ETLSegment rows
       * edge technologies without reported OutputNetCapacity
       * edge technologies without ETLSegment rows

   For edge technologies with ETLSegment rows, verify that positive solved edge
   flow appears only with positive reported OutputNetCapacity.

   This check does not assert flow <= capacity by default because annual flow
   and capacity may use different dimensional conventions.

3. Objective-cost consistency:
   Verify that the reported objective matches the summed model cost components.
   By default, the gate accepts either discounted or raw cost agreement because
   the current model is single-period and both conventions can be useful during
   development. The script still reports which convention matched. For future
   multi-period runs, use --objective-cost-mode discounted to require discounted
   objective accounting.

Exit codes
----------
0
    All checks passed.

1
    One or more physical or accounting checks failed.

2
    Script or input database error.

Examples
--------
Run the balance gate on a solved database or run output folder:

    python diagnostics/check_balance.py output_files/<run>
    python diagnostics/check_balance.py output_files/<run>/solved_CANOE_geospatial_*.sqlite

Write diagnostic CSV files beside the solved database:

    python diagnostics/check_balance.py output_files/<run> --write-csv
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd


# =============================================================================
# Defaults
# =============================================================================

DEFAULT_ABS_TOL = 1e-6
DEFAULT_REL_TOL = 1e-6

TRANSPORT_EDGE_SEPARATOR = "-"
DEFAULT_EXCLUDED_BALANCE_FLAGS = {"s", "e"}


# =============================================================================
# SQLite helpers
# =============================================================================

def resolve_database_path(path: Path) -> Path:
    """Resolve either a solved SQLite file or a run output directory."""
    path = path.resolve()

    if path.is_file():
        return path

    if path.is_dir():
        candidates = sorted(path.glob("solved_*.sqlite"))

        if len(candidates) == 1:
            return candidates[0].resolve()

        if not candidates:
            raise FileNotFoundError(
                f"No solved_*.sqlite file found in output directory: {path}"
            )

        candidate_list = "\n".join(f"  {candidate}" for candidate in candidates)
        raise ValueError(
            "Multiple solved_*.sqlite files found. Pass one explicitly:\n"
            f"{candidate_list}"
        )

    raise FileNotFoundError(f"Database path does not exist: {path}")


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    return sqlite3.connect(db_path)


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    query = """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
    """
    return con.execute(query, (table,)).fetchone() is not None


def read_table(con: sqlite3.Connection, table: str) -> pd.DataFrame:
    if not table_exists(con, table):
        raise ValueError(f"Required table missing: {table}")

    return pd.read_sql_query(f"SELECT * FROM {table}", con)


def read_optional_table(con: sqlite3.Connection, table: str) -> pd.DataFrame:
    if not table_exists(con, table):
        return pd.DataFrame()

    return pd.read_sql_query(f"SELECT * FROM {table}", con)


# =============================================================================
# General helpers
# =============================================================================

def is_edge_region(region: object) -> bool:
    return isinstance(region, str) and TRANSPORT_EDGE_SEPARATOR in region


def split_edge_region(region: str) -> tuple[str, str]:
    left, right = region.split(TRANSPORT_EDGE_SEPARATOR, maxsplit=1)
    return left, right


def within_tolerance(
    actual: float,
    expected: float,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    diff = abs(actual - expected)
    scale = max(abs(actual), abs(expected), 1.0)
    return diff <= max(abs_tol, rel_tol * scale)


def summarize_failures(df: pd.DataFrame, sort_col: str, max_rows: int) -> pd.DataFrame:
    if df.empty:
        return df

    return (
        df
        .sort_values(sort_col, key=lambda s: s.abs(), ascending=False)
        .head(max_rows)
        .copy()
    )


def fmt_set(values: set[str]) -> str:
    if not values:
        return "(none)"
    return ", ".join(sorted(values))


def print_section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")


# =============================================================================
# Check 1: Commodity balance
# =============================================================================

def build_node_balance(
    flow_in: pd.DataFrame,
    flow_out: pd.DataFrame,
    demand: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build commodity balance by reinterpreting edge pseudo-regions.

    For node regions:
        OutputFlowOut = local production of output_comm
        OutputFlowIn  = local consumption of input_comm

    For edge regions like R1-R2:
        OutputFlowIn  = export from R1 into transport edge
        OutputFlowOut = import into R2 from transport edge

    Balance:
        production + imports = consumption + exports + demand
    """

    required_in = {"scenario", "region", "period", "input_comm", "flow"}
    required_out = {"scenario", "region", "period", "output_comm", "flow"}
    required_demand = {"region", "period", "commodity", "demand"}

    missing_in = required_in - set(flow_in.columns)
    missing_out = required_out - set(flow_out.columns)
    missing_demand = required_demand - set(demand.columns)

    if missing_in:
        raise ValueError(f"OutputFlowIn missing columns: {sorted(missing_in)}")
    if missing_out:
        raise ValueError(f"OutputFlowOut missing columns: {sorted(missing_out)}")
    if missing_demand:
        raise ValueError(f"Demand missing columns: {sorted(missing_demand)}")

    flow_in = flow_in.copy()
    flow_out = flow_out.copy()
    demand = demand.copy()

    flow_in["flow"] = pd.to_numeric(flow_in["flow"], errors="coerce").fillna(0.0)
    flow_out["flow"] = pd.to_numeric(flow_out["flow"], errors="coerce").fillna(0.0)
    demand["demand"] = pd.to_numeric(demand["demand"], errors="coerce").fillna(0.0)

    flow_in["is_edge"] = flow_in["region"].map(is_edge_region)
    flow_out["is_edge"] = flow_out["region"].map(is_edge_region)

    production = (
        flow_out.loc[~flow_out["is_edge"]]
        .groupby(["scenario", "period", "region", "output_comm"], as_index=False)["flow"]
        .sum()
        .rename(columns={"output_comm": "commodity", "flow": "production"})
    )

    consumption = (
        flow_in.loc[~flow_in["is_edge"]]
        .groupby(["scenario", "period", "region", "input_comm"], as_index=False)["flow"]
        .sum()
        .rename(columns={"input_comm": "commodity", "flow": "consumption"})
    )

    edge_in = flow_in.loc[flow_in["is_edge"]].copy()

    if not edge_in.empty:
        edge_in[["region_from", "region_to"]] = edge_in["region"].apply(
            lambda r: pd.Series(split_edge_region(r))
        )

    exports = (
        edge_in
        .groupby(["scenario", "period", "region_from", "input_comm"], as_index=False)["flow"]
        .sum()
        .rename(
            columns={
                "region_from": "region",
                "input_comm": "commodity",
                "flow": "exports",
            }
        )
        if not edge_in.empty
        else pd.DataFrame(columns=["scenario", "period", "region", "commodity", "exports"])
    )

    edge_out = flow_out.loc[flow_out["is_edge"]].copy()

    if not edge_out.empty:
        edge_out[["region_from", "region_to"]] = edge_out["region"].apply(
            lambda r: pd.Series(split_edge_region(r))
        )

    imports = (
        edge_out
        .groupby(["scenario", "period", "region_to", "output_comm"], as_index=False)["flow"]
        .sum()
        .rename(
            columns={
                "region_to": "region",
                "output_comm": "commodity",
                "flow": "imports",
            }
        )
        if not edge_out.empty
        else pd.DataFrame(columns=["scenario", "period", "region", "commodity", "imports"])
    )

    scenarios = sorted(
        set(flow_in["scenario"].dropna().unique()).union(
            set(flow_out["scenario"].dropna().unique())
        )
    )

    demand_grouped = (
        demand
        .groupby(["period", "region", "commodity"], as_index=False)["demand"]
        .sum()
    )

    if scenarios:
        demand_by_scenario = pd.concat(
            [demand_grouped.assign(scenario=scenario) for scenario in scenarios],
            ignore_index=True,
        )
    else:
        demand_by_scenario = pd.DataFrame(
            columns=["scenario", "period", "region", "commodity", "demand"]
        )

    keys = ["scenario", "period", "region", "commodity"]
    balance = None

    for df in [production, imports, consumption, exports, demand_by_scenario]:
        if balance is None:
            balance = df.copy()
        else:
            balance = balance.merge(df, on=keys, how="outer")

    if balance is None:
        balance = pd.DataFrame(columns=keys)

    for col in ["production", "imports", "consumption", "exports", "demand"]:
        if col not in balance.columns:
            balance[col] = 0.0
        balance[col] = pd.to_numeric(balance[col], errors="coerce").fillna(0.0)

    balance["lhs_supply"] = balance["production"] + balance["imports"]
    balance["rhs_use"] = balance["consumption"] + balance["exports"] + balance["demand"]
    balance["residual"] = balance["lhs_supply"] - balance["rhs_use"]

    return balance


def filter_balance_commodities(
    balance: pd.DataFrame,
    commodity: pd.DataFrame,
    excluded_flags: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Remove commodities that should not be treated as conserved node balances.

    Source commodities are exogenous supplies. Emission commodities are usually
    handled through emission/output tables rather than normal commodity balance.
    """

    if commodity.empty:
        balance = balance.copy()
        balance["commodity_flag"] = pd.NA
        return balance, pd.DataFrame()

    required = {"name", "flag"}
    missing = required - set(commodity.columns)

    if missing:
        raise ValueError(f"Commodity table missing columns: {sorted(missing)}")

    commodity_flags = (
        commodity[["name", "flag"]]
        .drop_duplicates()
        .rename(columns={"name": "commodity", "flag": "commodity_flag"})
    )

    balance = balance.merge(commodity_flags, on="commodity", how="left")

    excluded = balance.loc[balance["commodity_flag"].isin(excluded_flags)].copy()
    included = balance.loc[~balance["commodity_flag"].isin(excluded_flags)].copy()

    return included, excluded


def check_commodity_balance(
    flow_in: pd.DataFrame,
    flow_out: pd.DataFrame,
    demand: pd.DataFrame,
    commodity: pd.DataFrame,
    excluded_flags: set[str],
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    balance_raw = build_node_balance(flow_in, flow_out, demand)

    balance, excluded = filter_balance_commodities(
        balance=balance_raw,
        commodity=commodity,
        excluded_flags=excluded_flags,
    )

    balance["scale"] = balance[["lhs_supply", "rhs_use"]].abs().max(axis=1).clip(lower=1.0)
    balance["abs_residual"] = balance["residual"].abs()
    balance["rel_residual"] = balance["abs_residual"] / balance["scale"]

    failures = balance.loc[
        (balance["abs_residual"] > abs_tol)
        & (balance["rel_residual"] > rel_tol)
    ].copy()

    return failures.empty, balance, failures, excluded


# =============================================================================
# Check 2: Edge technology diagnostics inferred from the database
# =============================================================================

def get_edge_techs_from_table(
    table: pd.DataFrame,
    tech_col: str,
    only_positive_col: str | None = None,
    abs_tol: float = DEFAULT_ABS_TOL,
) -> set[str]:
    """Return technologies appearing on edge pseudo-regions in a table."""
    if table.empty or "region" not in table.columns or tech_col not in table.columns:
        return set()

    subset = table.loc[table["region"].map(is_edge_region)].copy()

    if only_positive_col is not None and only_positive_col in subset.columns:
        subset[only_positive_col] = pd.to_numeric(
            subset[only_positive_col],
            errors="coerce",
        ).fillna(0.0)
        subset = subset.loc[subset[only_positive_col] > abs_tol]

    return set(subset[tech_col].dropna().astype(str).unique())


def infer_edge_technology_sets(
    flow_in: pd.DataFrame,
    flow_out: pd.DataFrame,
    efficiency: pd.DataFrame,
    cost_variable: pd.DataFrame,
    cost_invest: pd.DataFrame,
    etl_segment: pd.DataFrame,
    net_capacity: pd.DataFrame,
    abs_tol: float,
) -> tuple[dict[str, set[str]], pd.DataFrame]:
    """
    Infer edge technology categories from the solved/input database.

    No transport technology names are hard-coded. The categories are based on
    where technologies actually appear in the encoded database and solved output.
    """
    edge_tech_sets = {
        "flow_in": get_edge_techs_from_table(flow_in, "tech", "flow", abs_tol),
        "flow_out": get_edge_techs_from_table(flow_out, "tech", "flow", abs_tol),
        "efficiency": get_edge_techs_from_table(efficiency, "tech"),
        "cost_variable": get_edge_techs_from_table(cost_variable, "tech"),
        "cost_invest": get_edge_techs_from_table(cost_invest, "tech"),
        "etl_segment": get_edge_techs_from_table(etl_segment, "tech_or_group"),
        "net_capacity": get_edge_techs_from_table(net_capacity, "tech", "capacity", abs_tol),
    }

    encoded_edge_techs = set().union(
        edge_tech_sets["efficiency"],
        edge_tech_sets["cost_variable"],
        edge_tech_sets["cost_invest"],
        edge_tech_sets["etl_segment"],
        edge_tech_sets["flow_in"],
        edge_tech_sets["flow_out"],
        edge_tech_sets["net_capacity"],
    )

    positive_flow_edge_techs = edge_tech_sets["flow_in"] | edge_tech_sets["flow_out"]
    etl_defined_edge_techs = edge_tech_sets["etl_segment"]
    capacity_reported_edge_techs = edge_tech_sets["net_capacity"]

    summary_rows = []
    for tech in sorted(encoded_edge_techs):
        summary_rows.append(
            {
                "tech": tech,
                "has_positive_edge_flow_in": tech in edge_tech_sets["flow_in"],
                "has_positive_edge_flow_out": tech in edge_tech_sets["flow_out"],
                "has_positive_edge_flow": tech in positive_flow_edge_techs,
                "has_edge_efficiency": tech in edge_tech_sets["efficiency"],
                "has_edge_costvariable": tech in edge_tech_sets["cost_variable"],
                "has_edge_costinvest": tech in edge_tech_sets["cost_invest"],
                "has_edge_etlsegment": tech in etl_defined_edge_techs,
                "has_reported_edge_capacity": tech in capacity_reported_edge_techs,
                "missing_reported_edge_capacity": tech not in capacity_reported_edge_techs,
                "missing_edge_etlsegment": tech not in etl_defined_edge_techs,
            }
        )

    tech_summary = pd.DataFrame(summary_rows)

    return (
        {
            "encoded_edge_techs": encoded_edge_techs,
            "positive_flow_edge_techs": positive_flow_edge_techs,
            "etl_defined_edge_techs": etl_defined_edge_techs,
            "capacity_reported_edge_techs": capacity_reported_edge_techs,
        },
        tech_summary,
    )


def build_positive_edge_flow_summary(
    flow_out: pd.DataFrame,
    tech_summary: pd.DataFrame,
    abs_tol: float,
) -> pd.DataFrame:
    """Summarize positive OutputFlowOut on edge pseudo-regions by tech."""
    required_flow = {"region", "tech", "flow"}
    missing_flow = required_flow - set(flow_out.columns)

    if missing_flow:
        raise ValueError(f"OutputFlowOut missing columns: {sorted(missing_flow)}")

    edge_flow = flow_out.loc[flow_out["region"].map(is_edge_region)].copy()
    edge_flow["flow"] = pd.to_numeric(edge_flow["flow"], errors="coerce").fillna(0.0)
    edge_flow = edge_flow.loc[edge_flow["flow"] > abs_tol].copy()

    if edge_flow.empty:
        return pd.DataFrame(
            columns=["tech", "positive_edge_flow_rows", "total_edge_flow"]
        )

    summary = (
        edge_flow
        .groupby("tech", as_index=False)
        .agg(
            positive_edge_flow_rows=("flow", "size"),
            total_edge_flow=("flow", "sum"),
        )
    )

    if not tech_summary.empty:
        cols = [
            "tech",
            "has_edge_etlsegment",
            "has_reported_edge_capacity",
            "missing_reported_edge_capacity",
            "missing_edge_etlsegment",
        ]
        existing_cols = [col for col in cols if col in tech_summary.columns]
        summary = summary.merge(tech_summary[existing_cols], on="tech", how="left")

    return summary.sort_values("tech").reset_index(drop=True)


def check_etl_defined_edge_flow_capacity(
    flow_out: pd.DataFrame,
    net_capacity: pd.DataFrame,
    etl_defined_edge_techs: set[str],
    abs_tol: float,
    strict_capacity_flow: bool,
) -> tuple[bool, pd.DataFrame]:
    """
    Check whether positive ETLSegment-defined edge flows have capacity.

    This is deliberately based on ETLSegment rows rather than hard-coded names.
    If a technology has edge-region ETLSegment rows, the diagnostic expects
    positive solved edge flow to have positive OutputNetCapacity.
    """
    required_flow = {"scenario", "region", "period", "tech", "vintage", "flow"}
    missing_flow = required_flow - set(flow_out.columns)

    if missing_flow:
        raise ValueError(f"OutputFlowOut missing columns: {sorted(missing_flow)}")

    etl_flow = flow_out.loc[
        flow_out["region"].map(is_edge_region)
        & flow_out["tech"].isin(etl_defined_edge_techs)
    ].copy()

    etl_flow["flow"] = pd.to_numeric(etl_flow["flow"], errors="coerce").fillna(0.0)
    etl_flow = etl_flow.loc[etl_flow["flow"] > abs_tol].copy()

    if etl_flow.empty:
        return True, pd.DataFrame()

    if net_capacity.empty:
        etl_flow["capacity"] = pd.NA
        etl_flow["failure_reason"] = "OutputNetCapacity table missing"
        return False, etl_flow

    required_cap = {"scenario", "region", "period", "tech", "vintage", "capacity"}
    missing_cap = required_cap - set(net_capacity.columns)

    if missing_cap:
        raise ValueError(f"OutputNetCapacity missing columns: {sorted(missing_cap)}")

    cap = net_capacity.copy()
    cap["capacity"] = pd.to_numeric(cap["capacity"], errors="coerce").fillna(0.0)

    keys = ["scenario", "region", "period", "tech", "vintage"]
    cap_grouped = cap.groupby(keys, as_index=False)["capacity"].sum()

    checked = etl_flow.merge(cap_grouped, on=keys, how="left")
    checked["capacity"] = checked["capacity"].fillna(0.0)

    no_capacity = checked.loc[checked["capacity"] <= abs_tol].copy()
    no_capacity["failure_reason"] = (
        "positive ETLSegment-defined edge flow with zero or missing capacity"
    )

    failures = no_capacity

    if strict_capacity_flow:
        exceeds_capacity = checked.loc[
            checked["flow"] > checked["capacity"] + abs_tol
        ].copy()
        exceeds_capacity["failure_reason"] = (
            "ETLSegment-defined edge flow exceeds reported capacity"
        )
        failures = pd.concat([failures, exceeds_capacity], ignore_index=True)

    return failures.empty, failures


# =============================================================================
# Check 3: Objective equals cost components
# =============================================================================

def check_objective_cost_consistency(
    objective: pd.DataFrame,
    output_cost: pd.DataFrame,
    abs_tol: float,
    rel_tol: float,
    objective_cost_mode: str = "either",
) -> tuple[bool, pd.DataFrame]:
    """
    Compare OutputObjective total_system_cost against OutputCost sums.

    Default pass condition for the current single-period model:
        objective matches either:
            sum(d_invest + d_fixed + d_var + d_emiss)
        or:
            sum(invest + fixed + var + emiss)

    Use objective_cost_mode="discounted" when the model becomes genuinely
    multi-period and discounted objective accounting should be required.
    """
    if objective.empty:
        raise ValueError("OutputObjective is empty or missing.")

    if output_cost.empty:
        raise ValueError("OutputCost is empty or missing.")

    required_obj = {"scenario", "objective_name", "total_system_cost"}
    required_cost = {
        "scenario",
        "d_invest",
        "d_fixed",
        "d_var",
        "d_emiss",
        "invest",
        "fixed",
        "var",
        "emiss",
    }

    missing_obj = required_obj - set(objective.columns)
    missing_cost = required_cost - set(output_cost.columns)

    if missing_obj:
        raise ValueError(f"OutputObjective missing columns: {sorted(missing_obj)}")
    if missing_cost:
        raise ValueError(f"OutputCost missing columns: {sorted(missing_cost)}")

    obj = objective.copy()
    cost = output_cost.copy()

    obj["total_system_cost"] = pd.to_numeric(
        obj["total_system_cost"],
        errors="coerce",
    ).fillna(0.0)

    cost_cols = [
        "d_invest",
        "d_fixed",
        "d_var",
        "d_emiss",
        "invest",
        "fixed",
        "var",
        "emiss",
    ]

    for col in cost_cols:
        cost[col] = pd.to_numeric(cost[col], errors="coerce").fillna(0.0)

    cost["discounted_cost_sum"] = (
        cost["d_invest"]
        + cost["d_fixed"]
        + cost["d_var"]
        + cost["d_emiss"]
    )

    cost["raw_cost_sum"] = (
        cost["invest"]
        + cost["fixed"]
        + cost["var"]
        + cost["emiss"]
    )

    cost_by_scenario = (
        cost
        .groupby("scenario", as_index=False)
        .agg(
            discounted_cost_sum=("discounted_cost_sum", "sum"),
            raw_cost_sum=("raw_cost_sum", "sum"),
        )
    )

    comparison = obj.merge(cost_by_scenario, on="scenario", how="left")

    comparison["discounted_diff"] = (
        comparison["total_system_cost"] - comparison["discounted_cost_sum"]
    )
    comparison["raw_diff"] = (
        comparison["total_system_cost"] - comparison["raw_cost_sum"]
    )

    comparison["matches_discounted"] = comparison.apply(
        lambda row: within_tolerance(
            row["total_system_cost"],
            row["discounted_cost_sum"],
            abs_tol,
            rel_tol,
        ),
        axis=1,
    )

    comparison["matches_raw"] = comparison.apply(
        lambda row: within_tolerance(
            row["total_system_cost"],
            row["raw_cost_sum"],
            abs_tol,
            rel_tol,
        ),
        axis=1,
    )

    if objective_cost_mode == "discounted":
        comparison["passed"] = comparison["matches_discounted"]
    elif objective_cost_mode == "either":
        comparison["passed"] = comparison["matches_discounted"] | comparison["matches_raw"]
    else:
        raise ValueError("objective_cost_mode must be one of: 'either', 'discounted'")

    comparison["objective_convention"] = "neither"
    comparison.loc[
        comparison["matches_discounted"] & comparison["matches_raw"],
        "objective_convention",
    ] = "both"
    comparison.loc[
        comparison["matches_discounted"] & ~comparison["matches_raw"],
        "objective_convention",
    ] = "discounted"
    comparison.loc[
        ~comparison["matches_discounted"] & comparison["matches_raw"],
        "objective_convention",
    ] = "raw"

    comparison["diagnostic"] = "matches neither discounted nor raw convention"
    comparison.loc[
        comparison["matches_raw"] & ~comparison["matches_discounted"],
        "diagnostic",
    ] = "matches raw but not discounted - check discounting"
    comparison.loc[
        comparison["matches_discounted"] & comparison["matches_raw"],
        "diagnostic",
    ] = "matches discounted and raw"
    comparison.loc[
        comparison["matches_discounted"] & ~comparison["matches_raw"],
        "diagnostic",
    ] = "matches discounted"

    failures = comparison.loc[~comparison["passed"]].copy()

    return failures.empty, comparison


# =============================================================================
# Main CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run post-solve balance and accounting diagnostics on a CANOE/TEMOA SQLite database."
    )

    parser.add_argument(
        "database",
        type=Path,
        help="Path to solved SQLite database or run output directory containing one solved_*.sqlite file.",
    )

    parser.add_argument(
        "--abs-tol",
        type=float,
        default=DEFAULT_ABS_TOL,
        help=f"Absolute tolerance. Default: {DEFAULT_ABS_TOL}",
    )

    parser.add_argument(
        "--rel-tol",
        type=float,
        default=DEFAULT_REL_TOL,
        help=f"Relative tolerance. Default: {DEFAULT_REL_TOL}",
    )

    parser.add_argument(
        "--objective-cost-mode",
        choices=["either", "discounted"],
        default="either",
        help=(
            "Objective-cost reconciliation rule. Default 'either' is appropriate "
            "for the current single-period model and passes if OutputObjective "
            "matches either discounted or raw OutputCost sums. Use 'discounted' "
            "when the model becomes genuinely multi-period."
        ),
    )

    parser.add_argument(
        "--strict-capacity-flow",
        action="store_true",
        help=(
            "Also fail when ETLSegment-defined edge flow exceeds reported capacity. "
            "Use only if units are confirmed comparable."
        ),
    )

    parser.add_argument(
        "--max-report-rows",
        type=int,
        default=20,
        help="Maximum failure rows to print per check.",
    )

    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Write diagnostic CSV files beside the database.",
    )

    parser.add_argument(
        "--exclude-balance-flags",
        nargs="*",
        default=sorted(DEFAULT_EXCLUDED_BALANCE_FLAGS),
        help=(
            "Commodity flags excluded from conservation balance. "
            "Default excludes source and emission commodities: s e"
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    db_path = resolve_database_path(args.database)
    report_dir = db_path.parent / "diagnostics"

    balance = pd.DataFrame()
    balance_failures = pd.DataFrame()
    balance_excluded = pd.DataFrame()
    tech_summary = pd.DataFrame()
    edge_flow_summary = pd.DataFrame()
    etl_capacity_failures = pd.DataFrame()
    objective_comparison = pd.DataFrame()

    try:
        with connect(db_path) as con:
            flow_in = read_table(con, "OutputFlowIn")
            flow_out = read_table(con, "OutputFlowOut")
            demand = read_table(con, "Demand")

            commodity = read_optional_table(con, "Commodity")
            objective = read_optional_table(con, "OutputObjective")
            output_cost = read_optional_table(con, "OutputCost")
            net_capacity = read_optional_table(con, "OutputNetCapacity")

            efficiency = read_optional_table(con, "Efficiency")
            cost_variable = read_optional_table(con, "CostVariable")
            cost_invest = read_optional_table(con, "CostInvest")
            etl_segment = read_optional_table(con, "ETLSegment")

    except Exception as exc:
        print_section("Balance gate error")
        print("Could not load required data from database:")
        print(f"  {db_path if 'db_path' in locals() else args.database}")
        print(f"\nError: {exc}")
        return 2

    try:
        tech_sets, tech_summary = infer_edge_technology_sets(
            flow_in=flow_in,
            flow_out=flow_out,
            efficiency=efficiency,
            cost_variable=cost_variable,
            cost_invest=cost_invest,
            etl_segment=etl_segment,
            net_capacity=net_capacity,
            abs_tol=args.abs_tol,
        )
    except Exception as exc:
        print_section("Balance gate error")
        print("Could not infer edge technology sets from database.")
        print(f"\nError: {exc}")
        return 2

    print_section("CANOE/TEMOA balance gate")
    print(f"Database: {db_path}")
    print(f"Absolute tolerance: {args.abs_tol}")
    print(f"Relative tolerance: {args.rel_tol}")
    print(f"Objective cost mode: {args.objective_cost_mode}")
    print(f"Encoded edge techs: {fmt_set(tech_sets['encoded_edge_techs'])}")
    print(f"Positive-flow edge techs: {fmt_set(tech_sets['positive_flow_edge_techs'])}")
    print(f"ETLSegment-defined edge techs: {fmt_set(tech_sets['etl_defined_edge_techs'])}")
    print(f"Edge techs with reported capacity: {fmt_set(tech_sets['capacity_reported_edge_techs'])}")

    any_failed = False

    # -------------------------------------------------------------------------
    # Commodity balance
    # -------------------------------------------------------------------------
    print_section("Check 1: commodity balance")

    try:
        balance_passed, balance, balance_failures, balance_excluded = check_commodity_balance(
            flow_in=flow_in,
            flow_out=flow_out,
            demand=demand,
            commodity=commodity,
            excluded_flags=set(args.exclude_balance_flags),
            abs_tol=args.abs_tol,
            rel_tol=args.rel_tol,
        )

        print_check(
            "Node commodity balance",
            balance_passed,
            detail=(
                f"checked {len(balance):,} conserved scenario-period-region-commodity balances; "
                f"excluded {len(balance_excluded):,} source/emission balance rows; "
                f"failures: {len(balance_failures):,}"
            ),
        )

        if not balance_passed:
            any_failed = True
            cols = [
                "scenario",
                "period",
                "region",
                "commodity",
                "production",
                "imports",
                "consumption",
                "exports",
                "demand",
                "residual",
                "rel_residual",
            ]
            print("\nLargest balance failures:")
            print(
                summarize_failures(
                    balance_failures[cols],
                    sort_col="residual",
                    max_rows=args.max_report_rows,
                ).to_string(index=False)
            )

    except Exception as exc:
        any_failed = True
        print_check("Node commodity balance", False, detail=str(exc))

    # -------------------------------------------------------------------------
    # Edge technology diagnostics and ETLSegment capacity sanity
    # -------------------------------------------------------------------------
    print_section("Check 2: edge technology diagnostics")

    try:
        edge_flow_summary = build_positive_edge_flow_summary(
            flow_out=flow_out,
            tech_summary=tech_summary,
            abs_tol=args.abs_tol,
        )

        etl_capacity_passed, etl_capacity_failures = check_etl_defined_edge_flow_capacity(
            flow_out=flow_out,
            net_capacity=net_capacity,
            etl_defined_edge_techs=tech_sets["etl_defined_edge_techs"],
            abs_tol=args.abs_tol,
            strict_capacity_flow=args.strict_capacity_flow,
        )

        print("\nEdge technology classification:")
        if tech_summary.empty:
            print("No edge technologies inferred from database.")
        else:
            cols = [
                "tech",
                "has_positive_edge_flow",
                "has_edge_efficiency",
                "has_edge_costvariable",
                "has_edge_costinvest",
                "has_edge_etlsegment",
                "has_reported_edge_capacity",
                "missing_reported_edge_capacity",
                "missing_edge_etlsegment",
            ]
            existing_cols = [col for col in cols if col in tech_summary.columns]
            print(tech_summary[existing_cols].to_string(index=False))

        print("\nPositive OutputFlowOut edge-flow summary:")
        if edge_flow_summary.empty:
            print("No positive OutputFlowOut edge flows found.")
        else:
            print(edge_flow_summary.to_string(index=False))

        print_check(
            "Positive ETLSegment-defined edge flow has reported capacity",
            etl_capacity_passed,
            detail=f"failures: {len(etl_capacity_failures):,}",
        )

        if not etl_capacity_passed:
            any_failed = True
            cols = [
                "scenario",
                "period",
                "region",
                "tech",
                "vintage",
                "flow",
                "capacity",
                "failure_reason",
            ]
            existing_cols = [col for col in cols if col in etl_capacity_failures.columns]

            print("\nETLSegment-defined edge capacity failures:")
            print(
                etl_capacity_failures[existing_cols]
                .head(args.max_report_rows)
                .to_string(index=False)
            )

    except Exception as exc:
        any_failed = True
        print_check("Edge technology diagnostics", False, detail=str(exc))

    # -------------------------------------------------------------------------
    # Objective consistency
    # -------------------------------------------------------------------------
    print_section("Check 3: objective-cost consistency")

    try:
        objective_passed, objective_comparison = check_objective_cost_consistency(
            objective=objective,
            output_cost=output_cost,
            abs_tol=args.abs_tol,
            rel_tol=args.rel_tol,
            objective_cost_mode=args.objective_cost_mode,
        )

        print_check(
            "Objective matches cost components",
            objective_passed,
            detail=f"checked {len(objective_comparison):,} objective rows",
        )

        cols = [
            "scenario",
            "objective_name",
            "total_system_cost",
            "discounted_cost_sum",
            "raw_cost_sum",
            "discounted_diff",
            "raw_diff",
            "matches_discounted",
            "matches_raw",
            "objective_convention",
            "passed",
            "diagnostic",
        ]

        existing_cols = [col for col in cols if col in objective_comparison.columns]

        print("\nObjective comparison:")
        print(objective_comparison[existing_cols].to_string(index=False))

        if not objective_passed:
            any_failed = True

    except Exception as exc:
        any_failed = True
        print_check("Objective matches cost components", False, detail=str(exc))

    # -------------------------------------------------------------------------
    # Optional CSV outputs
    # -------------------------------------------------------------------------
    if args.write_csv:
        report_dir.mkdir(parents=True, exist_ok=True)

        if not balance.empty:
            balance.to_csv(report_dir / "commodity_balance_all.csv", index=False)

        if not balance_failures.empty:
            balance_failures.to_csv(report_dir / "commodity_balance_failures.csv", index=False)

        if not balance_excluded.empty:
            balance_excluded.to_csv(report_dir / "commodity_balance_excluded.csv", index=False)

        if not tech_summary.empty:
            tech_summary.to_csv(report_dir / "edge_technology_classification.csv", index=False)

        if not edge_flow_summary.empty:
            edge_flow_summary.to_csv(report_dir / "edge_flow_summary.csv", index=False)

        if not etl_capacity_failures.empty:
            etl_capacity_failures.to_csv(
                report_dir / "etl_defined_edge_capacity_failures.csv",
                index=False,
            )

        if not objective_comparison.empty:
            objective_comparison.to_csv(report_dir / "objective_cost_comparison.csv", index=False)

        print_section("CSV reports")
        print(f"Diagnostic CSV files written to: {report_dir}")

    # -------------------------------------------------------------------------
    # Final gate result
    # -------------------------------------------------------------------------
    print_section("Balance gate result")

    if any_failed:
        print("FAILED: one or more physical/accounting checks failed.")
        return 1

    print("PASSED: all physical/accounting checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
