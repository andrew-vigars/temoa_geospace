"""Annual/monthly CF previews from verified Silver outputs, without conversion."""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from tqdm.auto import tqdm

from geocanoe.acquisition.era5_renewables import manifest, sha256


def month_statistics(ds):
    """Per-region RMS error incurred by replacing hours with their monthly mean."""
    values = ds[["wind_cf", "solar_cf"]].to_array().values
    if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
        raise ValueError("Preview requires finite CFs between zero and one.")
    time = pd.DatetimeIndex(ds.time.values)
    if len(time) < 2 or not time.is_unique or not time.is_monotonic_increasing:
        raise ValueError("Preview requires ordered unique hourly time coordinates.")
    if not (np.diff(time.values) == np.timedelta64(1, "h")).all():
        raise ValueError("Preview requires continuous hourly data.")
    rows = []
    means = ds.mean("time").to_dataframe()[list(ds.data_vars)]
    profiles = (
        [(f"wind_cf_{t.lower()}", f"WIND_GEN_{t}") for t in ("T1", "T2", "T3")]
        if "wind_cf_t1" in ds
        else [("wind_cf", "WIND_GEN")]
    )
    for field, tech in profiles + [("solar_cf", "SOLAR_GEN")]:
        data = ds[field].transpose("region", "time").values
        summary = pd.DataFrame(
            {
                "region": ds.region.values,
                "tech": tech,
                "month": time[0].month,
                "hours": len(time),
                "monthly_mean_cf": data.mean(axis=1),
                "hourly_min_cf": data.min(axis=1),
                "hourly_max_cf": data.max(axis=1),
                "hourly_p05_cf": np.quantile(data, 0.05, axis=1),
                "hourly_p95_cf": np.quantile(data, 0.95, axis=1),
                "monthly_mean_reconstruction_rmse_cf": data.std(axis=1, ddof=0),
            }
        )
        rows.append(summary)
    return means, pd.concat(rows, ignore_index=True)


def plot_maps(regions, means, path, title, land_enabled=False):
    joined = regions.join(means)
    if joined[["wind_cf", "solar_cf"]].isna().any().any():
        raise ValueError("Preview geometry does not match profile region IDs.")
    profiles = (
        [(f"wind_cf_{t.lower()}", f"Onshore wind {t}") for t in ("T1", "T2", "T3")]
        if "wind_cf_t1" in means
        else [("wind_cf", "Onshore wind")]
    )
    profiles += [("solar_cf", "Utility PV (AC)")]
    fig, axes = plt.subplots(
        1, len(profiles), figsize=(7 * len(profiles), 9), constrained_layout=True
    )
    for axis, field, label in zip(
        axes, [p[0] for p in profiles], [p[1] for p in profiles]
    ):
        joined.plot(
            column=field,
            ax=axis,
            cmap="viridis",
            vmin=0,
            vmax=1,
            legend=True,
            linewidth=0,
            rasterized=True,
        )
        axis.set_title(label)
        axis.set_axis_off()
    fig.suptitle(
        title
        + (
            "\nEligible-land area weighting"
            if land_enabled
            else "\nArea-weighted resource baseline; no land eligibility"
        )
        + "; fixed CF scale 0–1"
    )
    fig.savefig(path, dpi=160)
    plt.close(fig)


def build_previews(output, preview_root=None, engine="netcdf4"):
    output = Path(output).resolve()
    record = json.loads((output / "manifest.json").read_text())
    # Standard layout: <root>/<build>/<grid>/<run>.
    root = Path(preview_root) if preview_root else output.parents[2] / "preview"
    destination = root / f"{output.parents[1].name}_{output.parent.name}_{output.name}"
    destination.mkdir(parents=True, exist_ok=True)
    print(f"Renewables: loading preview geometry from {output}", flush=True)
    regions = gpd.read_file(
        output / "annual_capacity_factors.gpkg", layer="capacity_factors"
    )
    regions = regions.set_index("region", drop=False)
    regions = regions.drop(
        columns=[c for c in regions if c.startswith("wind_cf") or c == "solar_cf"]
    )
    annual = pd.read_csv(output / "annual_capacity_factors.csv", index_col=0)
    print(
        f"Renewables: rendering annual preview for {len(annual):,} regions.", flush=True
    )
    plot_maps(
        regions,
        annual,
        destination / "annual_capacity_factors.png",
        f"{record['weather_year']} annual mean capacity factors",
        record.get("sources", {})
        .get("land_exclusion", {})
        .get("policy", {})
        .get("enabled", False),
    )
    summaries = []
    weighted = annual * 0
    total_hours = 0
    for month in tqdm(range(1, 13), desc="Renewables: monthly previews", unit="map"):
        path = output / f"month_{month:02d}.nc"
        if sha256(path) != record["monthly_sha256"][path.name]:
            raise ValueError(f"Monthly Silver checksum mismatch: {path.name}")
        with xr.open_dataset(path, engine=engine) as ds:
            expected = pd.date_range(
                f"{record['weather_year']}-{month:02d}-01",
                periods=pd.Period(f"{record['weather_year']}-{month:02d}").days_in_month
                * 24,
                freq="h",
            )
            if not pd.DatetimeIndex(ds.time.values).equals(expected):
                raise ValueError("Expected a complete UTC weather month.")
            means, stats = month_statistics(ds.load())
            hours = ds.sizes["time"]
        weighted += means.reindex(annual.index) * hours
        total_hours += hours
        summaries.append(stats)
        plot_maps(
            regions,
            means,
            destination / f"month_{month:02d}_capacity_factors.png",
            f"{record['weather_year']}-{month:02d} monthly mean capacity factors (UTC)",
            record.get("sources", {})
            .get("land_exclusion", {})
            .get("policy", {})
            .get("enabled", False),
        )
    if not np.allclose(weighted / total_hours, annual, rtol=1e-10, atol=1e-12):
        raise ValueError("Hour-weighted monthly means do not reproduce annual CFs.")
    pd.concat(summaries, ignore_index=True).to_csv(
        destination / "monthly_temporal_statistics.csv", index=False
    )
    (destination / "README.md").write_text(
        "Monthly maps retain seasonal/spatial mean CF differences. Replacing hourly profiles "
        "with monthly means preserves monthly energy for fixed capacity, but removes "
        "diurnal cycles, ramps, extremes and coincidence with demand or other resources. "
        "The statistics CSV reports per-region hourly ranges, quantiles and the RMS error "
        "of that replacement (equal to within-month population standard deviation). "
        "These are diagnostics; Gold model inputs are unchanged.\n",
        encoding="utf-8",
    )
    manifest(
        destination / "manifest.json",
        {
            "silver_run": str(output),
            "silver_manifest_sha256": sha256(output / "manifest.json"),
            "weather_year": record["weather_year"],
            "hours": total_hours,
            "regions": len(annual),
            "monthly_means_reproduce_annual": True,
            "cf_scale": [0, 1],
            "adapter_sha256": sha256(__file__),
            "monthly_sha256": record["monthly_sha256"],
        },
    )
    print(f"Renewables: preview gallery saved at {destination}", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Completed Silver run directory")
    parser.add_argument("--preview-root", type=Path)
    args = parser.parse_args()
    build_previews(args.output, args.preview_root)
