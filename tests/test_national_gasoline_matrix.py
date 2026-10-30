"""Cross-product completeness and matched-policy boundaries for the manual batch."""
from pathlib import Path
import runpy

import pytest
import tomlkit

from geocanoe.config import load_model_config

ROOT = Path(__file__).resolve().parents[1]
scenario_cases = runpy.run_path(str(ROOT / 'scripts/prepare_national_gasoline_matrix.py'))['scenario_cases']


def settings():
    # Test inputs are independent of the user's ignored, mutable run recipe.
    return {
        'matrix': {'synthetic_shares': [0, .2, 1], 'carbon_prices': [100, 250, 350, 500],
                   'include_net_zero': True, 'net_zero_point_source_fraction': .75},
        'common': {'period_start': 2025, 'period_end': 2050,
                   'gasoline_silver_config': 'config/canoe_fuel/silver.toml',
                   'grid_type': 'projected', 'resolution': 25,
                   'roads_enabled': True, 'pipelines_enabled': True,
                   'pipeline_impedance_scope': 'none', 'co2_pipeline_eos_stack_count': 1,
                   'fallback_gasoline_emission_factor': 0},
    }


def test_complete_national_share_carbon_cross_product(tmp_path):
    cases = list(scenario_cases(settings()))
    assert len(cases) == 18
    assert len({case['document']['scenario']['id'] for case in cases}) == 18
    assert {(case['share'], case['policy']) for case in cases} == {
        (share, policy) for share in [0, .2, 1]
        for policy in ['baseline', 'net_zero', 'price_100', 'price_250', 'price_350', 'price_500']}
    common_infrastructure = []
    for case in cases:
        path = tmp_path / f"{case['stem']}.toml"
        path.write_text(tomlkit.dumps(case['document']), encoding='utf-8')
        model = load_model_config(ROOT / 'registry/model.toml', path)
        assert model.synthetic_gasoline_minimum_national_share == case['share']
        assert (model.time.start_year, model.time.end_year) == (2025, 2050)
        assert model.emissions.price_per_tonne == case['price']
        assert model.emissions.fallback_gasoline_emission_factor == 0
        if case['policy'] == 'net_zero':
            assert model.storage.requirement == 'minimum_point_source_fraction'
            assert model.storage.point_source_fraction == .75
            assert model.emissions.policy == 'none'
        else:
            assert model.storage.requirement == 'none'
            assert model.storage.point_source_fraction == 0
            assert model.emissions.policy == ('emissions_price' if case['price'] else 'none')
        common_infrastructure.append((model.basemap, model.transport_modes, model.pipeline_costs,
                                      model.conventional_gasoline_silver_config))
    assert all(item == common_infrastructure[0] for item in common_infrastructure)


@pytest.mark.parametrize('key,value', [('synthetic_shares', [0, 20, 100]),
                                      ('synthetic_shares', [0, 0]),
                                      ('carbon_prices', [100, -1]),
                                      ('carbon_prices', [100, 100])])
def test_invalid_matrix_axes_are_rejected(key, value):
    config = settings()
    config['matrix'][key] = value
    with pytest.raises(ValueError):
        list(scenario_cases(config))
