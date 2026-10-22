"""ERA5-to-Atlite physical conversion promoted from the validated Notebook 22 pilot."""

from __future__ import annotations
import json
import math
from pathlib import Path
import shutil
import zipfile
import numpy as np
import pandas as pd
import xarray as xr


def spatial_subset(ds, bounds):
    """Subset raw NetCDF coordinates before loading national arrays into memory."""
    if bounds is None:
        return ds
    west, south, east, north = bounds
    return (
        ds.sortby("latitude")
        .sortby("longitude")
        .sel(latitude=slice(south, north), longitude=slice(west, east))
    )


def raw_dataset(payload, extraction_root, bounds=None, engine="netcdf4"):
    """CDS can return a NetCDF or a ZIP of NetCDFs; preserve raw bytes."""
    payload, extraction_root = Path(payload), Path(extraction_root)
    if not zipfile.is_zipfile(payload):
        with xr.open_dataset(payload, engine=engine) as ds:
            return spatial_subset(ds, bounds).load()
    extraction_root.mkdir(parents=True, exist_ok=True)
    datasets = []
    with zipfile.ZipFile(payload) as archive:
        members = archive.namelist()
        names = [Path(name).name for name in members]
        if len(names) != len(set(names)) or any(
            not name.endswith(".nc") for name in names
        ):
            raise ValueError("Expected uniquely named NetCDF files in CDS ZIP.")
        for name, basename in zip(members, names):
            target = extraction_root / basename
            with archive.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            with xr.open_dataset(target, engine=engine) as ds:
                datasets.append(spatial_subset(ds, bounds).load())
    return xr.merge(datasets, compat="no_conflicts", join="exact")


def prepare_weather(payloads, extraction_root, bounds=None, engine="netcdf4"):
    """Pin private weather-preparation functions to the inspected Atlite 0.7.0."""
    import atlite
    from atlite.datasets import era5

    if atlite.__version__ != "0.7.0":
        raise ValueError("Review preparation adapter before changing Atlite 0.7.0.")
    datasets = []
    for feature, payload in payloads.items():
        ds = raw_dataset(payload, Path(extraction_root) / feature, bounds, engine)
        ds = era5._rename_and_clean_coords(ds).sortby(["y", "x", "time"])
        if feature == "wind":
            ds = era5.sanitize_wind(era5._process_wind(ds))
        elif feature == "influx":
            ds = era5.sanitize_influx(era5._process_influx(ds))
        else:
            ds = ds.rename(
                {
                    "t2m": "temperature",
                    "stl4": "soil temperature",
                    "d2m": "dewpoint temperature",
                }
            )
        datasets.append(ds)
    return xr.merge(datasets, compat="equals", join="exact")


def aggregation_matrix(cutout, regions):
    """Native Atlite fractional overlap in equal-area CRS; uniform area weights."""
    from atlite.gis import compute_indicatormatrix

    grid = cutout.grid.to_crs(3347)
    matrix = compute_indicatormatrix(
        grid, regions, orig_crs=3347, dest_crs=3347
    ).tocsr()
    area = grid.area.to_numpy() / 1e6
    coverage = (
        np.asarray(matrix @ area).ravel() / regions.production_area_km2.to_numpy()
    )
    # Area weights in cutout.grid order, deliberately independent of land eligibility.
    layout = xr.DataArray(
        area.reshape(len(cutout.data.y), len(cutout.data.x)),
        coords={"y": cutout.data.y, "x": cutout.data.x},
        dims=("y", "x"),
    )
    return matrix, layout, coverage


def t1_config(bundle):
    import yaml

    curve = pd.read_csv(Path(bundle) / "T1_curve.csv")
    specs = yaml.safe_load((Path(bundle) / "T1_specs.yaml").read_text())
    if (specs["turbine_class"], specs["rated_power"], specs["hub_height"]) != (
        "T1",
        6000,
        115,
    ):
        raise ValueError("Expected class-tagged T1 6 MW / 115 m metadata.")
    speeds = curve["Wind Speed [m/s]"].to_numpy(float)
    power = curve["Power [kW]"].to_numpy(float) / 1000
    return {
        "V": np.r_[speeds, speeds[-1]],
        "POW": np.r_[np.minimum(power, 6), 0],
        "P": 6.0,
        "hub_height": 115.0,
    }


