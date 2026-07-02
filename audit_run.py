# =============================================================================
# audit_run.py
#
# General post-solve audit for CANOE/TEMOA SQLite outputs.
# Does not rerun model.
# =============================================================================

import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt

try:
    import networkx as nx
except ImportError:
    nx = None


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "output_files"
AUDIT_ROOT = PROJECT_ROOT / "audit_outputs"


# =============================================================================
# Basic helpers
# =============================================================================

def select_db():
    dbs = sorted(OUTPUT_ROOT.glob("**/*.sqlite"))

    if not dbs:
        raise FileNotFoundError(f"No .sqlite files found under {OUTPUT_ROOT}")

    print("\nAvailable solved databases:")
    for i, db in enumerate(dbs):
        print(f"[{i}] {db.relative_to(PROJECT_ROOT)} ({db.stat().st_size / 1e6:.1f} MB)")

    while True:
        try:
            idx = int(input("\nSelect database number: "))
            if 0 <= idx < len(dbs):
                return dbs[idx]
        except ValueError:
            pass
        print("Invalid selection.")


def table_exists(con, table):
    q = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    return con.execute(q, (table,)).fetchone() is not None


def table_cols(con, table):
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def read(con, query, params=None):
    return pd.read_sql_query(query, con, params=params or [])


def safe_ident(name):
    return '"' + name.replace('"', '""') + '"'


def first_existing_col(con, table, candidates):
    c = table_cols(con, table)
    for candidate in candidates:
        if candidate in c:
            return candidate
    return None


def make_audit_dir(db_path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = AUDIT_ROOT / db_path.parent.name / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# =============================================================================
# Discovery
# =============================================================================

def discover_cost_columns(con):
    if not table_exists(con, "OutputCost"):
        return []

    c = table_cols(con, "OutputCost")

    candidates = [
        "invest",
        "d_invest",
        "fixed",
        "d_fixed",
        "var",
        "d_var",
        "emiss",
        "d_emiss",
        "emissions",
        "d_emissions",
    ]

    return [x for x in candidates if x in c]


def discover_exchange_techs(con):
    techs = set()

    for table in ["OutputFlowOut", "OutputCost", "OutputCapacity"]:
        if not table_exists(con, table):
            continue

        c = table_cols(con, table)

        if {"region", "tech"} <= set(c):
            df = read(con, f"""
                SELECT DISTINCT tech
                FROM {safe_ident(table)}
                WHERE region LIKE '%-%'
            """)
            techs.update(df["tech"].dropna())

    return sorted(techs)


def discover_etl_techs(con):
    if not table_exists(con, "ETLSegment"):
        return []

    tech_col = first_existing_col(
        con,
        "ETLSegment",
        ["tech", "technology", "tech_or_group"],
    )

    if tech_col is None:
        print(f"WARNING: Could not identify technology column in ETLSegment: {table_cols(con, 'ETLSegment')}")
        return []

    df = read(con, f"SELECT DISTINCT {safe_ident(tech_col)} AS tech FROM ETLSegment")
    return sorted(df["tech"].dropna())


# =============================================================================
# Generic audits
# =============================================================================

def audit_table_counts(con, out_dir):
    tables = read(con, """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)

    rows = []

    for table in tables["name"]:
        try:
            n_rows = con.execute(f"SELECT COUNT(*) FROM {safe_ident(table)}").fetchone()[0]
        except Exception:
            n_rows = None

        rows.append({"table": table, "n_rows": n_rows})

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "audit_table_counts.csv", index=False)
    return df


def audit_schema_columns(con, out_dir):
    tables = read(con, """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)

    rows = []

    for table in tables["name"]:
        for col in table_cols(con, table):
            rows.append({"table": table, "column": col})

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "audit_schema_columns.csv", index=False)
    return df


def audit_output_tables(con, out_dir):
    output_tables = read(con, """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name LIKE 'Output%'
        ORDER BY name
    """)

    rows = []

    for table in output_tables["name"]:
        try:
            n_rows = con.execute(f"SELECT COUNT(*) FROM {safe_ident(table)}").fetchone()[0]
        except Exception:
            n_rows = None

        rows.append({
            "output_table": table,
            "n_rows": n_rows,
            "columns": ";".join(table_cols(con, table)),
        })

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "output_table_inventory.csv", index=False)
    return df


# =============================================================================
# Cost audits
# =============================================================================

