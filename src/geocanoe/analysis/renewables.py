"""Post-solve monthly renewable availability, weighted by installed MW.

Availability is potential output before dispatch and curtailment, not generation.
The exact Silver bundle recorded by the Gold manifest supplies spatial profiles.
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.preprocessing.wind_options import profile_field


def weighted_profiles(capacity, profiles, provinces):
    """Sum vintages; use installed MW for both grid and provincial mixtures."""
    if profiles.duplicated(["region", "tech", "month"]).any():
        raise ValueError("Duplicate monthly renewable profiles.")
    keys = ["scenario", "period", "region", "tech"]
    capacity = capacity.groupby(keys, as_index=False).capacity.sum()
    capacity = capacity.loc[capacity.capacity > 0].copy()
    months_per_pair = (
        profiles.groupby(["region", "tech"]).month.nunique().rename("months")
    )
    coverage = capacity.merge(
        months_per_pair, on=["region", "tech"], how="left", validate="many_to_one"
    )
    if not coverage.months.eq(profiles.month.nunique()).all():
        raise ValueError("Missing renewable profile months for deployed capacity.")
    grid = capacity.merge(
        profiles, on=["region", "tech"], how="left", validate="many_to_many"
    )
    if (
        grid.factor.isna().any()
        or not np.isfinite(grid.factor).all()
        or not grid.factor.between(0, 1).all()
    ):
        raise ValueError("Missing renewable profile values.")
    if set(capacity.region) - set(profiles.region):
        raise ValueError("Silver profiles do not cover solved renewable capacity.")
    grid["province"] = grid.region.map(provinces)
    if grid.province.isna().any():
        raise ValueError("Missing province for a deployed renewable grid cell.")
    grid["resource"] = np.where(grid.tech.str.startswith("WIND_GEN"), "wind", "solar")
    grid["available_mw"] = grid.capacity * grid.factor
    grid["available_mwh"] = grid.available_mw * grid.hours
    dimensions = ["scenario", "period", "province", "resource", "month", "hours"]
    province = grid.groupby(dimensions, as_index=False)[
        ["capacity", "available_mw", "available_mwh"]
    ].sum()
    province["factor"] = province.available_mw / province.capacity
    return grid, province


def export_renewable_profiles(database, output_dir):
    """Write grid/province CSV tables and PNG plots for renewable model runs."""
    database, output_dir = Path(database), Path(output_dir)
    sidecar = database.with_suffix(".manifest.json")
    if not sidecar.exists():
        return {"status": "skipped", "reason": "No Gold schema manifest"}
    config = json.loads(sidecar.read_text())["resolved_gold_configuration"]
    bundle = config.get("renewables_bundle_dir")
    if not bundle:
        return {"status": "skipped", "reason": "No renewable Silver bundle"}
    import geopandas as gpd
    import xarray as xr
    import matplotlib.pyplot as plt

    source = Path(bundle).parent
    manifest = json.loads((source / "manifest.json").read_text())
    with sqlite3.connect(database) as connection:
        capacity = pd.read_sql_query(
            "SELECT scenario, period, region, tech, capacity FROM output_net_capacity "
            "WHERE tech = 'SOLAR_GEN' OR tech = 'WIND_GEN' OR tech LIKE 'WIND_GEN_T%'",
            connection,
        )
    if capacity.empty or not (capacity.capacity > 0).any():
        return {"status": "skipped", "reason": "No deployed wind or solar capacity"}
    # Reuse the centroid-to-province mapping used for provincial demand allocation.
    geography = gpd.read_file(
        config["gasoline_demand_path"], layer="regional_gasoline_demand"
    )
    provinces = geography.set_index("region").province
    if not provinces.index.is_unique:
        raise ValueError("Province mapping must contain one row per grid cell.")
    profiles, months = [], []
    for name, digest in sorted(manifest["monthly_sha256"].items()):
        path = source / name
        if sha256(path) != digest:
            raise ValueError(f"Renewable profile checksum mismatch: {path}")
        with xr.open_dataset(path, engine="netcdf4") as weather:
            hours = weather.sizes["time"]
            month = pd.Timestamp(weather.time.values[0]).month
            months.append(month)
            means = weather.mean("time", skipna=False)
            for tech in capacity.tech.unique():
                frame = (
                    means[profile_field(tech)].to_dataframe(name="factor").reset_index()
                )
                frame["tech"], frame["month"], frame["hours"] = tech, month, hours
                profiles.append(frame[["region", "tech", "month", "hours", "factor"]])
    if sorted(months) != list(range(1, 13)):
        raise ValueError("Renewable profiles require all twelve months exactly once.")
    grid, province = weighted_profiles(capacity, pd.concat(profiles), provinces)
    destination = output_dir / "renewable_profiles"
    destination.mkdir(parents=True, exist_ok=True)
    grid.to_csv(destination / "grid_monthly.csv", index=False)
    province.to_csv(destination / "province_monthly.csv", index=False)
    for plot_index, ((scenario, period), rows) in enumerate(
        province.groupby(["scenario", "period"])
    ):
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
        for ax, resource in zip(axes, ["wind", "solar"]):
            for label, values in rows.loc[rows.resource == resource].groupby(
                "province"
            ):
                ax.plot(values.month, values.factor, label=label)
            ax.set(
                title=resource.title(),
                xlabel="Month",
                ylabel="Capacity-weighted CF",
                ylim=(0, 1),
            )
            if ax.lines:
                ax.legend(fontsize=7)
        fig.suptitle(f"{scenario}, {period}: potential availability before curtailment")
        fig.tight_layout()
        # Numeric index avoids interpreting scenario names as filesystem paths.
        fig.savefig(destination / f"province_profile_{plot_index:03d}.png", dpi=150)
        plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5))
    for resource, rows in grid.groupby("resource"):
        ax.scatter(rows.capacity, rows.factor, s=3, alpha=0.25, label=resource)
    ax.set(
        xlabel="Installed capacity per grid cell and technology (MW)",
        ylabel="Monthly capacity factor",
        ylim=(0, 1),
    )
    ax.legend()
    fig.tight_layout()
    fig.savefig(destination / "grid_profiles.png", dpi=150)
    plt.close(fig)
    return {
        "status": "success",
        "directory": str(destination),
        "weighting": "solved installed MW",
        "temporal_resolution": "monthly",
    }
