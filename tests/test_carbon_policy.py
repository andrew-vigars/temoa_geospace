from pathlib import Path
import sqlite3

import pandas as pd
import pytest
from pyomo.environ import SolverFactory, value
from temoa import TemoaConfig, TemoaSequencer

from geocanoe.config import load_model_config
from geocanoe.analysis.exports import export_output_tables
from geocanoe.schema.build import (
    export_sqlite,
    load_empty_temoa_v4_tables,
    rebuild_static_supporting_tables,
)
from geocanoe.schema.carbon import FACILITY_EMISSION, rebuild_carbon_accounting


ROOT = Path(__file__).resolve().parents[1]


def carbon_fixture(price=100, storage_capacity=None):
    db = load_empty_temoa_v4_tables()
    commodities = pd.DataFrame(
        [
            {"name": name, "flag": flag}
            for name, flag in [
                ("ethos", "s"),
                ("co2", "a"),
                ("co2_stored", "wa"),
                ("gsl", "a"),
                ("d_gsl", "d"),
            ]
        ]
    )
    rebuild_static_supporting_tables(db, commodities, 2025, 2050, 0, 0)
    db["region"] = pd.DataFrame([{"region": "R0"}], columns=db["region"].columns)
    techs = ["CO2_CAP", "CO2_INJECT", "METOH_PLANT", "GSL_BACKUP", "GSL_DEMAND"]
    db["technology"] = pd.DataFrame(
        [
            {
                **{
                    c: 0
                    for c in [
                        "unlim_cap",
                        "annual",
                        "reserve",
                        "curtail",
                        "retire",
                        "flex",
                        "exchange",
                        "seas_stor",
                    ]
                },
                "tech": tech,
                "flag": "p",
                "annual": 1,
                "unlim_cap": 1,
                "sector": "industrial",
            }
            for tech in techs
        ],
        columns=db["technology"].columns,
    )
    db["efficiency"] = pd.DataFrame(
        [
            {
                "region": "R0",
                "input_comm": inp,
                "tech": tech,
                "vintage": 2025,
                "output_comm": out,
                "efficiency": 1,
            }
            for tech, inp, out in [
                ("CO2_CAP", "ethos", "co2"),
                ("CO2_INJECT", "co2", "co2_stored"),
                ("METOH_PLANT", "co2", "gsl"),
                ("GSL_BACKUP", "ethos", "d_gsl"),
                ("GSL_DEMAND", "gsl", "d_gsl"),
            ]
        ],
        columns=db["efficiency"].columns,
    )
    db["cost_variable"] = pd.DataFrame(
        [
            {
                "region": "R0",
                "period": 2025,
                "tech": tech,
                "vintage": 2025,
                "cost": cost,
            }
            for tech, cost in zip(techs, [10, 30, 0, 20, 0])
        ],
        columns=db["cost_variable"].columns,
    )
    db["demand"] = pd.DataFrame(
        [{"region": "R0", "period": 2025, "commodity": "d_gsl", "demand": 1}],
        columns=db["demand"].columns,
    )
    if storage_capacity is not None:
        db["limit_activity"] = pd.DataFrame(
            [
                {
                    "region": "R0",
                    "period": 2025,
                    "tech_or_group": "CO2_INJECT",
                    "operator": "le",
                    "activity": storage_capacity,
                }
            ],
            columns=db["limit_activity"].columns,
        )
    rebuild_carbon_accounting(
        db,
        pd.DataFrame({"region": ["R0"], "co2": [100]}),
        2025,
        25,
        policy="emissions_price",
        price_per_tonne=price,
    )
    return db


def test_baseline_support_only_exists_in_positive_emission_regions():
    db = load_empty_temoa_v4_tables()
    rebuild_carbon_accounting(
        db, pd.DataFrame({"region": ["R0", "R1"], "co2": [100, 0]}),
        2025, 25, policy="emissions_price",
    )
    for table in ["capacity_to_activity", "lifetime_tech"]:
        baseline = db[table].loc[db[table].tech.eq("CO2_BASELINE")]
        assert baseline.region.tolist() == ["R0"]
        release = db[table].loc[db[table].tech.eq("CO2_RELEASE")]
        assert set(release.region) == {"R0", "R1"}


def test_price_config_overlay(tmp_path):
    path = tmp_path / "price.toml"
    path.write_text(
        '[scenario]\nid="carbon-price"\ndescription="Price test"\n'
        '[emissions]\npolicy="emissions_price"\nprice_per_tonne=100\n',
        encoding="utf-8",
    )
    config = load_model_config(ROOT / "registry/model.toml", path)
    assert config.emissions.policy == "emissions_price"
    assert config.emissions.price_per_tonne == 100
    assert config.storage.requirement == "none"


def test_committed_emissions_price_scenario():
    config = load_model_config(
        ROOT / "registry/model.toml",
        ROOT / "registry/scenarios/sample_emissions_price.toml",
    )
    assert config.emissions.policy == "emissions_price"
    assert config.emissions.fallback_gasoline_emission_factor == 0
    assert config.storage.requirement == "none"


