from pathlib import Path

import pandas as pd
import pytest
from pyomo.environ import SolverFactory, value
from temoa import TemoaConfig, TemoaSequencer

from geocanoe.config import load_model_config
from geocanoe.schema.build import export_sqlite
from geocanoe.schema.fuel_policy import CONVENTIONAL_GROUP, rebuild_national_synthetic_share
from test_carbon_policy import carbon_fixture


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('share', [-0.1, 1.1, float('nan'), float('inf')])
def test_invalid_scenario_share(tmp_path, share):
    scenario = tmp_path / 'share.toml'
    scenario.write_text(f'[scenario]\nid="share-test"\n[synthetic_gasoline]\nminimum_national_share={share}\n')
    with pytest.raises(ValueError, match='minimum_national_share'):
        load_model_config(ROOT / 'registry/model.toml', scenario)


def test_committed_scenarios():
    for name, expected in [('synthetic_share_baseline', 0), ('synthetic_share_20', .2), ('synthetic_share_100', 1)]:
        cfg = load_model_config(ROOT / 'registry/model.toml', ROOT / f'registry/scenarios/{name}.toml')
        assert cfg.synthetic_gasoline_minimum_national_share == expected


def test_global_annual_cap_counts_backup_and_resets():
    db = carbon_fixture()
    db['demand'] = pd.DataFrame([dict(region='A', period=2025, commodity='d_gsl', demand=40),
                                 dict(region='B', period=2025, commodity='d_gsl', demand=60)])
    rebuild_national_synthetic_share(db, 2025, .2)
    cap = db['limit_activity'].query('tech_or_group == @CONVENTIONAL_GROUP')
    assert cap.region.tolist() == ['global']
    assert cap.activity.tolist() == [80]  # Annual, not multiplied by 25
    assert set(db['tech_group_member'].tech) == {'GSL_BACKUP'}
    rebuild_national_synthetic_share(db, 2025, 0)
    assert db['limit_activity'].query('tech_or_group == @CONVENTIONAL_GROUP').empty
    assert db['tech_group_member'].empty


@pytest.mark.parametrize('share', [0, .4, 1])
def test_solver_enforces_share_with_cheaper_conventional_supply(tmp_path, share):
    if not SolverFactory('gurobi').available(False):
        pytest.skip('Gurobi required for backend integration')
    db = carbon_fixture(price=100)
    # At this price storage + backup wins without a blending requirement.
    rebuild_national_synthetic_share(db, 2025, share)
    database = tmp_path / 'share.sqlite'
    export_sqlite(db, database)
    config_path = tmp_path / 'solve.toml'
    config_path.write_text(
        f'scenario="share_test"\nscenario_mode="perfect_foresight"\n'
        f'input_database="{database.as_posix()}"\noutput_database="{database.as_posix()}"\n'
        'solver="gurobi"\nneos=false\nsave_excel=false\nsave_duals=false\nsave_lp_file=false\n'
        'time_sequencing="seasonal_timeslices"\nreserve_margin="static"\n', encoding='utf-8')
    sequencer = TemoaSequencer(config=TemoaConfig.build_config(config_file=config_path, output_path=tmp_path, silent=True))
    sequencer.start()
    model = sequencer.pf_solved_instance
    flows = model.v_flow_out_annual
    by_tech = lambda tech: sum(value(flows[idx]) for idx in flows if idx[3] == tech)
    assert by_tech('GSL_BACKUP') == pytest.approx(1-share)
    assert by_tech('GSL_DEMAND') == pytest.approx(share)


def test_backup_is_the_only_registered_conventional_gasoline_route():
    technologies = pd.read_csv(ROOT / 'registry/techs.csv')
    routes = pd.read_csv(ROOT / 'registry/generation_efficiency.csv')
    assert 'GSL_EXISTING' not in set(technologies.tech)
    conventional = routes.loc[routes.input_comm.eq('ethos') & routes.output_comm.isin(['gsl', 'd_gsl'])]
    assert conventional.tech.tolist() == ['GSL_BACKUP']
    assert conventional.output_comm.tolist() == ['d_gsl']
    assert conventional.efficiency.tolist() == [1.0]
