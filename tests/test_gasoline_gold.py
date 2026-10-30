"""Conventional price mapping, Gold identity and national blend contracts."""
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from geocanoe.config import load_geospatial_build_config, load_model_config
from geocanoe.schema.build import build_schema_fingerprint, rebuild_node_costs
from geocanoe.schema.fuel_policy import rebuild_national_synthetic_share
from geocanoe.schema.gasoline import map_conventional_prices, validate_encoded_gasoline
from test_carbon_policy import carbon_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prices():
    return pd.DataFrame([dict(
        province=p, period=2025, period_end=2050, price_year=2050, cost=cost,
        units='CAD2020/t gasoline', source_series_id='native_gasoline',
        price_boundary='us_sector_delivered_including_embedded_taxes',
        status='normalized_with_explicit_assumptions', is_territorial_proxy=p == 'NT',
    ) for p, cost in [('ON', 900), ('QC', 1000), ('NL', 1100), ('NT', 1200)]])


@pytest.fixture
def sites():
    return pd.DataFrame(dict(region=['A', 'B', 'C'], province=['ON', 'QC', None],
                             demand=[40., 60., 0.], LCOE=[1., 2., 3.], co2_cost=[4., 5., 6.]))


def test_prices_follow_demand_province_and_never_supply_zero_demand_nodes(sites, prices):
    audit = map_conventional_prices(sites, prices, 2025, 2050)
    assert audit.region.tolist() == ['A', 'B']
    assert audit.cost.tolist() == [900, 1000]
    assert audit.demand.sum() == 100
    assert audit.price_year.eq(2050).all()


@pytest.mark.parametrize('mutation,match', [
    (lambda p: p.drop(index=1), 'Unpriced'),
    (lambda p: pd.concat([p, p.iloc[[0]]]), 'Duplicate'),
    (lambda p: p.assign(cost=0), 'positive normalized'),
    (lambda p: p.assign(cost=float('inf')), 'positive normalized'),
    (lambda p: p.assign(cost=float('nan')), 'positive normalized'),
    (lambda p: p.assign(units='MCAD2020/PJ'), 'positive normalized'),
    (lambda p: p.assign(status='review_required'), 'positive normalized'),
    (lambda p: p.assign(period=2030), 'Gold period'),
    (lambda p: p.assign(period_end=2030), 'Gold period'),
    (lambda p: p.assign(price_year=2030), 'Gold period'),
    (lambda p: p.assign(price_boundary='wholesale'), 'positive normalized'),
])
def test_mapping_rejects_missing_or_incompatible_prices(sites, prices, mutation, match):
    with pytest.raises(ValueError, match=match):
        map_conventional_prices(sites, mutation(prices), 2025, 2050)


@pytest.mark.parametrize('province', [None, 'ZZ'])
def test_unknown_demand_province_is_rejected(sites, prices, province):
    sites.loc[0, 'province'] = province
    with pytest.raises(ValueError, match='province'):
        map_conventional_prices(sites, prices, 2025, 2050)


def test_aliases_and_territorial_proxy_are_retained(sites, prices):
    sites.loc[0, 'province'] = 'NLLAB'
    sites.loc[1, 'province'] = 'NT'
    audit = map_conventional_prices(sites, prices, 2025, 2050)
    assert audit.province.tolist() == ['NL', 'NT']
    assert audit.is_territorial_proxy.tolist() == [False, True]


@pytest.mark.parametrize('share', [0, .2, 1])
def test_encoded_costs_and_blend_cap_match_same_annual_demand(sites, prices, share):
    db = carbon_fixture()
    audit = rebuild_node_costs(db, sites, 2025, prices, 2050)
    db['demand'] = pd.DataFrame([dict(region=r, period=2025, commodity='d_gsl', demand=d)
                                 for r, d in zip(audit.region, audit.demand)])
    route = db['efficiency'].loc[db['efficiency'].tech.eq('GSL_BACKUP')].iloc[0].to_dict()
    db['efficiency'] = pd.DataFrame([{**route, 'region': r} for r in audit.region])
    rebuild_national_synthetic_share(db, 2025, share)
    validate_encoded_gasoline(db, audit, 2025, share)
    cost = db['cost_variable'].query('tech == "GSL_BACKUP"')
    assert cost.cost.tolist() == [900, 1000]
    assert cost.vintage.tolist() == [2025, 2025]
    # Rebuilding must not leave duplicate costs or the old penalty behind.
    rebuild_node_costs(db, sites, 2025, prices, 2050)
    validate_encoded_gasoline(db, audit, 2025, share)
    db['cost_variable'].loc[db['cost_variable'].tech.eq('GSL_BACKUP'), 'cost'] = 500000
    with pytest.raises(AssertionError):
        validate_encoded_gasoline(db, audit, 2025, share)


