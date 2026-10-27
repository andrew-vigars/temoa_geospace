"""The independent run audit must accept T4 and include GCC in costs."""

import json
from pathlib import Path
import runpy
import tomllib

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.preprocessing.renewables import gold_tables, select_costs


ROOT = Path(__file__).resolve().parents[1]


def test_audit_t4_gcc_and_reject_omitted_connection_cost(tmp_path):
    settings = tomllib.loads(
        (ROOT / "config/renewables/canada_t4_gcc.toml").read_text()
    )
    crosswalk, costs = select_costs(settings, ROOT)
    directory = tmp_path / "wind_resource"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"sha256": {}}))
    pd.DataFrame(
        [
            dict(
                region="R1",
                represented_wind_km2=10,
                T1_fraction=0.1,
                T2_fraction=0.2,
                T3_fraction=0.3,
                T4_fraction=0.4,
            )
        ]
    ).to_csv(directory / "regional_mix.csv", index=False)
    annual = pd.DataFrame(
        {"wind_cf": [0.4], "solar_cf": [0.2]}, index=pd.Index(["R1"], name="region")
    )
    annual.to_csv(tmp_path / "annual_capacity_factors.csv")
    costs.to_csv(tmp_path / "atb_selected_rows.csv", index=False)
    gold_tables(annual, crosswalk, costs, settings, tmp_path / "temoa_inputs")
    checksums = {}
    for month in range(1, 13):
        start = pd.Timestamp(2018, month, 1)
        time = pd.date_range(
            start, start + pd.offsets.MonthBegin(), freq="h", inclusive="left"
        )
        data = xr.Dataset(
            {
                "wind_cf": (("region", "time"), np.full((1, len(time)), 0.4)),
                "solar_cf": (("region", "time"), np.full((1, len(time)), 0.2)),
            },
            coords={"region": ["R1"], "time": time},
        )
        path = tmp_path / f"month_{month:02d}.nc"
        data.to_netcdf(path, engine="netcdf4")
        checksums[path.name] = sha256(path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"regions": 1, "monthly_sha256": checksums})
    )
    validate = runpy.run_path(str(ROOT / "scripts/validate_renewables_run.py"))[
        "validate"
    ]
    assert validate(tmp_path)["passed"]

    # Re-checksum a deliberately wrong table so the independent cost calculation,
    # rather than the file-integrity check, must catch the omitted GCC.
    path = tmp_path / "temoa_inputs/cost_invest.csv"
    investment = pd.read_csv(path)
    contract_path = path.parent / "contract.json"
    contract = json.loads(contract_path.read_text())
    solar_gcc = costs.loc[
        costs.tech.eq("SOLAR_GEN") & costs.core_metric_parameter.eq("GCC"),
        "value_per_MW",
    ].item()
    investment.loc[investment.tech.eq("SOLAR_GEN"), "cost"] -= (
        solar_gcc * contract["currency_conversion"]["multiplier"]
    )
    investment.to_csv(path, index=False)
    contract["table_sha256"]["cost_invest"] = sha256(path)
    contract_path.write_text(json.dumps(contract))
    with pytest.raises(AssertionError):
        validate(tmp_path)
