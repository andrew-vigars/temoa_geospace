# =============================================================================
# temoa_etl_outputcost_diagnostic_v2.py
#
# Purpose:
#   Diagnose ETL exchange-tech investment costs across:
#     1. raw V_ETLPeriodCost
#     2. expected endpoint allocation with accumulation
#     3. current ExchangeTechCostLedger output
#     4. poll_cost_results()
#     5. final OutputCost table
#
# Key correction versus previous diagnostic:
#   Multiple physical corridors can allocate cost to the same endpoint key:
#       (region, period, tech, vintage)
#   Therefore endpoint costs must be accumulated, not overwritten.
# =============================================================================

import csv
import shutil
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from pyomo.environ import value

from db_mgmt import update_db_paths
from temoa.temoa_model.temoa_sequencer import TemoaSequencer
from temoa.temoa_model.temoa_mode import TemoaMode
from temoa.temoa_model.table_data_puller import (
    poll_cost_results,
    poll_objective,
    loan_costs,
)
from temoa.temoa_model.exchange_tech_cost_ledger import (
    ExchangeTechCostLedger,
    CostType,
)
from temoa.temoa_model import temoa_rules


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_PATH = (
    PROJECT_ROOT
    / "temoa"
    / "data_files"
    / "my_configs"
    / "config_sample.toml"
)

SCHEMA_DIR = PROJECT_ROOT / "data_files" / "processed" / "schema"
OUTPUT_ROOT = PROJECT_ROOT / "output_files"
EPS = 1e-6


def safe_value(x, default=0.0):
    try:
        return float(value(x))
    except Exception:
        return default


def split_exchange_region(r):
    parts = str(r).split("-")
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def directed_flow(M, rr, period, tech, vintage):
    if tech not in M.tech_annual:
        return safe_value(
            sum(
                M.V_FlowOut[rr, period, s, d, i, tech, vintage, o]
                for s in M.TimeSeason[period]
                for d in M.time_of_day
                for i in M.processInputs[rr, period, tech, vintage]
                for o in M.processOutputsByInput[rr, period, tech, vintage, i]
            )
        )

    return safe_value(
        sum(
            M.V_FlowOutAnnual[rr, period, i, tech, vintage, o]
            for i in M.processInputs[rr, period, tech, vintage]
            for o in M.processOutputsByInput[rr, period, tech, vintage, i]
        )
    )


def use_ratio_importer(M, r1, r2, period, tech, vintage):
    rr1 = f"{r1}-{r2}"
    rr2 = f"{r2}-{r1}"

    f1 = directed_flow(M, rr1, period, tech, vintage)
    f2 = directed_flow(M, rr2, period, tech, vintage)

    if f1 + f2 > 0:
        return f1 / (f1 + f2)

    return 0.5


def add_nested_cost(target, key, cost_type, amount):
    target[key][cost_type] += float(amount)


def read_outputcost_endpoint_totals(db_path, scenario=None):
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(OutputCost)").fetchall()]
    lower = {c.lower(): c for c in cols}

    required = ["region", "period", "tech", "vintage"]
    for c in required:
        if c not in lower:
            raise RuntimeError(f"OutputCost missing required column: {c}")

    invest_col = lower.get("invest")
    d_invest_col = lower.get("d_invest")

    if invest_col is None or d_invest_col is None:
        raise RuntimeError("OutputCost must contain invest and d_invest columns.")

    scenario_clause = ""
    params = []

    if scenario is not None and "scenario" in lower:
        scenario_clause = f"WHERE {lower['scenario']} = ?"
        params.append(scenario)

    qry = f"""
        SELECT
            {lower['region']} AS region,
            {lower['period']} AS period,
            {lower['tech']} AS tech,
            {lower['vintage']} AS vintage,
            SUM(COALESCE({invest_col}, 0.0)) AS invest,
            SUM(COALESCE({d_invest_col}, 0.0)) AS d_invest
        FROM OutputCost
        {scenario_clause}
        GROUP BY
            {lower['region']},
            {lower['period']},
            {lower['tech']},
            {lower['vintage']}
    """

    out = {}
    for region, period, tech, vintage, invest, d_invest in cur.execute(qry, params):
        out[(region, int(period), tech, int(vintage))] = {
            CostType.INVEST: float(invest or 0.0),
            CostType.D_INVEST: float(d_invest or 0.0),
        }

    con.close()
    return out


def select_schema():
    schemas = sorted(SCHEMA_DIR.glob("*.sqlite"))

    if not schemas:
        raise FileNotFoundError(f"No SQLite schemas found in {SCHEMA_DIR}")

    print("\nAvailable schemas:")
    for i, path in enumerate(schemas):
        print(f"[{i}] {path.name}")

    while True:
        try:
            idx = int(input("\nSelect schema number: "))
            if 0 <= idx < len(schemas):
                return schemas[idx]
        except ValueError:
            pass
        print("Invalid selection.")


