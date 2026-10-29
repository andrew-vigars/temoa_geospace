import numpy as np
import pandas as pd
import pytest
import xarray as xr
import geopandas as gpd
from shapely.geometry import box

from geocanoe.preprocessing.renewables_preview import month_statistics
from geocanoe.preprocessing.renewables_preview import (
    raw_weather_means,
    build_raw_weather_preview,
    project_weather_means,
)


def test_weather_maps_use_production_geometry_and_overlap_area():
    means = xr.Dataset(
        {"wind_100m": (("latitude", "longitude"), [[2.0, 6.0], [2.0, 6.0]])},
        coords={"latitude": [50.0, 51.0], "longitude": [-120.0, -119.0]},
    )
    regions = gpd.GeoDataFrame(
        {"region": ["west", "east"]},
        geometry=[box(-120.4, 49.6, -119.6, 51.4), box(-119.4, 49.6, -118.6, 51.4)],
        crs=4326,
    )
    mapped = project_weather_means(means, regions)
    assert mapped.crs.to_epsg() == 3347
    assert mapped.region.tolist() == ["west", "east"]
    assert np.allclose(mapped.wind_100m, [2.0, 6.0])
    assert mapped.geometry.equals(regions.to_crs(3347).geometry)


def test_raw_preview_averages_speed_before_direction_cancellation(tmp_path):
    coords = {
        "valid_time": pd.date_range("2018-01-01", periods=2, freq="h"),
        "latitude": [50.0, 51.0],
        "longitude": [-120.0, -119.0],
    }
    dims = ("valid_time", "latitude", "longitude")
    u = np.array([np.full((2, 2), 3.0), np.full((2, 2), -3.0)])
    wind = xr.Dataset(
        {
            f"{component}{height}": (dims, u if component == "u" else u * 4 / 3)
            for height in (10, 100)
            for component in ("u", "v")
        },
        coords=coords,
    )
    influx = xr.Dataset({"ssrd": (dims, np.full((2, 2, 2), 360000.0))}, coords=coords)
    means = raw_weather_means(wind, influx)
    assert np.allclose(means.wind_100m, 5)
    assert np.allclose(means.solar_downwards, 100)
    assert means.solar_downwards.attrs["units"] == "W m-2"
    assert means.wind_100m.attrs["units"] == "m s-1"
    paths = {"wind": tmp_path / "wind.nc", "influx": tmp_path / "influx.nc"}
    wind.to_netcdf(paths["wind"])
    influx.to_netcdf(paths["influx"])
    png = build_raw_weather_preview(
        paths, tmp_path / "preview", 2018, 1, [-121, 49, -118, 52]
    )
    assert png.stat().st_size > 0
    modified = png.stat().st_mtime_ns
    build_raw_weather_preview(
        paths, tmp_path / "preview", 2018, 1, [-121, 49, -118, 52]
    )
    assert png.stat().st_mtime_ns == modified


def test_monthly_mean_preserves_energy_but_removes_hourly_variation():
    ds = xr.Dataset(
        {
            f: (("region", "time"), [[0.0, 1.0, 0.0, 1.0]])
            for f in ("wind_cf", "solar_cf")
        },
        coords={
            "region": ["R0"],
            "time": pd.date_range("2018-01-01", periods=4, freq="h"),
        },
    )
    means, stats = month_statistics(ds)
    assert (means == 0.5).all().all()
    assert (stats.monthly_mean_reconstruction_rmse_cf == 0.5).all()
    assert (stats.hours == 4).all()
    assert np.allclose(stats.monthly_mean_cf * stats.hours, 2)
    ds["wind_cf"][0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        month_statistics(ds)


def test_preview_accepts_roundoff_without_mutating_source():
    ds = xr.Dataset(
        {
            field: (("region", "time"), [[-1e-16, 1.0000000000000004]])
            for field in (
                "wind_cf",
                "solar_cf",
                "wind_cf_t1",
                "wind_cf_t2",
                "wind_cf_t3",
            )
        },
        coords={
            "region": ["R0"],
            "time": pd.date_range("2018-01-01", periods=2, freq="h"),
        },
    )
    original = ds.copy(deep=True)
    means, stats = month_statistics(ds)
    assert (means == 0.5).all().all()
    assert stats.hourly_min_cf.eq(0).all()
    assert stats.hourly_max_cf.eq(1).all()
    xr.testing.assert_identical(ds, original)


@pytest.mark.parametrize("field", ["wind_cf", "solar_cf", "wind_cf_t1"])
@pytest.mark.parametrize("value", [-1e-4, 1.0001, np.nan, np.inf])
def test_preview_rejects_invalid_cf_in_every_design(field, value):
    ds = xr.Dataset(
        {
            name: (("region", "time"), [[0.2, 0.3]])
            for name in (
                "wind_cf",
                "solar_cf",
                "wind_cf_t1",
                "wind_cf_t2",
                "wind_cf_t3",
            )
        },
        coords={
            "region": ["R0"],
            "time": pd.date_range("2018-01-01", periods=2, freq="h"),
        },
    )
    ds[field][0, 0] = value
    with pytest.raises(ValueError, match="finite CFs"):
        month_statistics(ds)
