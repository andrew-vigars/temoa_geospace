"""Scientific/data-contract checks for the exploratory Notebook 22 helper."""

from pathlib import Path
import importlib.util
import sqlite3

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "renewables_pilot", ROOT / "notebooks/support/renewables_pilot.py"
)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_monthly_plan_stable_and_leap_aware():
    regions = gpd.GeoDataFrame(
        {"region": ["R1"]}, geometry=[box(-81, 43, -80, 44)], crs=4326
    )
    plan = pilot.request_plan(regions, 2020)
    assert len(plan) == 36
    assert len({row["request_id"] for row in plan}) == 36
    assert plan == pilot.request_plan(regions, 2020)
    feb = next(row for row in plan if row["month"] == 2 and row["feature"] == "wind")
    assert feb["hours"] == 696
    assert feb["request"]["day"][-1] == "29"
    assert feb["request"]["data_format"] == "netcdf"


def test_acquisition_bounds_http_retries_and_reuses_cache(tmp_path, monkeypatch):
    import cdsapi

    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def retrieve(self, dataset, request):
            assert dataset == "reanalysis-era5-single-levels"
            return self

        def download(self, destination):
            Path(destination).write_bytes(b"test payload")

    monkeypatch.setattr(cdsapi, "Client", Client)
    plan = [
        {
            "request_id": "fixture",
            "request": {"year": ["2018"]},
            "feature": "wind",
            "month": 1,
        }
    ]
    assert pilot.acquire(plan, tmp_path, max_new_requests=1) == 1
    assert calls == [{"retry_max": 3, "sleep_max": 30, "timeout": 60}]
    assert pilot.acquire(plan, tmp_path, max_new_requests=1) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("year,hours", [(2018, 8760), (2020, 8784)])
def test_annual_gate_rejects_partial_and_duplicate_year(tmp_path, year, hours):
    time = pd.date_range(f"{year}-01-01", periods=hours, freq="h")
    ds = xr.Dataset(
        {
            "wind_cf": (("region", "time"), np.full((1, hours), 0.4)),
            "solar_cf": (("region", "time"), np.full((1, hours), 0.2)),
        },
        coords={"region": ["R1"], "time": time},
    )
    path = tmp_path / "annual.nc"
    ds.to_netcdf(path)
    _, annual = pilot.annual_profiles([path], year)
    assert annual.loc["R1", "wind_cf"] == pytest.approx(0.4)
    short = tmp_path / "short.nc"
    ds.isel(time=slice(1, None)).to_netcdf(short)
    with pytest.raises(ValueError, match="complete UTC"):
        pilot.annual_profiles([short], year)
    with pytest.raises(ValueError, match="complete UTC"):
        pilot.annual_profiles([path, path], year)


def test_raw_weather_adapter_preserves_era5_units(tmp_path):
    pytest.importorskip("atlite")
    time = pd.date_range("2018-06-06", periods=24, freq="h")
    coordinates = {
        "valid_time": time,
        "latitude": [43.0, 43.25],
        "longitude": [-80.0, -79.75],
    }
    shape = (24, 2, 2)
    raw = {
        "wind": {"u10": 4.0, "v10": 0.0, "u100": 8.0, "v100": 0.0, "fsr": 0.1},
        "influx": {
            "ssrd": 720000.0,
            "ssr": 576000.0,
            "fdir": 360000.0,
            "tisr": 1080000.0,
        },
        "temperature": {"t2m": 288.15, "stl4": 285.0, "d2m": 280.0},
    }
    paths = {}
    for feature, values in raw.items():
        ds = xr.Dataset(
            {
                key: (("valid_time", "latitude", "longitude"), np.full(shape, value))
                for key, value in values.items()
            },
            coords=coordinates,
        )
        path = tmp_path / f"{feature}.nc"
        ds.to_netcdf(path)
        paths[feature] = path
    prepared = pilot.prepare_weather(paths, tmp_path / "extract")
    assert float(prepared.wnd100m.mean()) == pytest.approx(8.0)
    assert float(prepared.influx_direct.mean()) == pytest.approx(100.0)
    assert float(prepared.influx_diffuse.mean()) == pytest.approx(100.0)
    assert float(prepared.temperature.mean()) == pytest.approx(288.15)
    assert pd.DatetimeIndex(prepared.time.values).equals(time)