def audit_outputcost(con, out_dir):
    if not table_exists(con, "OutputCost"):
        return None

    c = table_cols(con, "OutputCost")

    if not {"region", "tech"} <= set(c):
        print("WARNING: OutputCost exists but lacks region/tech columns.")
        return None

    cost_cols = discover_cost_columns(con)

    if not cost_cols:
        print("WARNING: OutputCost has no recognized cost columns.")
        return None

    select_sums = ",\n".join(
        f"SUM(COALESCE({safe_ident(col)}, 0.0)) AS {col}_sum"
        for col in cost_cols
    )

    summary = read(con, f"""
        SELECT
            CASE
                WHEN region LIKE '%-%' THEN 'edge_region'
                ELSE 'node_region'
            END AS region_type,
            tech,
            COUNT(*) AS n_rows,
            {select_sums}
        FROM OutputCost
        GROUP BY region_type, tech
        ORDER BY tech, region_type
    """)

    summary.to_csv(out_dir / "outputcost_by_region_type_and_tech.csv", index=False)

    component_summary = read(con, f"""
        SELECT
            tech,
            COUNT(*) AS n_rows,
            {select_sums}
        FROM OutputCost
        GROUP BY tech
        ORDER BY tech
    """)

    component_summary.to_csv(out_dir / "outputcost_by_tech_components.csv", index=False)

    checks = []
    for col in cost_cols:
        ident = safe_ident(col)
        checks.append(f"""
            SELECT
                '{col}' AS column_name,
                SUM(CASE WHEN {ident} IS NULL THEN 1 ELSE 0 END) AS n_null,
                SUM(CASE WHEN {ident} < 0 THEN 1 ELSE 0 END) AS n_negative
            FROM OutputCost
        """)

    null_negative = read(con, "\nUNION ALL\n".join(checks))
    null_negative.to_csv(out_dir / "outputcost_null_negative_summary.csv", index=False)

    edge_rows = read(con, """
        SELECT *
        FROM OutputCost
        WHERE region LIKE '%-%'
        ORDER BY tech, region, period, vintage
    """)
    edge_rows.to_csv(out_dir / "outputcost_edge_region_rows.csv", index=False)

    return summary


def audit_etl_costs(con, out_dir):
    if not table_exists(con, "OutputCost"):
        return None

    etl_techs = discover_etl_techs(con)

    if not etl_techs:
        return None

    cost_cols = discover_cost_columns(con)

    if not cost_cols:
        return None

    marks = ",".join("?" for _ in etl_techs)

    select_sums = ",\n".join(
        f"SUM(COALESCE({safe_ident(col)}, 0.0)) AS {col}_sum"
        for col in cost_cols
    )

    df = read(con, f"""
        SELECT
            CASE
                WHEN region LIKE '%-%' THEN 'edge_region'
                ELSE 'node_region'
            END AS region_type,
            tech,
            COUNT(*) AS n_rows,
            {select_sums}
        FROM OutputCost
        WHERE tech IN ({marks})
        GROUP BY region_type, tech
        ORDER BY tech, region_type
    """, etl_techs)

    df.to_csv(out_dir / "etl_tech_outputcost_summary.csv", index=False)
    return df


# =============================================================================
# Flow and graph audits
# =============================================================================

def audit_exchange_flows(con, out_dir):
    if not table_exists(con, "OutputFlowOut"):
        return None

    c = table_cols(con, "OutputFlowOut")

    if not {"region", "tech", "period", "vintage"} <= set(c):
        print("WARNING: OutputFlowOut exists but lacks required columns.")
        return None

    flow_col = first_existing_col(con, "OutputFlowOut", ["flow", "value"])

    if flow_col is None:
        print("WARNING: OutputFlowOut has no recognized flow/value column.")
        return None

    flows = read(con, f"""
        SELECT
            region,
            tech,
            period,
            vintage,
            SUM(COALESCE({safe_ident(flow_col)}, 0.0)) AS flow_sum
        FROM OutputFlowOut
        WHERE region LIKE '%-%'
        GROUP BY region, tech, period, vintage
        HAVING ABS(flow_sum) > 1e-9
        ORDER BY tech, region, period, vintage
    """)

    flows.to_csv(out_dir / "positive_exchange_flow_edges.csv", index=False)

    if flows.empty:
        return flows

    endpoints = flows["region"].str.split("-", n=1, expand=True)
    flows["r1"] = endpoints[0]
    flows["r2"] = endpoints[1]

    degree_rows = []

    for tech, g in flows.groupby("tech"):
        undirected_edges = set()

        for _, row in g.iterrows():
            undirected_edges.add(tuple(sorted((row["r1"], row["r2"]))))

        degree = {}

        for r1, r2 in undirected_edges:
            degree[r1] = degree.get(r1, 0) + 1
            degree[r2] = degree.get(r2, 0) + 1

        for region, deg in degree.items():
            degree_rows.append({
                "tech": tech,
                "region": region,
                "incident_positive_flow_edges": deg,
            })

    degree_df = pd.DataFrame(degree_rows)

    degree_df = degree_df.sort_values(
        ["tech", "incident_positive_flow_edges", "region"],
        ascending=[True, False, True],
    )

    degree_df.to_csv(out_dir / "exchange_endpoint_degrees.csv", index=False)

    degree_summary = (
        degree_df
        .groupby(["tech", "incident_positive_flow_edges"], as_index=False)
        .agg(n_regions=("region", "count"))
        .sort_values(["tech", "incident_positive_flow_edges"])
    )

    degree_summary.to_csv(out_dir / "exchange_endpoint_degree_summary.csv", index=False)

    return flows