@pytest.mark.parametrize(
    "setting",
    [
        'policy="invalid"',
        "price_per_tonne=-1",
        "price_per_tonne=inf",
        "price_per_tonne=nan",
        "price_per_tonne=true",
    ],
)
def test_invalid_price_config(tmp_path, setting):
    path = tmp_path / "invalid.toml"
    path.write_text(
        '[scenario]\nid="invalid"\ndescription="Invalid"\n[emissions]\n' + setting,
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_model_config(ROOT / "registry/model.toml", path)


def test_coefficients_price_incoming_carbon_and_preserve_storage_bounds():
    db = carbon_fixture(storage_capacity=60)
    assert (
        not db["emission_activity"]
        .tech.isin(["CO2_CAP", "CO2_INJECT", "GSL_BACKUP"])
        .any()
    )
    assert db["emission_activity"].activity.ge(0).all()
    assert db["limit_activity"].operator.tolist() == ["le"]
    assert db["cost_emission"].cost.tolist() == [100]


@pytest.mark.parametrize(
    "price,capacity,stored,fallback",
    [(0, None, 0, 0), (20, None, 0, 0), (100, None, 100, 1), (100, 60, 60, 0)],
)
def test_unchanged_temoa_v4_solves_carbon_price(
    tmp_path, price, capacity, stored, fallback
):
    if not SolverFactory("gurobi").available(False):
        pytest.skip("Gurobi is needed for the v4 integration solve")
    db = carbon_fixture(price, capacity)
    database = tmp_path / "carbon.sqlite"
    export_sqlite(db, database)
    config_path = tmp_path / "solve.toml"
    config_path.write_text(
        f'scenario="carbon_test"\nscenario_mode="perfect_foresight"\n'
        f'input_database="{database.as_posix()}"\noutput_database="{database.as_posix()}"\n'
        'solver="gurobi"\nneos=false\nsave_excel=false\nsave_duals=false\nsave_lp_file=false\n'
        'time_sequencing="seasonal_timeslices"\nreserve_margin="static"\n',
        encoding="utf-8",
    )
    config = TemoaConfig.build_config(
        config_file=config_path, output_path=tmp_path, silent=True
    )
    sequencer = TemoaSequencer(config=config)
    sequencer.start()
    model = sequencer.pf_solved_instance
    assert model is not None
    flows = model.v_flow_out_annual

    def by_tech(tech):
        return sum(value(flows[idx]) for idx in flows if idx[3] == tech)

    assert by_tech("CO2_BASELINE") == pytest.approx(100)
    assert by_tech("CO2_INJECT") == pytest.approx(stored)
    assert by_tech("GSL_BACKUP") == pytest.approx(fallback)
    emissions = sum(
        value(flows[r, 2025, i, t, v, o]) * row.activity
        for row in db["emission_activity"].itertuples(index=False)
        for r, i, t, v, o in [
            (row.region, row.input_comm, row.tech, row.vintage, row.output_comm)
        ]
        if row.emis_comm == FACILITY_EMISSION
    )
    assert emissions + stored == pytest.approx(100)
    expected_annual_cost = (
        10 * by_tech("CO2_CAP") + 30 * stored + 20 * fallback + price * emissions
    )
    assert value(model.total_cost) == pytest.approx(25 * expected_annual_cost)
    [workbook] = export_output_tables(database, tmp_path)
    carbon = pd.read_excel(workbook, sheet_name="CO2AccountingSummary")
    assert carbon.loc[
        carbon.metric.eq("facility_atmospheric_emissions"), "annual_carbon_tonnes"
    ].sum() == pytest.approx(emissions)
    supply = pd.read_excel(workbook, sheet_name="GasolineSupplySummary")
    assert supply.loc[
        supply.tech.eq("GSL_BACKUP"), "annual_gasoline_tonnes"
    ].sum() == pytest.approx(fallback)
    with sqlite3.connect(database) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_utilization_and_transport_coefficients_use_input_carbon():
    db = carbon_fixture()
    # Rebuild from fixture inputs, before the accounting layer.
    raw = load_empty_temoa_v4_tables()
    for name in ["commodity", "technology", "efficiency"]:
        raw[name] = db[name].copy()
    raw["commodity"] = raw["commodity"].loc[
        ~raw["commodity"].name.isin(
            [
                "co2_raw",
                "co2_released",
                "co2_facility_emission",
                "co2_fallback_emission",
            ]
        )
    ]
    raw["technology"] = raw["technology"].loc[
        ~raw["technology"].tech.isin(["CO2_BASELINE", "CO2_RELEASE"])
    ]
    raw["efficiency"] = (
        raw["efficiency"]
        .loc[~raw["efficiency"].tech.isin(["CO2_BASELINE", "CO2_RELEASE"])]
        .copy()
    )
    raw["efficiency"].loc[raw["efficiency"].tech.eq("CO2_CAP"), "input_comm"] = "ethos"
    raw["efficiency"]["efficiency"] = raw["efficiency"].efficiency.astype(float)
    raw["efficiency"].loc[raw["efficiency"].tech.eq("METOH_PLANT"), "efficiency"] = 0.5
    transport = {
        "region": "R0",
        "input_comm": "co2",
        "tech": "CO2_PIPE",
        "vintage": 2025,
        "output_comm": "co2",
        "efficiency": 0.8,
    }
    raw["efficiency"] = pd.concat(
        [raw["efficiency"], pd.DataFrame([transport])], ignore_index=True
    )
    rebuild_carbon_accounting(
        raw,
        pd.DataFrame({"region": ["R0"], "co2": [100]}),
        2025,
        25,
        policy="emissions_price",
        price_per_tonne=100,
    )
    coefficients = raw["emission_activity"].set_index("tech").activity
    assert coefficients["METOH_PLANT"] == pytest.approx(2)
    assert coefficients["CO2_PIPE"] == pytest.approx(0.25)
