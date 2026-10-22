import numpy as np
import pandas as pd
import pytest
import xarray as xr

from geocanoe.preprocessing.renewables_preview import month_statistics


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