def audit_network_metrics(out_dir):
    flow_path = out_dir / "positive_exchange_flow_edges.csv"

    if not flow_path.exists() or nx is None:
        if nx is None:
            print("WARNING: networkx not installed. Skipping network metrics.")
        return None

    flows = pd.read_csv(flow_path)

    if flows.empty:
        return None

    endpoints = flows["region"].str.split("-", n=1, expand=True)
    flows["r1"] = endpoints[0]
    flows["r2"] = endpoints[1]

    metric_rows = []
    node_rows = []
    edge_rows = []

    for tech, g in flows.groupby("tech"):
        G = nx.Graph()

        for _, row in g.iterrows():
            r1 = row["r1"]
            r2 = row["r2"]
            flow = float(row["flow_sum"])

            if G.has_edge(r1, r2):
                G[r1][r2]["flow_sum"] += flow
                G[r1][r2]["n_directed_records"] += 1
            else:
                G.add_edge(
                    r1,
                    r2,
                    flow_sum=flow,
                    n_directed_records=1,
                )

        if G.number_of_nodes() == 0:
            continue

        components = list(nx.connected_components(G))
        largest_component = max(components, key=len)

        degree = dict(G.degree())
        weighted_degree = dict(G.degree(weight="flow_sum"))

        if G.number_of_nodes() <= 5000:
            betweenness = nx.betweenness_centrality(G, weight=None)
        else:
            betweenness = {n: None for n in G.nodes}

        metric_rows.append({
            "tech": tech,
            "n_nodes": G.number_of_nodes(),
            "n_edges": G.number_of_edges(),
            "n_connected_components": nx.number_connected_components(G),
            "largest_component_nodes": len(largest_component),
            "largest_component_share": len(largest_component) / G.number_of_nodes(),
            "average_degree": sum(dict(G.degree()).values()) / G.number_of_nodes(),
            "max_degree": max(degree.values()) if degree else 0,
            "total_edge_flow_sum": sum(d["flow_sum"] for _, _, d in G.edges(data=True)),
        })

        for node in G.nodes:
            node_rows.append({
                "tech": tech,
                "region": node,
                "degree": degree.get(node, 0),
                "weighted_degree_flow": weighted_degree.get(node, 0.0),
                "betweenness": betweenness.get(node),
            })

        for r1, r2, data in G.edges(data=True):
            edge_rows.append({
                "tech": tech,
                "r1": r1,
                "r2": r2,
                "flow_sum": data.get("flow_sum", 0.0),
                "n_directed_records": data.get("n_directed_records", 0),
            })

    metrics = pd.DataFrame(metric_rows)
    nodes = pd.DataFrame(node_rows)
    edges = pd.DataFrame(edge_rows)

    metrics.to_csv(out_dir / "network_metrics_by_tech.csv", index=False)
    nodes.to_csv(out_dir / "network_node_metrics.csv", index=False)
    edges.to_csv(out_dir / "network_edges_undirected.csv", index=False)

    return metrics


# =============================================================================
# Plots
# =============================================================================

def plot_cost_component(con, out_dir, component):
    if not table_exists(con, "OutputCost"):
        return None

    if component not in discover_cost_columns(con):
        return None

    df = read(con, f"""
        SELECT
            tech,
            SUM(COALESCE({safe_ident(component)}, 0.0)) AS value
        FROM OutputCost
        GROUP BY tech
        HAVING ABS(value) > 1e-9
        ORDER BY value DESC
    """)

    if df.empty:
        return None

    top = df.head(20).copy()

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(top["tech"], top["value"])
    ax.set_xlabel(f"{component} reported cost")
    ax.set_ylabel("Technology")
    ax.set_title(f"Top technology costs: {component}")
    ax.invert_yaxis()
    fig.tight_layout()

    fig_path = out_dir / f"plot_top_technology_costs_{component}.png"
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)

    top.to_csv(out_dir / f"plot_data_top_technology_costs_{component}.csv", index=False)

    return fig_path