def main():
    db_path = select_schema()

    run_timestamp = datetime.today().strftime("%Y-%m-%d_%H%M")
    schema_name = db_path.stem.replace("CANOE_geospatial_", "")
    run_id = f"{run_timestamp}_{schema_name}_ETL_OUTPUTCOST_DIAG_V2"
    output_dir = OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nSelected schema: {db_path.name}")
    print(f"Run ID: {run_id}")

    update_db_paths(CONFIG_PATH, str(db_path), create_backup=True)

    shutil.copy2(db_path, output_dir / f"input_{db_path.name}")

    ts = TemoaSequencer(
        config_file=CONFIG_PATH,
        output_path=output_dir,
        mode_override=TemoaMode.PERFECT_FORESIGHT,
        silent=True,
    )
    ts.start()

    M = ts.pf_solved_instance

    if M is None:
        raise RuntimeError("No solved model instance returned.")

    shutil.copy2(db_path, output_dir / f"output_{db_path.name}")

    print("\nSolve complete. Running ETL OutputCost diagnostic v2...\n")

    print("=" * 80)
    print("STAGE 0: Solve status")
    print("=" * 80)

    results = ts.pf_results
    print(f"Solver status: {results.solver.status}")
    print(f"Termination condition: {results.solver.termination_condition}")

    for name, val in poll_objective(M):
        print(f"Objective {name}: {val:,.6f}")

    print("\n" + "=" * 80)
    print("STAGE 1: Build expected ETL endpoint allocations with accumulation")
    print("=" * 80)

    p_0 = min(M.time_optimize)
    p_e = M.time_future.last()
    GDR = safe_value(M.GlobalDiscountRate)
    LLN = M.LoanLifetimeProcess

    expected = defaultdict(lambda: defaultdict(float))
    contributors = defaultdict(list)
    corridor_rows = []

    n_seen = 0
    n_nonzero = 0
    n_mirror_skipped = 0
    n_added = 0

    for r, p, t in M.ETLPeriodCost_rpt:
        n_seen += 1

        raw_cost = safe_value(M.V_ETLPeriodCost[r, p, t])

        if raw_cost < EPS:
            continue

        n_nonzero += 1

        if "-" in r and not temoa_rules._etl_is_canonical_exchange_entry(M, r, p, t):
            n_mirror_skipped += 1
            continue

        endpoints = split_exchange_region(r)

        if endpoints is None:
            continue

        r1, r2 = endpoints

        r0, t0 = M.etlClusterProcess[r, p, t]

        model_loan_cost, undiscounted_cost = loan_costs(
            loan_rate=safe_value(M.LoanRate[r0, t0, p]),
            loan_life=safe_value(LLN[r0, t0, p]),
            capacity=1,
            invest_cost=raw_cost,
            process_life=safe_value(M.LifetimeProcess[r0, t0, p]),
            p_0=p_0,
            p_e=p_e,
            global_discount_rate=GDR,
            vintage=p,
        )

        ratio = use_ratio_importer(M, r1, r2, p, t, p)

        alloc_r1_invest = undiscounted_cost * (1.0 - ratio)
        alloc_r2_invest = undiscounted_cost * ratio

        alloc_r1_d_invest = model_loan_cost * (1.0 - ratio)
        alloc_r2_d_invest = model_loan_cost * ratio

        key1 = (r1, p, t, p)
        key2 = (r2, p, t, p)

        add_nested_cost(expected, key1, CostType.INVEST, alloc_r1_invest)
        add_nested_cost(expected, key2, CostType.INVEST, alloc_r2_invest)
        add_nested_cost(expected, key1, CostType.D_INVEST, alloc_r1_d_invest)
        add_nested_cost(expected, key2, CostType.D_INVEST, alloc_r2_d_invest)

        contributors[key1].append(r)
        contributors[key2].append(r)

        corridor_rows.append({
            "edge_region": r,
            "period": p,
            "tech": t,
            "raw_V_ETLPeriodCost": raw_cost,
            "loan_invest_expected_total": undiscounted_cost,
            "d_loan_invest_expected_total": model_loan_cost,
            "flow_use_ratio_to_r2": ratio,
            "r1": r1,
            "r2": r2,
            "r1_invest_expected": alloc_r1_invest,
            "r2_invest_expected": alloc_r2_invest,
            "r1_d_invest_expected": alloc_r1_d_invest,
            "r2_d_invest_expected": alloc_r2_d_invest,
        })

        n_added += 1

    print(f"ETLPeriodCost_rpt entries seen: {n_seen:,}")
    print(f"Nonzero raw ETL entries: {n_nonzero:,}")
    print(f"Mirror entries skipped: {n_mirror_skipped:,}")
    print(f"Canonical exchange entries allocated: {n_added:,}")
    print(f"Endpoint keys with expected ETL cost: {len(expected):,}")

    collision_keys = {
        k: v for k, v in contributors.items()
        if len(v) > 1
    }

    print(f"Endpoint keys receiving cost from >1 corridor: {len(collision_keys):,}")

    corridor_csv = output_dir / "stage1_expected_etl_corridor_allocations.csv"
    with open(corridor_csv, "w", newline="") as f:
        fields = list(corridor_rows[0].keys()) if corridor_rows else []
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(corridor_rows)

    collision_csv = output_dir / "stage1_endpoint_collision_keys.csv"
    with open(collision_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "region", "period", "tech", "vintage",
            "n_contributing_corridors",
            "contributing_corridors",
            "expected_invest_sum",
            "expected_d_invest_sum",
        ])
        for key, links in sorted(collision_keys.items()):
            writer.writerow([
                key[0], key[1], key[2], key[3],
                len(links),
                ";".join(sorted(links)),
                expected[key].get(CostType.INVEST, 0.0),
                expected[key].get(CostType.D_INVEST, 0.0),
            ])

    print(f"Written: {corridor_csv}")
    print(f"Written: {collision_csv}")

    print("\n" + "=" * 80)
    print("STAGE 2: Reproduce current ExchangeTechCostLedger behavior")
    print("=" * 80)

    ledger = ExchangeTechCostLedger(M)

    for row in corridor_rows:
        r = row["edge_region"]
        p = row["period"]
        t = row["tech"]

        ledger.add_cost_record(
            r,
            period=p,
            tech=t,
            vintage=p,
            cost=row["d_loan_invest_expected_total"],
            cost_type=CostType.D_INVEST,
        )
        ledger.add_cost_record(
            r,
            period=p,
            tech=t,
            vintage=p,
            cost=row["loan_invest_expected_total"],
            cost_type=CostType.INVEST,
        )

    ledger_entries = ledger.get_entries()

    print(f"Ledger endpoint rows returned: {len(ledger_entries):,}")

    print("\n" + "=" * 80)
    print("STAGE 3: Compare expected accumulated ETL vs ledger output")
    print("=" * 80)

    ledger_mismatch_rows = []

    all_keys = sorted(set(expected.keys()) | set(ledger_entries.keys()))

    for key in all_keys:
        exp_i = expected.get(key, {}).get(CostType.INVEST, 0.0)
        exp_di = expected.get(key, {}).get(CostType.D_INVEST, 0.0)

        led_i = ledger_entries.get(key, {}).get(CostType.INVEST, 0.0)
        led_di = ledger_entries.get(key, {}).get(CostType.D_INVEST, 0.0)

        diff_i = exp_i - led_i
        diff_di = exp_di - led_di

        if abs(diff_i) > 1e-5 or abs(diff_di) > 1e-5:
            links = contributors.get(key, [])
            ledger_mismatch_rows.append({
                "region": key[0],
                "period": key[1],
                "tech": key[2],
                "vintage": key[3],
                "n_contributing_corridors": len(links),
                "contributing_corridors": ";".join(sorted(links)),
                "expected_invest_accumulated": exp_i,
                "ledger_invest": led_i,
                "invest_diff_expected_minus_ledger": diff_i,
                "expected_d_invest_accumulated": exp_di,
                "ledger_d_invest": led_di,
                "d_invest_diff_expected_minus_ledger": diff_di,
            })

    ledger_mismatch_csv = output_dir / "stage3_expected_vs_ledger_mismatches.csv"
    with open(ledger_mismatch_csv, "w", newline="") as f:
        fields = [
            "region", "period", "tech", "vintage",
            "n_contributing_corridors",
            "contributing_corridors",
            "expected_invest_accumulated",
            "ledger_invest",
            "invest_diff_expected_minus_ledger",
            "expected_d_invest_accumulated",
            "ledger_d_invest",
            "d_invest_diff_expected_minus_ledger",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ledger_mismatch_rows)

    print(f"Mismatched endpoint keys: {len(ledger_mismatch_rows):,}")
    print(f"Written: {ledger_mismatch_csv}")

    print("\n" + "=" * 80)
    print("STAGE 4: Compare expected accumulated ETL vs poll_cost_results()")
    print("=" * 80)

    regular_entries, exchange_entries = poll_cost_results(M, p_0=None)

    poll_mismatch_rows = []

    for key in all_keys:
        exp_i = expected.get(key, {}).get(CostType.INVEST, 0.0)
        exp_di = expected.get(key, {}).get(CostType.D_INVEST, 0.0)

        poll_i = exchange_entries.get(key, {}).get(CostType.INVEST, 0.0)
        poll_di = exchange_entries.get(key, {}).get(CostType.D_INVEST, 0.0)

        diff_i = exp_i - poll_i
        diff_di = exp_di - poll_di

        if abs(diff_i) > 1e-5 or abs(diff_di) > 1e-5:
            links = contributors.get(key, [])
            poll_mismatch_rows.append({
                "region": key[0],
                "period": key[1],
                "tech": key[2],
                "vintage": key[3],
                "n_contributing_corridors": len(links),
                "contributing_corridors": ";".join(sorted(links)),
                "expected_invest_accumulated": exp_i,
                "poll_invest": poll_i,
                "invest_diff_expected_minus_poll": diff_i,
                "expected_d_invest_accumulated": exp_di,
                "poll_d_invest": poll_di,
                "d_invest_diff_expected_minus_poll": diff_di,
            })

    poll_mismatch_csv = output_dir / "stage4_expected_vs_poll_cost_results_mismatches.csv"
    with open(poll_mismatch_csv, "w", newline="") as f:
        fields = [
            "region", "period", "tech", "vintage",
            "n_contributing_corridors",
            "contributing_corridors",
            "expected_invest_accumulated",
            "poll_invest",
            "invest_diff_expected_minus_poll",
            "expected_d_invest_accumulated",
            "poll_d_invest",
            "d_invest_diff_expected_minus_poll",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(poll_mismatch_rows)

    print(f"Mismatched endpoint keys: {len(poll_mismatch_rows):,}")
    print(f"Written: {poll_mismatch_csv}")

    print("\n" + "=" * 80)
    print("STAGE 5: Compare expected accumulated ETL vs final OutputCost table")
    print("=" * 80)

    db_costs = read_outputcost_endpoint_totals(
        db_path=db_path,
        scenario=getattr(ts.config, "scenario", None),
    )

    db_mismatch_rows = []

    for key in all_keys:
        exp_i = expected.get(key, {}).get(CostType.INVEST, 0.0)
        exp_di = expected.get(key, {}).get(CostType.D_INVEST, 0.0)

        db_i = db_costs.get(key, {}).get(CostType.INVEST, 0.0)
        db_di = db_costs.get(key, {}).get(CostType.D_INVEST, 0.0)

        diff_i = exp_i - db_i
        diff_di = exp_di - db_di

        if abs(diff_i) > 1e-5 or abs(diff_di) > 1e-5:
            links = contributors.get(key, [])
            db_mismatch_rows.append({
                "region": key[0],
                "period": key[1],
                "tech": key[2],
                "vintage": key[3],
                "n_contributing_corridors": len(links),
                "contributing_corridors": ";".join(sorted(links)),
                "expected_invest_accumulated": exp_i,
                "outputcost_invest": db_i,
                "invest_diff_expected_minus_outputcost": diff_i,
                "expected_d_invest_accumulated": exp_di,
                "outputcost_d_invest": db_di,
                "d_invest_diff_expected_minus_outputcost": diff_di,
            })

    db_mismatch_csv = output_dir / "stage5_expected_vs_outputcost_mismatches.csv"
    with open(db_mismatch_csv, "w", newline="") as f:
        fields = [
            "region", "period", "tech", "vintage",
            "n_contributing_corridors",
            "contributing_corridors",
            "expected_invest_accumulated",
            "outputcost_invest",
            "invest_diff_expected_minus_outputcost",
            "expected_d_invest_accumulated",
            "outputcost_d_invest",
            "d_invest_diff_expected_minus_outputcost",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(db_mismatch_rows)

    print(f"Mismatched endpoint keys: {len(db_mismatch_rows):,}")
    print(f"Written: {db_mismatch_csv}")

    print("\n" + "=" * 80)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 80)

    print(f"Canonical ETL corridor entries allocated: {n_added:,}")
    print(f"Endpoint collision keys: {len(collision_keys):,}")
    print(f"Expected-vs-ledger mismatches: {len(ledger_mismatch_rows):,}")
    print(f"Expected-vs-poll_cost_results mismatches: {len(poll_mismatch_rows):,}")
    print(f"Expected-vs-OutputCost mismatches: {len(db_mismatch_rows):,}")

    if collision_keys and ledger_mismatch_rows:
        print(
            "\nLikely failure mode: ExchangeTechCostLedger.get_entries() is overwriting "
            "rather than accumulating when multiple corridors allocate the same CostType "
            "to the same endpoint key."
        )

    print(f"\nDiagnostic run complete: {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise