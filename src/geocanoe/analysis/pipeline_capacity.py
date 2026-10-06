"""Derived pseudo-parallel pipeline capacity metrics for solved outputs."""

from __future__ import annotations

import numpy as np
import pandas as pd


PIPELINE_CAPACITY_SUMMARY_COLUMNS = [
    "scenario",
    "period",
    "region",
    "tech",
    "capacity",
    "units",
    "eos_base_capacity",
    "eos_pipeline_count",
]


def _repeating_block_length(values: np.ndarray) -> int:
    """Return the shortest block that exactly tiles a numeric sequence."""

    size = len(values)
    for block_length in range(1, size):
        if size % block_length:
            continue
        repeated = np.tile(values[:block_length], size // block_length)
        if np.allclose(values, repeated, rtol=1e-9, atol=1e-6):
            return block_length
    return size


def infer_pipeline_eos_base_capacities(
    eos_capacity_curves: pd.DataFrame,
) -> dict[str, float]:
    """Infer one-curve terminal capacity from encoded EOS segment widths.

    Stacked curves repeat the complete sequence of segment widths. The shortest
    repeating block therefore identifies one engineering EOS curve without
    requiring the scenario registry or a hard-coded stack count.
    """

    required = {
        "tech_or_group",
        "segment",
        "capacity_lower",
        "capacity_upper",
    }
    missing = required - set(eos_capacity_curves.columns)
    if missing:
        raise ValueError(
            "EOS capacity curves are missing required columns: "
            f"{sorted(missing)}"
        )

    base_capacities: dict[str, float] = {}
    pipeline_rows = eos_capacity_curves.loc[
        eos_capacity_curves["tech_or_group"].astype(str).str.endswith("_PIPE")
    ].copy()

    for tech, rows in pipeline_rows.groupby("tech_or_group", sort=True):
        curve = (
            rows[["segment", "capacity_lower", "capacity_upper"]]
            .drop_duplicates()
            .sort_values("segment")
            .reset_index(drop=True)
        )
        lower = pd.to_numeric(curve["capacity_lower"], errors="coerce")
        upper = pd.to_numeric(curve["capacity_upper"], errors="coerce")
        widths = (upper - lower).to_numpy(dtype=float)
        if (
            len(widths) == 0
            or not np.isfinite(widths).all()
            or (widths <= 0).any()
        ):
            raise ValueError(f"Invalid EOS capacity bounds for {tech}.")

        block_length = _repeating_block_length(widths)
        base_capacity = float(widths[:block_length].sum())
        if not np.isfinite(base_capacity) or base_capacity <= 0:
            raise ValueError(f"Invalid inferred EOS base capacity for {tech}.")
        base_capacities[str(tech)] = base_capacity

    return base_capacities


def _canonical_corridor(region: object) -> str | None:
    endpoints = str(region).split("-")
    if len(endpoints) != 2 or not all(endpoints):
        return None
    return "-".join(sorted(endpoints))


def build_pipeline_capacity_summary(
    net_capacity: pd.DataFrame,
    eos_capacity_curves: pd.DataFrame,
) -> pd.DataFrame:
    """Build canonical corridor capacity and fractional EOS pipeline counts.

    Reverse exchange orientations report the same installed capacity and are
    collapsed with ``max`` rather than summed. Capacity across distinct vintages
    is then summed before dividing by the inferred one-curve terminal capacity.
    """

    required = {"region", "tech", "capacity"}
    missing = required - set(net_capacity.columns)
    if missing:
        raise ValueError(
            "OutputNetCapacity is missing columns required for the pipeline "
            f"capacity summary: {sorted(missing)}"
        )

    base_capacities = infer_pipeline_eos_base_capacities(eos_capacity_curves)
    capacity = net_capacity.loc[
        net_capacity["tech"].astype(str).isin(base_capacities)
    ].copy()
    capacity["capacity"] = pd.to_numeric(capacity["capacity"], errors="coerce")
    capacity = capacity.loc[
        capacity["capacity"].notna() & (capacity["capacity"] > 0)
    ].copy()
    capacity["region"] = capacity["region"].map(_canonical_corridor)
    capacity = capacity.loc[capacity["region"].notna()].copy()

    if capacity.empty:
        return pd.DataFrame(columns=PIPELINE_CAPACITY_SUMMARY_COLUMNS)

    identity = [
        column
        for column in ["scenario", "period", "region", "tech"]
        if column in capacity.columns
    ]
    orientation_identity = [*identity]
    if "vintage" in capacity.columns:
        orientation_identity.append("vintage")

    aggregations: dict[str, tuple[str, str]] = {
        "capacity": ("capacity", "max"),
    }
    if "units" in capacity.columns:
        aggregations["units"] = ("units", "first")

    by_vintage = capacity.groupby(
        orientation_identity,
        as_index=False,
        dropna=False,
    ).agg(**aggregations)

    final_aggregations: dict[str, tuple[str, str]] = {
        "capacity": ("capacity", "sum"),
    }
    if "units" in by_vintage.columns:
        final_aggregations["units"] = ("units", "first")
    summary = by_vintage.groupby(
        identity,
        as_index=False,
        dropna=False,
    ).agg(**final_aggregations)
    if "units" not in summary.columns:
        summary["units"] = None

    summary["eos_base_capacity"] = summary["tech"].map(base_capacities)
    summary["eos_pipeline_count"] = (
        summary["capacity"] / summary["eos_base_capacity"]
    )
    return summary[PIPELINE_CAPACITY_SUMMARY_COLUMNS].sort_values(
        [column for column in ["scenario", "period", "tech", "region"] if column in summary],
        kind="stable",
    ).reset_index(drop=True)
