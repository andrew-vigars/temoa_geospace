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


def raw_weather_means(wind, influx):
    """Average hourly scalar speeds, never the monthly U/V vector magnitude."""
    fields = {
        f"wind_{height}m": np.hypot(wind[f"u{height}"], wind[f"v{height}"])
        for height in (10, 100)
    }
    # CDS hourly ERA5 single-level radiation is accumulated energy per hour.
    fields["solar_downwards"] = influx.ssrd / 3600
    result = {}
    for name, data in fields.items():
        if "valid_time" in data.dims:
            data = data.rename(valid_time="time")
        if not np.isfinite(data.values).all():
            raise ValueError(f"Non-finite raw ERA5 values: {name}")
        result[name] = data.mean("time", skipna=False).assign_attrs(
            units="W m-2" if name == "solar_downwards" else "m s-1",
            long_name=(
                "Surface solar radiation downwards"
                if name == "solar_downwards"
                else name.replace("_", " ")
            ),
        )
    return xr.Dataset(result)


def build_raw_weather_preview(
    payloads, destination, year, month, bounds, engine="netcdf4", regions=None
):
    """Render native ERA5 cells before any Atlite preparation or CF conversion."""
    from tempfile import TemporaryDirectory
    from geocanoe.preprocessing.renewables_weather import raw_dataset

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"{year}_{month:02d}_era5"
    png = destination / f"{stem}.png"
    nc = destination / f"{stem}.nc"
    sidecar = destination / f"{stem}.json"
    sources = {k: sha256(payloads[k]) for k in ("wind", "influx")}
    signature = dict(
        raw_sha256=sources, bounds=list(bounds), adapter_sha256=sha256(__file__)
    )
    if regions is not None:
        import hashlib

        signature["geometry_sha256"] = hashlib.sha256(
            b"".join(regions.to_crs(3347).geometry.to_wkb())
        ).hexdigest()
    record = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    if sidecar.exists() and png.exists() and nc.exists():
        record = json.loads(sidecar.read_text())
        if (
            all(record.get(k) == v for k, v in signature.items())
            and record.get("png_sha256") == sha256(png)
            and record.get("nc_sha256") == sha256(nc)
        ):
            return png
    print(f"Renewables: rendering raw ERA5 preview {year}-{month:02d}", flush=True)
    if (
        nc.exists()
        and record.get("raw_sha256") == sources
        and record.get("bounds") == list(bounds)
        and record.get("nc_sha256") == sha256(nc)
    ):
        with xr.open_dataset(nc, engine=engine) as ds:
            means = ds.load()
    else:
        with TemporaryDirectory(dir=destination) as extraction:
            wind = raw_dataset(
                payloads["wind"], Path(extraction) / "wind", bounds, engine
            )
            influx = raw_dataset(
                payloads["influx"], Path(extraction) / "influx", bounds, engine
            )
            means = raw_weather_means(wind, influx)
            wind.close()
            influx.close()
    mapped = project_weather_means(means, regions) if regions is not None else None
    fig, axes = plt.subplots(1, 3, figsize=(21, 7), constrained_layout=True)
    try:
        for axis, (field, label, maximum) in zip(
            axes,
            [
                ("wind_10m", "10 m wind speed (m/s)", 15),
                ("wind_100m", "100 m wind speed (m/s)", 15),
                ("solar_downwards", "Surface solar radiation downwards (W/m²)", 400),
            ],
        ):
            if mapped is not None:
                mapped.plot(
                    column=field,
                    ax=axis,
                    vmin=0,
                    vmax=maximum,
                    cmap="viridis",
                    legend=True,
                    linewidth=0,
                    legend_kwds={"label": label, "extend": "max"},
                    rasterized=True,
                )
                axis.set_axis_off()
            else:
                means[field].plot.pcolormesh(
                    ax=axis,
                    x="longitude",
                    y="latitude",
                    vmin=0,
                    vmax=maximum,
                    cmap="viridis",
                    cbar_kwargs={"extend": "max"},
                )
            axis.set_title(label)
        fig.suptitle(
            f"{year}-{month:02d} raw ERA5 monthly means (UTC) — before Atlite; no land masks"
            + (
                "\nProduction basemap; area-weighted weather cells (EPSG:3347)"
                if mapped is not None
                else ""
            )
        )
        fig.savefig(png, dpi=160)
    finally:
        plt.close(fig)
    means.to_netcdf(nc, engine=engine)
    manifest(sidecar, {**signature, "png_sha256": sha256(png), "nc_sha256": sha256(nc)})
    return png


