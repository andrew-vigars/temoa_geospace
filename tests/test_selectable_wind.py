from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tomllib

from geocanoe.preprocessing import renewables as stage
from geocanoe.preprocessing.wind_options import package_identity
from geocanoe.preprocessing.renewables_weather import turbine_config
from geocanoe.schema.renewables import apply_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_physical_designs_use_distinct_curves_and_shared_land(monkeypatch):
    import xarray as xr
    from scipy import sparse
    from geocanoe.preprocessing import renewables_weather as physical

    calls = []

    class Cutout:
        def wind(self, turbine, **kwargs):
            calls.append(turbine["hub_height"])
            return xr.DataArray(
                [[[turbine["hub_height"] / 1000]]],
                dims=("time", "y", "x"),
                coords={"time": [0], "y": [0], "x": [0]},
            )

        def pv(self, **kwargs):
            return xr.DataArray(
                [[[0.2]]],
                dims=("time", "y", "x"),
                coords={"time": [0], "y": [0], "x": [0]},
            )

    regions = pd.DataFrame({"region": ["R1"]})
    monkeypatch.setattr(
        physical,
        "aggregation_matrix",
        lambda *args: (
            sparse.csr_matrix([[1.0]]),
            xr.DataArray([[1.0]], dims=("y", "x")),
            np.ones(1),
        ),
    )
    weights = {name: sparse.csr_matrix([[10.0]]) for name in ["wind", "solar"]}
    result, _ = physical.convert(
        Cutout(),
        regions,
        None,
        dict(
            wind_representation="selectable_t1_t2_t3",
            wind_plant_loss=0.0,
            pv_additional_loss=0.0,
            pv_ilr=1.32,
            inverter_efficiency=0.96,
        ),
        land_weights=weights,
    )
    assert calls == [115.0, 130.0, 100.0]
    for design, expected in [("t1", 0.115), ("t2", 0.130), ("t3", 0.100)]:
        assert float(result[f"wind_cf_{design}"].item()) == pytest.approx(expected)
    assert result.wind_cf.equals(result.wind_cf_t1)


def test_package_curves_match_designs_and_moderate_costs():
    settings = tomllib.loads((ROOT / "config/renewables/canada.toml").read_text())
    settings["technology"]["wind_representation"] = "selectable_t1_t2_t3"
    crosswalk, costs = stage.select_costs(settings, ROOT)
    assert set(crosswalk.tech) == {
        "WIND_GEN_T1",
        "WIND_GEN_T2",
        "WIND_GEN_T3",
        "SOLAR_GEN",
    }
    assert crosswalk.scenario.eq("Moderate").all()
    assert len(costs) == 12
    for design, power, height in [("T1", 6.0, 115), ("T2", 8.3, 130), ("T3", 3.3, 100)]:
        curve = turbine_config(None, design)
        assert curve["P"] == power
        assert curve["hub_height"] == height
        assert np.max(curve["POW"]) == pytest.approx(power)
        assert curve["POW"][-1] == 0
    assert package_identity(settings)["version"] == "0.2.2"
    settings["technology"]["wind_representation"] = "t1_only"
    fallback, _ = stage.select_costs(settings, ROOT)
    assert set(fallback.tech) == {"WIND_GEN", "SOLAR_GEN"}


def test_shared_wind_gold_bounds_and_schema(tmp_path):
    settings = tomllib.loads((ROOT / "config/renewables/canada.toml").read_text())
    settings["technology"]["wind_representation"] = "selectable_t1_t2_t3"
    crosswalk, costs = stage.select_costs(settings, ROOT)
    assumptions = tmp_path / "capacity.csv"
    pd.DataFrame(
        [
            dict(
                region="R1",
                tech=t,
                eligible_area_km2=10.0,
                density_mw_per_km2=2.0,
                basis="test",
                source="test",
            )
            for t in ["WIND_GEN", "SOLAR_GEN"]
        ]
    ).to_csv(assumptions, index=False)
    settings["capacity_limits"] = dict(enabled=True, input_csv=str(assumptions))
    annual = pd.DataFrame(
        {
            "wind_cf": [0.3],
            "wind_cf_t1": [0.3],
            "wind_cf_t2": [0.4],
            "wind_cf_t3": [0.5],
            "solar_cf": [0.2],
        },
        index=["R1"],
    )
    tables = stage.gold_tables(annual, crosswalk, costs, settings, tmp_path / "gold")
    assert len(tables["limit_capacity"]) == 2
    assert {r["tech_or_group"] for r in tables["limit_capacity"]} == {
        "WIND_RESOURCE",
        "SOLAR_GEN",
    }
    assert {r["tech"] for r in tables["tech_group_member"]} == {
        "WIND_GEN_T1",
        "WIND_GEN_T2",
        "WIND_GEN_T3",
    }
    assert {
        r["tech_or_group"]: r["factor"] for r in tables["limit_annual_capacity_factor"]
    }["WIND_GEN_T3"] == 0.5
    stage.validate_temoa_tables(
        tables, ROOT.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
    )
    encoded = {
        name: pd.DataFrame(rows).iloc[:0].copy() for name, rows in tables.items()
    }
    report = apply_bundle(encoded, tmp_path / "gold", {"R1"}, settings["model_period"])
    assert len(report["technologies"]) == 4
    # A missing member must fail before mutation, even with an updated file checksum.
    member = tmp_path / "gold/tech_group_member.csv"
    pd.read_csv(member).iloc[:2].to_csv(member, index=False)
    import json
    from geocanoe.acquisition.era5_renewables import sha256

    path = tmp_path / "gold/contract.json"
    contract = json.loads(path.read_text())
    contract["table_sha256"]["tech_group_member"] = sha256(member)
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match="membership"):
        apply_bundle(encoded, tmp_path / "gold", {"R1"}, settings["model_period"])


def test_selectable_wind_requires_shared_bounds(tmp_path):
    settings = tomllib.loads((ROOT / "config/renewables/canada.toml").read_text())
    settings["technology"]["wind_representation"] = "selectable_t1_t2_t3"
    crosswalk, costs = stage.select_costs(settings, ROOT)
    with pytest.raises(ValueError, match="shared capacity"):
        stage.gold_tables(pd.DataFrame(), crosswalk, costs, settings, tmp_path / "gold")
