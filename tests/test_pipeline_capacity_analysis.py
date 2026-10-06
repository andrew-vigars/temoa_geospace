from __future__ import annotations

import pandas as pd
import pytest

from geocanoe.analysis.pipeline_capacity import (
    build_pipeline_capacity_summary,
    infer_pipeline_eos_base_capacities,
)


def _three_stack_curve() -> pd.DataFrame:
    widths = [10.0, 20.0] * 3
    lower = 0.0
    rows = []
    for segment, width in enumerate(widths):
        rows.append(
            {
                "tech_or_group": "CO2_PIPE",
                "segment": segment,
                "capacity_lower": lower,
                "capacity_upper": lower + width,
            }
        )
        lower += width
    return pd.DataFrame(rows)


def test_infers_original_curve_capacity_from_stacked_segments() -> None:
    capacities = infer_pipeline_eos_base_capacities(_three_stack_curve())

    assert capacities == {"CO2_PIPE": 30.0}


def test_pipeline_count_can_be_fractional_without_double_counting_directions() -> None:
    net_capacity = pd.DataFrame(
        [
            {
                "scenario": "S",
                "period": 2030,
                "region": "A-B",
                "tech": "CO2_PIPE",
                "vintage": 2030,
                "capacity": 81.0,
                "units": "kt/year",
            },
            {
                "scenario": "S",
                "period": 2030,
                "region": "B-A",
                "tech": "CO2_PIPE",
                "vintage": 2030,
                "capacity": 81.0,
                "units": "kt/year",
            },
        ]
    )

    summary = build_pipeline_capacity_summary(net_capacity, _three_stack_curve())

    assert len(summary) == 1
    assert summary.loc[0, "region"] == "A-B"
    assert summary.loc[0, "capacity"] == 81.0
    assert summary.loc[0, "eos_base_capacity"] == 30.0
    assert summary.loc[0, "eos_pipeline_count"] == pytest.approx(2.7)