def convert(cutout, regions, bundle, config):
    from atlite.resource import get_solarpanelconfig

    for key in ["wind_plant_loss", "pv_additional_loss"]:
        if not math.isfinite(config[key]) or not 0 <= config[key] < 1:
            raise ValueError(f"Invalid {key}.")
    if not math.isfinite(config["pv_ilr"]) or config["pv_ilr"] <= 0:
        raise ValueError("PV ILR must be positive.")
    if not 0 < config["inverter_efficiency"] <= 1:
        raise ValueError("Invalid inverter efficiency.")
    matrix, layout, coverage = aggregation_matrix(cutout, regions)
    if (
        not np.isfinite(coverage).all()
        or np.min(coverage) < 0.999
        or np.max(coverage) > 1.001
    ):
        raise ValueError(
            "Weather grid must cover every selected production polygon completely."
        )
    common = dict(
        matrix=matrix,
        index=pd.Index(regions.region, name="region"),
        layout=layout,
        per_unit=True,
        aggregate_time=None,
        dask_kwargs={"scheduler": "single-threaded"},
    )
    wind = cutout.wind(
        turbine=t1_config(bundle),
        add_cutout_windspeed=False,
        interpolation_method="logarithmic",
        **common,
    )
    # Convert and clip at weather-cell level before spatial averaging.
    panel = dict(get_solarpanelconfig(config.get("pv_panel", "CSi")))
    panel["inverter_efficiency"] = 1.0
    solar_cell = cutout.pv(
        panel=panel,
        orientation={
            "slope": config.get("pv_slope", 0),
            "azimuth": config.get("pv_azimuth", 180),
        },
        tracking=config.get("pv_tracking", "horizontal"),
        aggregate_time=None,
        dask_kwargs={"scheduler": "single-threaded"},
    )
    solar_cell = (
        solar_cell
        * config["pv_ilr"]
        * config["inverter_efficiency"]
        * (1 - config["pv_additional_loss"])
    ).clip(max=1)
    weights = matrix.multiply(layout.values.ravel())
    denominator = np.asarray(weights.sum(axis=1)).ravel()
    solar = (
        weights
        @ solar_cell.transpose("time", "y", "x")
        .values.reshape(len(solar_cell.time), -1)
        .T
    )
    solar = xr.DataArray(
        solar / denominator[:, None],
        dims=("region", "time"),
        coords={"region": regions.region.to_numpy(), "time": solar_cell.time},
    )
    result = xr.Dataset(
        {"wind_cf": wind * (1 - config["wind_plant_loss"]), "solar_cf": solar}
    )
    values = result.to_array().values
    if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1 + 1e-8:
        raise ValueError("Invalid converted capacity factors.")
    result.attrs.update(
        timezone="UTC",
        spatial_weighting="Native Atlite overlap, equal-area uniform capacity density; no eligibility masks",
        configuration=json.dumps(config, sort_keys=True),
    )
    return result, coverage


def annual_profiles(paths, year, engine="netcdf4"):
    # Region time-series are small compared with raw weather and retain hourly outputs.
    pieces = []
    for path in paths:
        with xr.open_dataset(path, engine=engine) as ds:
            pieces.append(ds.load())
    ds = xr.concat(pieces, dim="time", join="exact").sortby("time")
    expected = pd.date_range(
        f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left"
    )
    if not pd.DatetimeIndex(ds.time.values).equals(expected):
        raise ValueError(
            "Annual export requires exactly one complete UTC weather year, without duplicates."
        )
    if not np.isfinite(ds.to_array().values).all():
        raise ValueError("Missing capacity factors cannot be silently averaged.")
    return ds, ds.mean("time", skipna=False).to_dataframe()[["wind_cf", "solar_cf"]]
