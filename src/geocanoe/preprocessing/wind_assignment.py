"""Fixed wind-design mixtures from annual resource quality and eligible land."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from geocanoe.acquisition.era5_renewables import manifest, sha256


def policy(settings):
    result = dict(settings.get("wind_assignment", {}))
    expected = dict(
        reference_height_m=110,
        t1_min_ms=7.10,
        t2_min_ms=6.53,
        t3_min_ms=5.90,
        low_resource_policy="exclude",
        atb_edition=2025,
    )
    result = {**expected, **result}
    if result != expected:
        raise ValueError(
            "Review wind assignment adapter before changing ATB thresholds, height or low-resource policy."
        )
    return result


def classify(speed, settings):
    p = policy(settings)
    speed = np.asarray(speed)
    if not np.isfinite(speed).all() or (speed < 0).any():
        raise ValueError("Invalid annual wind speed.")
    return np.select(
        [speed >= p["t1_min_ms"], speed >= p["t2_min_ms"], speed >= p["t3_min_ms"]],
        [1, 2, 3],
        default=0,
    )


def annual_assignment(output, settings, weather_month):
    """Use the whole configured weather year, never a different class per month."""
    from atlite.wind import extrapolate_wind_speed

    directory = Path(output) / "wind_resource"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "resource.nc"
    record = directory / "resource.json"
    p = policy(settings)
    if path.exists():
        captured = json.loads(record.read_text())
        if (
            captured["policy"] != p
            or captured.get("weather_year") != settings["weather_year"]
            or captured["sha256"] != sha256(path)
        ):
            raise ValueError("Wind resource cache checksum/policy mismatch.")
        with xr.open_dataset(path, engine="netcdf4") as ds:
            return ds.load()
    total, count = None, 0
    for month in range(1, 13):
        print(
            f"Wind resource classification: annual mean, month {month:02d}/12",
            flush=True,
        )
        weather, _ = weather_month(month)
        try:
            speed = extrapolate_wind_speed(
                weather, to_height=p["reference_height_m"], method="logarithmic"
            )
            partial = speed.sum("time", skipna=False).load()
            if total is not None:
                total, partial = xr.align(total, partial, join="exact")
            total = partial if total is None else total + partial
            count += weather.sizes["time"]
        finally:
            weather.close()
    expected = len(
        pd.date_range(
            f"{settings['weather_year']}-01-01",
            f"{settings['weather_year'] + 1}-01-01",
            freq="h",
            inclusive="left",
        )
    )
    if count != expected:
        raise ValueError("Resource assignment requires a complete weather year.")
    mean = total / count
    result = xr.Dataset(
        {
            "mean_wind_speed_110m": mean,
            "design": xr.DataArray(
                classify(mean.values, settings), coords=mean.coords, dims=mean.dims
            ),
        }
    )
    result.attrs.update(
        weather_year=settings["weather_year"],
        reference_height_m=110,
        classification_basis="single-year ERA5 proxy; not a multiyear climatology",
        design_zero="Excluded: below T3 threshold; T4 not represented",
    )
    temporary = path.with_suffix(".partial.nc")
    result.to_netcdf(temporary, engine="netcdf4")
    temporary.replace(path)
    manifest(
        record,
        {
            "policy": p,
            "sha256": sha256(path),
            "hours": count,
            "weather_year": settings["weather_year"],
        },
    )
    return result


def regional_mix(resource, weights, regions, settings, output):
    """Keep spatially assigned design fractions and omitted potential auditable."""
    directory = Path(output) / "wind_resource"
    design = resource.design.transpose("y", "x").values.ravel()
    total = np.asarray(weights.sum(axis=1)).ravel()
    areas = np.column_stack(
        [
            np.asarray(weights.multiply(design == k).sum(axis=1)).ravel()
            for k in [1, 2, 3]
        ]
    )
    represented = areas.sum(axis=1)
    fractions = np.divide(
        areas,
        represented[:, None],
        out=np.zeros_like(areas),
        where=represented[:, None] > 0,
    )
    frame = pd.DataFrame(
        {
            "region": regions.region.astype(str).to_numpy(),
            "land_eligible_km2": total,
            "represented_wind_km2": represented,
            "excluded_low_resource_km2": np.maximum(total - represented, 0),
        }
    )
    for k in range(3):
        frame[f"T{k + 1}_fraction"] = fractions[:, k]
    frame.to_csv(directory / "regional_mix.csv", index=False)
    capacity = pd.read_csv(
        Path(output) / "land_exclusion/regional_capacity_assumptions.csv"
    )
    replacement = dict(zip(frame.region, represented))
    wind = capacity.tech.eq("WIND_GEN")
    capacity.loc[wind, "eligible_area_km2"] = (
        capacity.loc[wind, "region"].astype(str).map(replacement)
    )
    capacity.loc[wind, "source"] = (
        "wind_resource/resource.json; land eligibility plus resource-class restriction"
    )
    capacity.to_csv(directory / "regional_capacity_assumptions.csv", index=False)
    audit = (
        regions[["region", "geometry"]]
        .copy()
        .reset_index(drop=True)
        .merge(frame, on="region", validate="one_to_one")
    )
    audit.to_file(directory / "regional_mix.gpkg", layer="wind_mix", index=False)
    plot_resource_assignment(audit, resource, directory / "resource_assignment.png")
    manifest(
        directory / "manifest.json",
        {
            "policy": policy(settings),
            "sha256": {
                p.name: sha256(p)
                for p in directory.iterdir()
                if p.is_file() and p.name != "manifest.json"
            },
        },
    )


def plot_resource_assignment(audit, resource, path):
    """Label the native ERA5 geographic axes independently of coordinate attrs."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 4, figsize=(18, 5))
    for ax, column in zip(axes[:3], ["T1_fraction", "T2_fraction", "T3_fraction"]):
        audit.plot(column=column, ax=ax, vmin=0, vmax=1, legend=True)
        ax.set_title(column)
        ax.set_axis_off()
    resource.mean_wind_speed_110m.plot.pcolormesh(
        ax=axes[3], x="x", y="y", cbar_kwargs={"label": "Wind speed (m/s)"}
    )
    axes[3].set_xlabel("Longitude (degrees east)")
    axes[3].set_ylabel("Latitude (degrees north)")
    axes[3].set_title("Annual mean wind at 110 m (m/s)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def export_settings(settings, output):
    directory = Path(output) / "wind_resource"
    record = json.loads((directory / "manifest.json").read_text())
    if record["policy"] != policy(settings):
        raise ValueError("Wind resource policy changed; regenerate Silver.")
    for name, digest in record["sha256"].items():
        if sha256(directory / name) != digest:
            raise ValueError(f"Wind resource export checksum mismatch: {name}")
    return {
        **settings,
        "capacity_limits": dict(
            enabled=True, input_csv=str(directory / "regional_capacity_assumptions.csv")
        ),
    }