def project_weather_means(means, regions):
    """Average native weather cells by overlap area on the CF production polygons."""
    import shapely
    from scipy.sparse import coo_matrix

    def edges(values):
        values = np.asarray(values)
        if len(values) < 2 or not (np.diff(values) > 0).all():
            raise ValueError(
                "Weather coordinates must be increasing with at least two cells."
            )
        return np.r_[
            values[0] - (values[1] - values[0]) / 2,
            (values[:-1] + values[1:]) / 2,
            values[-1] + (values[-1] - values[-2]) / 2,
        ]

    means = means.sortby("latitude").sortby("longitude")
    x, y = edges(means.longitude), edges(means.latitude)
    west, south = np.meshgrid(x[:-1], y[:-1])
    east, north = np.meshgrid(x[1:], y[1:])
    cells = gpd.GeoSeries(
        shapely.box(west.ravel(), south.ravel(), east.ravel(), north.ravel()), crs=4326
    ).to_crs(3347)
    mapped = regions.to_crs(3347).copy()
    rows, cols = shapely.STRtree(cells.array).query(
        mapped.geometry.array, predicate="intersects"
    )
    areas = shapely.area(
        shapely.intersection(mapped.geometry.array[rows], cells.array[cols])
    )
    weights = coo_matrix((areas, (rows, cols)), shape=(len(mapped), len(cells))).tocsr()
    total = np.asarray(weights.sum(axis=1)).ravel()
    if (total <= 0).any() or not np.allclose(total, mapped.area, rtol=0.001):
        raise ValueError("ERA5 cells must cover each production polygon completely.")
    for field in means:
        mapped[field] = (
            weights @ means[field].transpose("latitude", "longitude").values.ravel()
        ) / total
    return mapped


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


def build_raw_previews(output, bronze, preview_root=None, engine="netcdf4"):
    """Build only raw maps, using the weather extent of an existing Silver run."""
    from geocanoe.acquisition.era5_renewables import cache_verified

    output, bronze = Path(output).resolve(), Path(bronze).resolve()
    year = json.loads((output / "manifest.json").read_text())["weather_year"]
    regions = gpd.read_file(
        output / "annual_capacity_factors.gpkg", layer="capacity_factors"
    )
    plan = json.loads((bronze / "request_plan.json").read_text())["plan"]
    with xr.open_dataset(output / "wind_resource" / "resource.nc", engine=engine) as ds:
        bounds = [
            float(ds.x.min()),
            float(ds.y.min()),
            float(ds.x.max()),
            float(ds.y.max()),
        ]
    root = Path(preview_root) if preview_root else output.parents[2] / "preview"
    destination = (
        root / f"{output.parents[1].name}_{output.parent.name}_{output.name}" / "era5"
    )
    for month in tqdm(range(1, 13), desc="Renewables: raw ERA5 previews", unit="month"):
        items = [
            i
            for i in plan
            if i["month"] == month and i["feature"] in ("wind", "influx")
        ]
        if len(items) != 2 or {i["feature"] for i in items} != {"wind", "influx"}:
            raise ValueError(f"Expected wind and influx requests for month {month}.")
        for item in items:
            if item["request"]["year"] != [str(year)] or not cache_verified(
                item, bronze
            ):
                raise ValueError(
                    f"Missing or mismatched ERA5 cache: {item['request_id']}"
                )
        build_raw_weather_preview(
            {i["feature"]: bronze / f"{i['request_id']}.payload" for i in items},
            destination,
            year,
            month,
            bounds,
            engine,
            regions=regions,
        )
    print(f"Raw ERA5 preview gallery saved at {destination}", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Completed Silver run directory")
    parser.add_argument("--preview-root", type=Path)
    parser.add_argument(
        "--raw-era5",
        action="store_true",
        help="Render raw ERA5 only; no Atlite or CF conversion",
    )
    parser.add_argument(
        "--bronze-dir",
        type=Path,
        help="ERA5 cache containing request_plan.json (required with --raw-era5)",
    )
    args = parser.parse_args()
    if args.raw_era5:
        if args.bronze_dir is None:
            parser.error("--raw-era5 requires --bronze-dir")
        build_raw_previews(args.output, args.bronze_dir, args.preview_root)
    else:
        build_previews(args.output, args.preview_root)
