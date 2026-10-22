"""Audit a completed regional Silver run without downloading or modifying profiles."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.preprocessing.renewables_weather import annual_profiles


def validate(output, previous_output=None):
    output = Path(output)
    record = json.loads((output / "manifest.json").read_text())
    paths = sorted(output.glob("month_??.nc"))
    assert len(paths) == 12, "Require twelve monthly profile files"
    for path in paths:
        assert sha256(path) == record["monthly_sha256"][path.name]
    hourly, annual = annual_profiles(paths, 2018)
    values = hourly.to_array().values
    assert np.isfinite(values).all() and values.min() >= 0 and values.max() <= 1
    assert annual.index.is_unique and len(annual) == record["regions"]
    exported = pd.read_csv(output / "annual_capacity_factors.csv", index_col=0)
    pd.testing.assert_frame_equal(
        annual, exported, check_dtype=False, atol=1e-12, rtol=1e-12
    )
    tables = output / "temoa_inputs"
    contract = json.loads((tables / "contract.json").read_text())
    assert contract["currency"] == "CAD2020"
    assert contract["capacity_unit"] == "MW"
    assert contract["weather_year"] == 2018 and contract["model_hours"] == 8760
    for name, checksum in contract["table_sha256"].items():
        assert sha256(tables / f"{name}.csv") == checksum
    costs = pd.read_csv(output / "atb_selected_rows.csv")
    basis = contract["currency_conversion"]["basis"]
    multiplier = (
        basis["cad_per_usd"]
        * basis["target_price_index"]
        / basis["source_price_index"]
        / basis["target_scale"]
    )
    assert np.isclose(multiplier, contract["currency_conversion"]["multiplier"])
    expected_techs = {"WIND_GEN", "SOLAR_GEN"}
    for name in [
        "cost_invest",
        "cost_fixed",
        "efficiency",
        "limit_annual_capacity_factor",
    ]:
        frame = pd.read_csv(tables / f"{name}.csv")
        frame = frame.rename(columns={"tech_or_group": "tech"})
        assert len(frame) == 2 * len(annual)
        assert set(frame.tech) == expected_techs
        assert not frame.duplicated(["region", "tech"]).any()
        assert set(frame.region) == set(annual.index)
        for tech in expected_techs:
            selected = frame.loc[frame.tech == tech]
            if name in {"cost_invest", "cost_fixed"}:
                metric = "OCC" if name == "cost_invest" else "Fixed O&M"
                source = costs.loc[
                    (costs.tech == tech) & (costs.core_metric_parameter == metric)
                ]
                assert len(source) == 1
                assert np.allclose(
                    selected.cost, float(source.value.iloc[0]) * 1000 * multiplier
                )
            elif name == "efficiency":
                assert (selected.efficiency == 1).all()
            else:
                field = "wind_cf" if tech == "WIND_GEN" else "solar_cf"
                assert np.allclose(
                    selected.set_index("region").factor.reindex(annual.index),
                    annual[field],
                )
    comparison = None
    if previous_output is not None:
        old = Path(previous_output) / "annual_capacity_factors.csv"
        previous = pd.read_csv(old, index_col=0).reindex(annual.index)
        assert not previous.isna().any().any(), (
            "Previous run must cover the same regions"
        )
        comparison = (annual - previous).abs().max().to_dict()
    report = dict(
        passed=True,
        regions=len(annual),
        hours=hourly.sizes["time"],
        weather_year=2018,
        technology_options_per_region=2,
        cf_statistics=annual.agg(["min", "mean", "max"]).to_dict(),
        currency="CAD2020",
        usd2023_to_cad2020=multiplier,
        maximum_absolute_cf_change_from_previous_pilot=comparison,
        checks=[
            "monthly and table checksums",
            "complete continuous UTC year",
            "finite CF bounds",
            "annual means equal hourly means",
            "one option per region and technology",
            "native ATB cost conversion",
            "annual Gold factors equal Silver CFs",
        ],
    )
    (output / "validation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    hourly.close()
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--previous-output", type=Path)
    args = parser.parse_args()
    validate(args.output, args.previous_output)
