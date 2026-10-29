"""Recover flow unit labels without changing solved quantities or databases."""
from __future__ import annotations

import sqlite3

import pandas as pd

from geocanoe.registry import Registry


def commodity_units(connection: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Prefer archived commodity metadata; keep current-registry inference separate."""
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
    table = next((name for name in names if name.lower() == "commodity"), None)
    archived = pd.DataFrame()
    if table:
        quoted = table.replace('"', '""')
        archived = pd.read_sql_query(f'SELECT * FROM "{quoted}"', connection)
    registry = pd.read_csv(Registry().resolve_path("commodities"))

    def lookup(frame: pd.DataFrame) -> dict[str, str]:
        if not {"name", "units"}.issubset(frame.columns):
            return {}
        valid = frame.dropna(subset=["name", "units"])
        return {str(row["name"]): str(row["units"]).strip() for _, row in valid.iterrows()
                if str(row["units"]).strip()}

    return lookup(archived), lookup(registry)


def label_flow_units(
    frame: pd.DataFrame, table: str, archived: dict[str, str], registry: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Label inputs by input commodity, outputs by output commodity, with provenance."""
    normalized = table.replace("_", "").lower()
    commodity_column = {
        "outputflowin": "input_comm",
        "outputflowout": "output_comm",
        "outputflowoutsummary": "output_comm",
        "outputcurtailment": "output_comm",
    }.get(normalized)
    if commodity_column is None or commodity_column not in frame:
        return frame, pd.DataFrame()
    result = frame.copy()
    if "units" not in result:
        result["units"] = None
    result["units_source"] = "output_table"
    for lookup, source in [(archived, "archived_commodity"), (registry, "current_registry_inference")]:
        missing = result["units"].isna() | result["units"].astype(str).str.strip().eq("")
        recovered = result[commodity_column].map(lookup)
        fill = missing & recovered.notna()
        result.loc[fill, "units"] = recovered.loc[fill]
        result.loc[fill, "units_source"] = source
    missing = result["units"].isna() | result["units"].astype(str).str.strip().eq("")
    result.loc[missing, "units_source"] = "missing"
    audit = result.rename(columns={commodity_column: "commodity"})[
        ["commodity", "units", "units_source"]
    ].groupby(["commodity", "units", "units_source"], dropna=False).size().reset_index(name="rows")
    audit.insert(0, "table", table)
    return result, audit
