"""Audit a completed regional Silver run without downloading or modifying profiles."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.preprocessing.renewables_weather import annual_profiles
from geocanoe.preprocessing.wind_options import MULTI_TECHS, WIND_GROUP, profile_field


def validate(output, previous_output=None):
    output = Path(output)
    record = json.loads((output / "manifest.json").read_text())
    paths = sorted(output.glob("month_??.nc"))
    assert len(paths) == 12, "Require twelve monthly profile files"
    for path in paths:
        assert sha256(path) == record["monthly_sha256"][path.name]
    hourly, annual = annual_profiles(paths, 2018)
    values = hourly.to_array().values
    assert np.isfinite(values).all() and values.min() >= 0 and values.max() <= 1 + 1e-8
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
        basis["cad_per_source_currency"] * basis["source_scale"]
        * basis["target_price_index"]
        / basis["source_price_index"]
        / basis["target_scale"]
    )
    assert np.isclose(multiplier, contract["currency_conversion"]["multiplier"])
    selectable = contract.get("wind_representation") == "selectable_t1_t2_t3"
    assigned = contract.get("wind_representation") == "resource_assigned"
    if assigned:
        directory = output / "wind_resource"
        assignment_record = json.loads((directory / "manifest.json").read_text())
        for name, digest in assignment_record["sha256"].items():
            assert sha256(directory / name) == digest
        mixture = pd.read_csv(directory / "regional_mix.csv").set_index("region")
        fractions = mixture[["T1_fraction", "T2_fraction", "T3_fraction"]]
        assert np.isfinite(fractions).all().all() and fractions.ge(0).all().all()
        assert np.allclose(
            fractions.sum(axis=1), mixture.represented_wind_km2.gt(0).astype(float)
        )
    expected_techs = (
        MULTI_TECHS | {"SOLAR_GEN"} if selectable else {"WIND_GEN", "SOLAR_GEN"}
    )
    for name in [
        "cost_invest",
        "cost_fixed",
        "efficiency",
        "limit_annual_capacity_factor",
    ]:
        frame = pd.read_csv(tables / f"{name}.csv")
        frame = frame.rename(columns={"tech_or_group": "tech"})
        assert len(frame) == len(expected_techs) * len(annual)
        assert set(frame.tech) == expected_techs
        assert not frame.duplicated(["region", "tech"]).any()
        assert set(frame.region) == set(annual.index)
        for tech in expected_techs:
            selected = frame.loc[frame.tech == tech]
            if name in {"cost_invest", "cost_fixed"}:
                metric = "OCC" if name == "cost_invest" else "Fixed O&M"
                if assigned and tech == "WIND_GEN":
                    expected = (
                        sum(
                            mixture[f"{design}_fraction"]
                            * float(
                                costs.loc[
                                    (costs.tech == f"WIND_GEN_{design}")
                                    & (costs.core_metric_parameter == metric),
                                    "value_per_MW",
                                ].iloc[0]
                            )
                            for design in ["T1", "T2", "T3"]
                        )
                        * multiplier
                    )
                    assert np.allclose(
                        selected.set_index("region").cost.reindex(expected.index),
                        expected,
                    )
                    continue
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
                field = profile_field(tech)
                assert np.allclose(
                    selected.set_index("region").factor.reindex(annual.index),
                    annual[field],
                )
    if selectable:
        members = pd.read_csv(tables / "tech_group_member.csv")
        assert set(zip(members.group_name, members.tech)) == {
            (WIND_GROUP, tech) for tech in MULTI_TECHS
        }
        bounds = pd.read_csv(tables / "limit_capacity.csv")
        assert len(bounds) == 2 * len(annual)
        assert set(bounds.tech_or_group) == {WIND_GROUP, "SOLAR_GEN"}
        assert not bounds.duplicated(["region", "tech_or_group"]).any()
        assert np.isfinite(bounds.capacity).all() and bounds.capacity.ge(0).all()
    land_enabled = (
        record.get("sources", {})
        .get("land_exclusion", {})
        .get("policy", {})
        .get("enabled", False)
    )
    if land_enabled:
        directory = output / "land_exclusion"
        land_record = json.loads((directory / "manifest.json").read_text())
        for name, checksum in land_record["sha256"].items():
            assert sha256(directory / name) == checksum
        assumptions = pd.read_csv(
            (output / "wind_resource" if assigned else directory)
            / "regional_capacity_assumptions.csv"
        )
        bounds = pd.read_csv(tables / "limit_capacity.csv")
        for tech in ["WIND_GEN", "SOLAR_GEN"]:
            expected = assumptions.loc[assumptions.tech.eq(tech)].set_index("region")
            target = WIND_GROUP if selectable and tech == "WIND_GEN" else tech
            actual = (
                bounds.loc[bounds.tech_or_group.eq(target)].set_index("region").capacity
            )
            assert np.allclose(
                actual.reindex(expected.index),
                expected.eligible_area_km2 * expected.density_mw_per_km2,
            )
    comparison = None
    if previous_output is not None:
        old = Path(previous_output) / "annual_capacity_factors.csv"
        previous = pd.read_csv(old, index_col=0).reindex(annual.index)
        assert not previous.isna().any().any(), (
            "Previous run must cover the same regions"
        )
        shared = annual.columns.intersection(previous.columns)
        comparison = (annual[shared] - previous[shared]).abs().max().to_dict()
    report = dict(
        passed=True,
        regions=len(annual),
        hours=hourly.sizes["time"],
        weather_year=2018,
        technology_options_per_region=len(expected_techs),
        wind_representation=contract.get("wind_representation", "t1_only"),
        land_exclusion_enabled=land_enabled,
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