def plot_total_cost_by_tech(con, out_dir):
    if not table_exists(con, "OutputCost"):
        return None

    cost_cols = discover_cost_columns(con)

    needed = [
        x for x in ["invest", "fixed", "var", "emiss", "emissions"]
        if x in cost_cols
    ]

    if not needed:
        return None

    total_expr = " + ".join(
        f"COALESCE({safe_ident(col)}, 0.0)"
        for col in needed
    )

    df = read(con, f"""
        SELECT
            tech,
            SUM({total_expr}) AS total_cost
        FROM OutputCost
        GROUP BY tech
        HAVING ABS(total_cost) > 1e-9
        ORDER BY total_cost DESC
    """)

    if df.empty:
        return None

    top = df.head(20).copy()

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(top["tech"], top["total_cost"])
    ax.set_xlabel("Total undiscounted reported cost")
    ax.set_ylabel("Technology")
    ax.set_title("Top reported technology costs")
    ax.invert_yaxis()
    fig.tight_layout()

    fig_path = out_dir / "plot_top_technology_costs_total.png"
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)

    top.to_csv(out_dir / "plot_data_top_technology_costs_total.csv", index=False)

    return fig_path


def plot_exchange_degree(out_dir):
    degree_path = out_dir / "exchange_endpoint_degrees.csv"

    if not degree_path.exists():
        return None

    degree_df = pd.read_csv(degree_path)

    if degree_df.empty:
        return None

    top = degree_df.sort_values(
        "incident_positive_flow_edges",
        ascending=False,
    ).head(25)

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(
        top["tech"] + " | " + top["region"],
        top["incident_positive_flow_edges"],
    )
    ax.set_xlabel("Incident positive-flow exchange edges")
    ax.set_ylabel("Technology | region")
    ax.set_title("Most connected exchange endpoints")
    ax.invert_yaxis()
    fig.tight_layout()

    fig_path = out_dir / "plot_top_exchange_endpoint_degrees.png"
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)

    return fig_path


def plot_network_weighted_degree(out_dir):
    node_path = out_dir / "network_node_metrics.csv"

    if not node_path.exists():
        return None

    df = pd.read_csv(node_path)

    if df.empty:
        return None

    top = df.sort_values(
        "weighted_degree_flow",
        ascending=False,
    ).head(25)

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(
        top["tech"] + " | " + top["region"],
        top["weighted_degree_flow"],
    )
    ax.set_xlabel("Weighted degree by total incident flow")
    ax.set_ylabel("Technology | region")
    ax.set_title("Highest-flow exchange endpoints")
    ax.invert_yaxis()
    fig.tight_layout()

    fig_path = out_dir / "plot_top_exchange_weighted_degrees.png"
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)

    return fig_path


# =============================================================================
# Run report
# =============================================================================