def test_monthly_conversion_preserves_atlite_metadata(tmp_path, monkeypatch):
    pytest.importorskip("atlite")
    time = pd.date_range("2018-01-01", periods=744, freq="h")
    weather = xr.Dataset(
        coords={"time": time, "x": [-80.0, -79.75], "y": [43.0, 43.25]}
    )
    regions = gpd.GeoDataFrame(
        {"region": ["R1"]}, geometry=[box(-80.1, 42.9, -79.9, 43.1)], crs=4326
    ).to_crs(3347)
    regions["production_area_km2"] = regions.area / 1e6
    bronze = tmp_path / "bronze"
    bronze.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    pilot.manifest(bundle / "manifest.json", {})
    items = []
    for feature in pilot.FEATURE_VARIABLES:
        payload = bronze / f"{feature}.payload"
        payload.write_bytes(b"fixture")
        request = {"year": ["2018"]}
        pilot.manifest(
            bronze / f"{feature}.json",
            {"sha256": pilot.sha256(payload), "request": request},
        )
        items.append(
            {"request_id": feature, "feature": feature, "month": 1, "request": request}
        )
    monkeypatch.setattr(pilot, "prepare_weather", lambda *args: weather.copy())

    def conversion(cutout, regions, bundle, config):
        assert cutout.module == "era5"
        assert set(cutout.data.attrs["prepared_features"]) == set(
            pilot.FEATURE_VARIABLES
        )
        matrix, layout, coverage = pilot.aggregation_matrix(cutout, regions)
        assert matrix.shape == (1, 4)
        np.testing.assert_allclose(coverage, 1.0, atol=1e-6)
        return xr.Dataset(
            {"wind_cf": ("time", np.full(len(time), 0.4))}, coords={"time": time}
        ), coverage

    monkeypatch.setattr(pilot, "convert", conversion)
    silver = tmp_path / "silver"
    result = pilot.transform_month(items, bronze, silver, regions, bundle, {})
    # Simulate an interrupted legacy cache without Atlite metadata, then retry.
    result.unlink()
    cached_path = next(silver.glob("prepared_weather_*.nc"))
    weather.to_netcdf(cached_path)
    monkeypatch.setattr(
        pilot,
        "prepare_weather",
        lambda *args: pytest.fail("Should reuse cached weather"),
    )
    result = pilot.transform_month(items, bronze, silver, regions, bundle, {})
    assert result.is_file()
    with xr.open_dataset(cached_path) as cached:
        assert cached.attrs["module"] == "era5"


def test_cost_adapter_uses_2022_dollars_and_mw_scale(tmp_path):
    pd.DataFrame(
        [
            {
                "code": "wind_onshore",
                "base_tech": "E_WND_ON",
                "atb_display_name": "wind",
            },
            {"code": "solar", "base_tech": "E_SOL_PV", "atb_display_name": "pv"},
        ]
    ).to_csv(tmp_path / "generator_technologies.csv", index=False)
    pd.DataFrame(
        [
            {
                "display_name": name,
                "scenario": "Moderate",
                "core_metric_case": "Market",
                "crpyears": 20,
                "core_metric_variable": 2030,
                "core_metric_parameter": metric,
                "value": value,
            }
            for name in ["wind", "pv"]
            for metric, value in [("OCC", 1000), ("Fixed O&M", 20)]
        ]
    ).to_csv(tmp_path / "ATBe.csv", index=False)
    pd.DataFrame(
        {"Year": [2020, 2021, 2022], "CAD": [1, 1, 1], "USD": [1, 99, 1.25]}
    ).to_csv(tmp_path / "currency_exchange.csv", index=False)
    pd.DataFrame({"Year": [2020, 2021, 2022], "gdp_deflator": [1, 99, 0.8]}).to_csv(
        tmp_path / "cad_inflation.csv", index=False
    )
    costs = pilot.selected_costs(tmp_path, 2030)
    assert costs.loc["solar", "invest"] == pytest.approx(1.0)
    assert costs.loc["wind_onshore", "fixed"] == pytest.approx(0.02)
    assert (costs.source_currency_year == 2022).all()


@pytest.fixture
def sql_case(tmp_path):
    schema = ROOT.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
    if not schema.is_file():
        pytest.skip(
            "Target sibling Temoa v4 schema is required for pilot integration check"
        )
    annual = pd.DataFrame(
        {"wind_cf": [0.4], "solar_cf": [0.2]}, index=pd.Index(["R1"], name="region")
    )
    costs = pd.DataFrame(
        [
            {
                "code": "wind_onshore",
                "tech": "E_WND_ON",
                "invest": 1.2,
                "fixed": 0.03,
                "cost_year": 2030,
                "units": "million CAD2020/MW",
            },
            {
                "code": "solar",
                "tech": "E_SOL_PV",
                "invest": 0.8,
                "fixed": 0.02,
                "cost_year": 2030,
                "units": "million CAD2020/MW",
            },
        ]
    ).set_index("code")
    path = tmp_path / "renewables.sql"
    result = pilot.sql_candidate(annual, costs, schema, path)
    return schema, path, result


def target_connection(schema, region="R1"):
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO time_period VALUES(1,2025,'f')")
    conn.execute("INSERT INTO time_season VALUES(1,'S',1,'annual')")
    conn.execute("INSERT INTO time_of_day VALUES(1,'D',24,'annual')")
    conn.executemany(
        "INSERT INTO commodity(name,flag) VALUES(?,?)", [("ethos", "s"), ("elc", "a")]
    )
    conn.execute("INSERT INTO region(region) VALUES(?)", (region,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_sql_annual_semantics_and_duplicate_merge_rolls_back(sql_case):
    schema, path, result = sql_case
    assert result["validated_cf_rows"] == 2
    with target_connection(schema) as conn:
        conn.executescript(path.read_text())
        assert conn.execute(
            "SELECT DISTINCT c2a FROM capacity_to_activity"
        ).fetchall() == [(8760.0,)]
        assert (
            conn.execute("SELECT count(*) FROM capacity_factor_process").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM limit_annual_capacity_factor"
            ).fetchone()[0]
            == 2
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="prerequisites"):
            conn.executescript(path.read_text())
        assert not conn.in_transaction
        assert conn.execute("SELECT count(*) FROM technology").fetchone()[0] == 2


def test_sql_unknown_region_rolls_back_without_writes(sql_case):
    schema, path, _ = sql_case
    with target_connection(schema, region="OTHER") as conn:
        with pytest.raises(sqlite3.IntegrityError, match="prerequisites"):
            conn.executescript(path.read_text())
        assert not conn.in_transaction
        assert conn.execute("SELECT count(*) FROM technology").fetchone()[0] == 0
