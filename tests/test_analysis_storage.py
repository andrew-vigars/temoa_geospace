from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from geocanoe.analysis.exports import (
    build_co2_storage_summary,
    export_output_tables,
)


def test_co2_storage_summary_aggregates_injection_output() -> None:
    flow_out = pd.DataFrame(
        {
            "scenario": ["S", "S", "S"],
            "period": [1, 1, 1],
            "region": ["R0", "R0", "R1"],
            "input_comm": ["co2", "co2", "elc"],
            "tech": ["CO2_INJECT", "CO2_INJECT", "ELC_GEN"],
            "output_comm": ["co2_stored", "co2_stored", "elc"],
            "flow": [2.0, 3.0, 10.0],
        }
    )

    summary = build_co2_storage_summary(flow_out)

    assert len(summary) == 1
    assert summary.iloc[0]["region"] == "R0"
    assert summary.iloc[0]["flow"] == 5.0
    assert summary.iloc[0]["output_comm"] == "co2_stored"


def test_output_workbook_includes_co2_storage_summary(tmp_path: Path) -> None:
    database_path = tmp_path / "solved.sqlite"
    flow_out = pd.DataFrame(
        {
            "scenario": ["S"],
            "period": [1],
            "region": ["R0"],
            "input_comm": ["co2"],
            "tech": ["CO2_INJECT"],
            "output_comm": ["co2_stored"],
            "flow": [4.0],
        }
    )
    with sqlite3.connect(database_path) as connection:
        flow_out.to_sql("OutputFlowOut", connection, index=False)
        pd.DataFrame(
            [
                {
                    "scenario": "S",
                    "period": 1,
                    "region": region,
                    "tech": "CO2_PIPE",
                    "vintage": 1,
                    "capacity": 81.0,
                    "units": "kt/year",
                }
                for region in ["A-B", "B-A"]
            ]
        ).to_sql("OutputNetCapacity", connection, index=False)
        pd.DataFrame(
            [
                {
                    "tech_or_group": "CO2_PIPE",
                    "segment": segment,
                    "capacity_lower": lower,
                    "capacity_upper": upper,
                }
                for segment, (lower, upper) in enumerate(
                    [(0, 10), (10, 30), (30, 40), (40, 60), (60, 70), (70, 90)]
                )
            ]
        ).to_sql("cost_invest_eos", connection, index=False)

    [workbook_path] = export_output_tables(database_path, tmp_path)

    with pd.ExcelFile(workbook_path) as workbook:
        assert "CO2StorageSummary" in workbook.sheet_names
        assert "PipelineCapacitySummary" in workbook.sheet_names
        summary = pd.read_excel(workbook, sheet_name="CO2StorageSummary")
        pipeline_summary = pd.read_excel(
            workbook,
            sheet_name="PipelineCapacitySummary",
        )
    assert summary[["region", "tech", "output_comm", "flow"]].to_dict(
        "records"
    ) == [
        {
            "region": "R0",
            "tech": "CO2_INJECT",
            "output_comm": "co2_stored",
            "flow": 4,
        }
    ]
    assert pipeline_summary.loc[0, "region"] == "A-B"
    assert pipeline_summary.loc[0, "capacity"] == 81
    assert pipeline_summary.loc[0, "eos_pipeline_count"] == 2.7
