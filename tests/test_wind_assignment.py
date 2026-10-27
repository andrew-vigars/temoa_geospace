from pathlib import Path
import tomllib

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from scipy import sparse
from shapely.geometry import box

from geocanoe.preprocessing import wind_assignment as assignment
from geocanoe.preprocessing import renewables as stage

ROOT = Path(__file__).resolve().parents[1]


def test_thresholds_invalid_values_and_low_resource():
    np.testing.assert_array_equal(
        assignment.classify([0, 5.899, 5.9, 6.529, 6.53, 7.099, 7.1, 15], {}),
        [4, 4, 3, 3, 2, 2, 1, 1],
    )
    with pytest.raises(ValueError, match="Invalid annual"):
        assignment.classify([np.nan], {})


def test_assignment_uses_entire_year_and_checked_cache(tmp_path, monkeypatch):
    import atlite.wind

    monkeypatch.setattr(
        atlite.wind, "extrapolate_wind_speed", lambda ds, **kw: ds.speed
    )
    calls = []

    def weather(month):
        calls.append(month)
        start = pd.Timestamp(2018, month, 1)
        time = pd.date_range(
            start, start + pd.offsets.MonthBegin(), freq="h", inclusive="left"
        )
        # One unusually windy month must not assign T1 for the whole year.
        values = np.full((len(time), 1, 1), 10 if month == 1 else 6)
        return xr.Dataset(
            {"speed": (("time", "y", "x"), values)},
            coords={"time": time, "y": [50], "x": [-100]},
        ), None

    result = assignment.annual_assignment(tmp_path, {"weather_year": 2018}, weather)
    assert result.design.item() == 3
    assert calls == list(range(1, 13))
    assignment.annual_assignment(
        tmp_path, {"weather_year": 2018}, lambda m: pytest.fail("cache not reused")
    )


def test_spatial_cf_mixture_and_gold_costs_share_capacity_weights(
    tmp_path, monkeypatch
):
    from geocanoe.preprocessing import renewables_weather as physical

    settings = tomllib.loads((ROOT / "config/renewables/canada.toml").read_text())
    crosswalk, costs = stage.select_costs(settings, ROOT)
    assert set(crosswalk.tech) == {"WIND_GEN", "SOLAR_GEN"}
    regions = gpd.GeoDataFrame(
        {"region": ["R1", "R0"]}, geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1)], crs=3347
    )
    regions.index = pd.Index(regions.region, name="region")
    weights = sparse.csr_matrix([[1.0, 3.0, 6.0, 10.0], [0.0, 0.0, 0.0, 2.0]])
    resource = xr.Dataset(
        {
            "design": (("y", "x"), [[1, 2, 3, 4]]),
            "mean_wind_speed_110m": (("y", "x"), [[8.0, 6.8, 6.1, 5.0]]),
        },
        coords={"y": [50], "x": [-100, -99, -98, -97]},
    )
    (tmp_path / "wind_resource").mkdir()
    (tmp_path / "land_exclusion").mkdir()
    pd.DataFrame(
        [
            dict(
                region=r,
                tech=t,
                eligible_area_km2=float(weights[i].sum()),
                density_mw_per_km2=2,
                basis="test",
                source="test",
            )
            for i, r in enumerate(regions.region)
            for t in ["WIND_GEN", "SOLAR_GEN"]
        ]
    ).to_csv(tmp_path / "land_exclusion/regional_capacity_assumptions.csv", index=False)
    assignment.regional_mix(resource, weights, regions, settings, tmp_path)
    mix = pd.read_csv(tmp_path / "wind_resource/regional_mix.csv")
    np.testing.assert_allclose(
        mix.loc[0, ["T1_fraction", "T2_fraction", "T3_fraction", "T4_fraction"]].astype(
            float
        ),
        [0.05, 0.15, 0.3, 0.5],
    )
    assert mix.loc[0, "excluded_low_resource_km2"] == 0

    class Cutout:
        def wind(self, turbine, **kwargs):
            # Spatial differences make mixing pre-averaged design profiles wrong.
            return xr.DataArray(
                [[[0.1, 0.2, 0.3, 0.9]]],
                dims=("time", "y", "x"),
                coords={"time": [0], "y": resource.y, "x": resource.x},
            ) * (turbine["hub_height"] / 130)

        def pv(self, **kwargs):
            return self.wind({"hub_height": 130})

    monkeypatch.setattr(
        physical,
        "aggregation_matrix",
        lambda *args: (
            weights,
            xr.DataArray(np.ones((1, 4)), dims=("y", "x")),
            np.ones(2),
        ),
    )
    config = {**settings["technology"], "wind_plant_loss": 0.0}
    result, _ = physical.convert(
        Cutout(), regions, None, config, {"wind": weights, "solar": weights}, resource
    )
    expected = (
        0.1 * 115 / 130 + 3 * 0.2 + 6 * 0.3 * 100 / 130 + 10 * 0.9 * 140 / 130
    ) / 20
    assert result.wind_cf.sel(region="R1").item() == pytest.approx(expected)
    assert result.wind_cf.sel(region="R0").item() == pytest.approx(0.9 * 140 / 130)
    exported = assignment.export_settings(settings, tmp_path)
    annual = result.mean("time").to_dataframe()
    tables = stage.gold_tables(
        annual, crosswalk, costs, exported, tmp_path / "temoa_inputs"
    )
    stage.validate_temoa_tables(
        tables, ROOT.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
    )
    from geocanoe.schema.renewables import apply_bundle

    encoded = {
        name: pd.DataFrame(rows).iloc[:0].copy() for name, rows in tables.items()
    }
    report = apply_bundle(
        encoded, tmp_path / "temoa_inputs", {"R1", "R0"}, settings["model_period"]
    )
    assert len(report["technologies"]) == 2
    bounds = pd.DataFrame(tables["limit_capacity"])
    assert (
        bounds.loc[
            (bounds.region == "R0") & (bounds.tech_or_group == "WIND_GEN"), "capacity"
        ].iloc[0]
        == 2 * 2
    )
    investment = pd.DataFrame(tables["cost_invest"])
    expected_cost = sum(
        f
        * costs.loc[
            (costs.tech == f"WIND_GEN_T{k}")
            & (costs.core_metric_parameter.isin(["OCC", "GCC"])),
            "value_per_MW",
        ].sum()
        for k, f in [(1, 0.05), (2, 0.15), (3, 0.3), (4, 0.5)]
    )
    # Use native dollars for an unambiguous test of the weighted ATB cost.
    native = {k: v for k, v in exported.items() if k != "currency_basis"}
    tables = stage.gold_tables(
        annual, crosswalk, costs, native, tmp_path / "temoa_inputs"
    )
    investment = pd.DataFrame(tables["cost_invest"])
    assert investment.loc[
        (investment.region == "R1") & (investment.tech == "WIND_GEN"), "cost"
    ].iloc[0] == pytest.approx(expected_cost)
    solar_components = costs.loc[
        costs.tech.eq("SOLAR_GEN")
        & costs.core_metric_parameter.isin(["OCC", "GCC"])
    ]
    assert set(solar_components.core_metric_parameter) == {"OCC", "GCC"}
    assert solar_components.loc[
        solar_components.core_metric_parameter.eq("GCC"), "value_per_MW"
    ].item() > 0
    assert investment.loc[
        investment.region.eq("R1") & investment.tech.eq("SOLAR_GEN"), "cost"
    ].item() == pytest.approx(solar_components.value_per_MW.sum())
