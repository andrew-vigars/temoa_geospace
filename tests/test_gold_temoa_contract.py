from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from geocanoe.schema.build import (
    clear_output_tables,
    export_sqlite,
    load_empty_temoa_v4_tables,
)


def test_installed_temoa_v4_and_eos_schemas_are_the_gold_contract() -> None:
    tables = load_empty_temoa_v4_tables()

    assert len(tables) == 77
    assert tables["metadata"].set_index("element")["value"].to_dict() == {
        "DB_MAJOR": 4,
        "DB_MINOR": 0,
    }
    assert list(tables["cost_invest_eos"].columns) == [
        "region",
        "tech_or_group",
        "segment",
        "capacity_lower",
        "capacity_upper",
        "cost_lower",
        "cost_upper",
        "units",
        "notes",
    ]
    assert "ETLSegment" not in tables
    assert "DataSet" not in tables


def test_geocanoe_export_uses_only_v4_and_eos_tables(tmp_path: Path) -> None:
    tables = load_empty_temoa_v4_tables()
    tables["region"] = pd.DataFrame(
        {"region": ["R1"], "notes": ["test region"]}
    )
    tables["commodity"] = pd.DataFrame(
        {
            "name": ["fuel"],
            "flag": ["a"],
            "description": ["test fuel"],
            "data_id": ["legacy provenance is intentionally dropped"],
        }
    )
    tables["cost_invest_eos"] = pd.DataFrame(
        {
            "region": ["R1"],
            "tech_or_group": ["TECH"],
            "segment": [0],
            "capacity_lower": [0.0],
            "capacity_upper": [10.0],
            "cost_lower": [0.0],
            "cost_upper": [20.0],
            "units": [None],
            "notes": ["test curve"],
        }
    )
    clear_output_tables(tables)

    encoded_database = tmp_path / "geocanoe-v4.sqlite"
    export_sqlite(tables, encoded_database)

    with sqlite3.connect(encoded_database) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        commodity_columns = [
            row[1]
            for row in connection.execute('PRAGMA table_info("commodity")')
        ]
        version = dict(
            connection.execute(
                "SELECT element, value FROM metadata "
                "WHERE element IN ('DB_MAJOR', 'DB_MINOR')"
            ).fetchall()
        )

    assert version == {"DB_MAJOR": 4, "DB_MINOR": 0}
    assert commodity_columns == ["name", "flag", "description", "units"]
    assert {
        "cost_invest_eos",
        "cost_fixed_eos",
        "cost_variable_eos",
    }.issubset(table_names)
    assert "ETLSegment" not in table_names
    assert "DataSet" not in table_names
