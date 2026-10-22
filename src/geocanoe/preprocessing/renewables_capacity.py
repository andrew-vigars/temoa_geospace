"""Explicit regional resource bounds; never infer land eligibility from CFs."""

import numpy as np
import pandas as pd


def capacity_bounds(path, regions, period):
    """Convert an audited eligible-area/density input into regional MW bounds."""
    data = pd.read_csv(path)
    required = {
        "region",
        "tech",
        "eligible_area_km2",
        "density_mw_per_km2",
        "basis",
        "source",
    }
    if not required.issubset(data.columns):
        raise ValueError(f"Capacity input requires columns {sorted(required)}.")
    expected = {(str(r), t) for r in regions for t in ("WIND_GEN", "SOLAR_GEN")}
    data["region"] = data.region.astype(str)
    if (
        data.duplicated(["region", "tech"]).any()
        or set(zip(data.region, data.tech)) != expected
    ):
        raise ValueError(
            "Capacity input must cover every region/technology exactly once."
        )
    for column in ("eligible_area_km2", "density_mw_per_km2"):
        values = pd.to_numeric(data[column], errors="raise")
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(
                "Capacity area and density must be finite and nonnegative."
            )
        data[column] = values
    for column in ("basis", "source"):
        if (
            data[column].isna().any()
            or data[column].astype(str).str.strip().eq("").any()
        ):
            raise ValueError("Capacity assumptions require a basis and source.")
    data["capacity_mw"] = data.eligible_area_km2 * data.density_mw_per_km2
    if not np.isfinite(data.capacity_mw).all():
        raise ValueError("Capacity product must be finite.")
    rows = [
        dict(
            region=r.region,
            period=period,
            tech_or_group=r.tech,
            operator="le",
            capacity=r.capacity_mw,
            units="MW",
        )
        for r in data.itertuples()
    ]
    return rows, data