def test_fingerprint_tracks_price_evidence_and_share():
    build = load_geospatial_build_config(ROOT / 'config/build_profiles/canada_renewables_t4_gcc.toml')
    model = load_model_config(ROOT / 'registry/model.toml', ROOT / 'registry/scenarios/synthetic_share_20.toml')
    def fingerprint(model=model, digest='price-a'):
        return build_schema_fingerprint(build, model, 'national', implementation_digest='fixed',
                                        conventional_gasoline_identity={'silver_manifest_sha256': digest})
    assert fingerprint() != fingerprint(digest='price-b')
    assert fingerprint() != fingerprint(replace(model, synthetic_gasoline_minimum_national_share=1.0))
    assert fingerprint() != fingerprint(replace(model, conventional_gasoline_silver_config='another-price.toml'))
    assert set(build.study_area.provinces) == {'AB','BC','MB','NB','NL','NS','NT','NU','ON','PE','QC','SK','YT'}


@pytest.mark.parametrize('path_value', ['""', '123', 'false'])
def test_conventional_price_source_config_must_be_a_path(tmp_path, path_value):
    scenario = tmp_path / 'prices.toml'
    scenario.write_text('[scenario]\nid="test-prices"\n[conventional_gasoline]\n'
                        f'silver_config={path_value}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='silver_config'):
        load_model_config(ROOT / 'registry/model.toml', scenario)


def test_scenario_can_select_another_explicit_silver_price_release(tmp_path):
    scenario = tmp_path / 'prices.toml'
    scenario.write_text('[scenario]\nid="test-prices"\n[conventional_gasoline]\n'
                        'silver_config="config/canoe_fuel/another-release.toml"\n', encoding='utf-8')
    model = load_model_config(ROOT / 'registry/model.toml', scenario)
    assert model.conventional_gasoline_silver_config == 'config/canoe_fuel/another-release.toml'


def test_period_mismatch_is_rejected_by_gold_bundle_loader(tmp_path):
    from geocanoe.schema import gasoline
    # Use a real maintained Silver artifact for the local integration check.
    config = ROOT / 'config/canoe_fuel/silver.toml'
    if not (ROOT / 'data_files/processed/gasoline_demand/fuel_supply/manifest.json').exists():
        pytest.skip('Local Silver release required for bundle integration')
    with pytest.raises(ValueError, match='period does not match Gold'):
        gasoline.load_price_bundle(config, 2030, 2050)


@pytest.mark.parametrize('share', [0, .2, 1])
@pytest.mark.parametrize('conventional_cost', [900, 1800])
def test_solver_uses_conventional_price_and_delivered_synthetic_share(tmp_path, prices, share, conventional_cost):
    from pyomo.environ import SolverFactory, value
    from temoa import TemoaConfig, TemoaSequencer
    from geocanoe.schema.build import export_sqlite
    from geocanoe.schema.gasoline import conventional_cost_rows
    if not SolverFactory('gurobi').available(False):
        pytest.skip('Gurobi required for backend integration')
    db = carbon_fixture(price=0)
    sites = pd.DataFrame([dict(region='R0', province='ON', demand=1.)])
    prices = prices.copy()
    prices.loc[prices.province.eq('ON'), 'cost'] = conventional_cost
    audit = map_conventional_prices(sites, prices, 2025, 2050)
    costs = db['cost_variable']
    db['cost_variable'] = pd.concat([
        costs.loc[~costs.tech.eq('GSL_BACKUP')],
        conventional_cost_rows(audit, 2025, 'test').reindex(columns=costs.columns),
    ], ignore_index=True)
    # Deliberately expensive synthetic supply and a 20% delivery loss make
    # the conventional cap bind and distinguish production from delivery.
    db['cost_variable'].loc[db['cost_variable'].tech.eq('METOH_PLANT'), 'cost'] = 5000
    db['efficiency']['efficiency'] = db['efficiency'].efficiency.astype(float)
    db['efficiency'].loc[db['efficiency'].tech.eq('GSL_DEMAND'), 'efficiency'] = .8
    rebuild_national_synthetic_share(db, 2025, share)
    validate_encoded_gasoline(db, audit, 2025, share)
    database = tmp_path / 'gasoline.sqlite'
    export_sqlite(db, database)
    config = tmp_path / 'solve.toml'
    config.write_text(
        'scenario="gasoline_gold_test"\nscenario_mode="perfect_foresight"\n'
        f'input_database="{database.as_posix()}"\noutput_database="{database.as_posix()}"\n'
        'solver="gurobi"\nneos=false\nsave_excel=false\nsave_duals=false\nsave_lp_file=false\n'
        'time_sequencing="seasonal_timeslices"\nreserve_margin="static"\n', encoding='utf-8')
    sequencer = TemoaSequencer(config=TemoaConfig.build_config(
        config_file=config, output_path=tmp_path, silent=True))
    sequencer.start()
    model = sequencer.pf_solved_instance
    flows = model.v_flow_out_annual
    def activity(tech):
        return sum(value(flows[index]) for index in flows if index[3] == tech)
    assert activity('GSL_BACKUP') == pytest.approx(1-share)
    assert activity('GSL_DEMAND') == pytest.approx(share)
    assert activity('METOH_PLANT') == pytest.approx(share/.8)
    expected_annual = conventional_cost*(1-share) + (5000+10)*share/.8
    assert value(model.total_cost) == pytest.approx(25*expected_annual)