def write_run_report(con, db_path, out_dir):
    lines = []

    def add(text=""):
        lines.append(text)

    table_counts_path = out_dir / "audit_table_counts.csv"
    null_neg_path = out_dir / "outputcost_null_negative_summary.csv"
    network_metrics_path = out_dir / "network_metrics_by_tech.csv"
    outputcost_path = out_dir / "outputcost_by_tech_components.csv"

    add("# CANOE/TEMOA post-solve audit report")
    add("")
    add("## Database")
    add("")
    add(f"- Database: `{db_path}`")
    add(f"- Audit output: `{out_dir}`")
    add(f"- Audit timestamp: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`")
    add("")

    if table_counts_path.exists():
        table_counts = pd.read_csv(table_counts_path)
        add("## Table inventory")
        add("")
        add(f"- Tables: {len(table_counts):,}")
        add(f"- Total rows across tables: {int(table_counts['n_rows'].fillna(0).sum()):,}")
        add("")

    if outputcost_path.exists():
        costs = pd.read_csv(outputcost_path)
        cost_cols = [c for c in costs.columns if c.endswith("_sum")]

        add("## OutputCost summary")
        add("")
        for col in cost_cols:
            total = costs[col].fillna(0).sum()
            add(f"- {col}: {total:,.6g}")
        add("")

        total_cols = [c for c in ["invest_sum", "fixed_sum", "var_sum", "emiss_sum", "emissions_sum"] if c in costs.columns]
        if total_cols:
            costs["total_screen_cost"] = costs[total_cols].fillna(0).sum(axis=1)
            top = costs.sort_values("total_screen_cost", ascending=False).head(10)

            add("### Top technologies by screened undiscounted cost")
            add("")
            for _, row in top.iterrows():
                add(f"- {row['tech']}: {row['total_screen_cost']:,.6g}")
            add("")

    if null_neg_path.exists():
        nn = pd.read_csv(null_neg_path)
        add("## Data quality checks")
        add("")
        add(f"- Null OutputCost values: {int(nn['n_null'].fillna(0).sum()):,}")
        add(f"- Negative OutputCost values: {int(nn['n_negative'].fillna(0).sum()):,}")
        add("")

    edge_cost_path = out_dir / "outputcost_edge_region_rows.csv"
    if edge_cost_path.exists():
        edge_costs = pd.read_csv(edge_cost_path)
        add("## Exchange-cost placement")
        add("")
        add(f"- Edge-region OutputCost rows: {len(edge_costs):,}")
        add("")

    if network_metrics_path.exists():
        net = pd.read_csv(network_metrics_path)
        add("## Exchange network summary")
        add("")
        for _, row in net.iterrows():
            add(
                f"- {row['tech']}: "
                f"{int(row['n_nodes'])} nodes, "
                f"{int(row['n_edges'])} edges, "
                f"{int(row['n_connected_components'])} component(s), "
                f"largest component share {row['largest_component_share']:.3f}"
            )
        add("")

    exchange_techs = discover_exchange_techs(con)
    etl_techs = discover_etl_techs(con)

    add("## Discovered model structure")
    add("")
    add(f"- Exchange technologies: {', '.join(exchange_techs) if exchange_techs else 'None detected'}")
    add(f"- ETL technologies: {', '.join(etl_techs) if etl_techs else 'None detected'}")
    add("")

    md = "\n".join(lines)

    (out_dir / "run_report.md").write_text(md, encoding="utf-8")
    (out_dir / "run_report.txt").write_text(md, encoding="utf-8")

    return md


# =============================================================================
# Main
# =============================================================================

def main():
    db_path = select_db()
    out_dir = make_audit_dir(db_path)

    print(f"\nAuditing: {db_path}")
    print(f"Audit output: {out_dir}")

    con = sqlite3.connect(db_path)

    try:
        table_counts = audit_table_counts(con, out_dir)
        audit_schema_columns(con, out_dir)
        output_inventory = audit_output_tables(con, out_dir)

        audit_outputcost(con, out_dir)
        audit_etl_costs(con, out_dir)
        audit_exchange_flows(con, out_dir)
        audit_network_metrics(out_dir)

        plot_total_cost_by_tech(con, out_dir)
        for component in discover_cost_columns(con):
            plot_cost_component(con, out_dir, component)

        plot_exchange_degree(out_dir)
        plot_network_weighted_degree(out_dir)

        summary = {
            "database": str(db_path),
            "audit_output": str(out_dir),
            "n_tables": len(table_counts),
            "n_output_tables": len(output_inventory),
            "exchange_techs_discovered": ";".join(discover_exchange_techs(con)),
            "etl_techs_discovered": ";".join(discover_etl_techs(con)),
            "networkx_available": nx is not None,
        }

        pd.DataFrame([summary]).to_csv(out_dir / "audit_summary.csv", index=False)

        write_run_report(con, db_path, out_dir)

        print("\nCore outputs written:")
        print("  run_report.md")
        print("  audit_summary.csv")
        print("  audit_table_counts.csv")
        print("  audit_schema_columns.csv")
        print("  output_table_inventory.csv")
        print("  outputcost_by_region_type_and_tech.csv")
        print("  outputcost_by_tech_components.csv")
        print("  outputcost_null_negative_summary.csv")
        print("  outputcost_edge_region_rows.csv")
        print("  etl_tech_outputcost_summary.csv")
        print("  positive_exchange_flow_edges.csv")
        print("  exchange_endpoint_degrees.csv")
        print("  exchange_endpoint_degree_summary.csv")
        print("  network_metrics_by_tech.csv")
        print("  network_node_metrics.csv")
        print("  network_edges_undirected.csv")
        print("  plot_top_technology_costs_total.png")
        print("  plot_top_exchange_endpoint_degrees.png")
        print("  plot_top_exchange_weighted_degrees.png")

    finally:
        con.close()


if __name__ == "__main__":
    main()