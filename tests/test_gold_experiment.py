"""The Gold pilot freezes local inputs without touching active run destinations."""
import json
from pathlib import Path
import runpy
import sys

import pytest
import tomlkit

from geocanoe.execution.experiment import create_plan, verify_inputs

ROOT = Path(__file__).resolve().parents[1]
scenario_cases = runpy.run_path(str(ROOT / 'scripts/prepare_national_gasoline_matrix.py'))['scenario_cases']


def test_plan_isolated_resolved_and_change_detected(tmp_path):
    # Use a copy of the repository's minimal controlled inputs, not a real Gold build.
    root = tmp_path
    for name in ['registry/model.toml', 'config/build_profiles/sample_build_profile.toml', 'pyproject.toml']:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    settings = {
        'matrix': {'name': 'test', 'build_profile': 'config/build_profiles/sample_build_profile.toml',
                   'synthetic_shares': [0, .2], 'carbon_prices': [100],
                   'include_net_zero': True, 'net_zero_point_source_fraction': .75},
        'common': {'period_start': 2025, 'period_end': 2050,
                   'gasoline_silver_config': 'config/canoe_fuel/silver.toml',
                   'grid_type': 'projected', 'resolution': 25, 'roads_enabled': True,
                   'pipelines_enabled': True, 'pipeline_impedance_scope': 'none',
                   'co2_pipeline_eos_stack_count': 1, 'fallback_gasoline_emission_factor': 0},
        'batch': {'continue_on_failure': True},
    }
    database = root / 'data_files/existing.sqlite'
    database.parent.mkdir()
    database.write_bytes(b'not a snapshot input')
    settings['matrix']['existing_database'] = 'data_files/existing.sqlite'
    recipe = root / 'recipe.toml'
    recipe.write_text(tomlkit.dumps(settings), encoding='utf-8')
    output = root / 'experiment'
    plan = create_plan(root, recipe, output, list(scenario_cases(settings)))
    assert len(plan['cases']) == 6
    assert 'resolved_model' in plan['cases'][0]
    assert plan['settings']['matrix']['batch_path'] == 'experiment/batch.toml'
    assert (output / 'snapshots/recipe.toml').read_bytes() == recipe.read_bytes()
    assert not (output / 'batch.toml').exists()
    assert not (output / 'snapshots/data_files').exists()
    assert 'CAD2020/t' in (output / 'README.md').read_text(encoding='utf-8')
    assert len(json.loads((output / 'plan.json').read_text())['cases']) == 6
    verify_inputs(root, plan)
    recipe.write_text('# changed', encoding='utf-8')
    with pytest.raises(ValueError, match='Input changed'):
        verify_inputs(root, plan)
    with pytest.raises(FileExistsError):
        create_plan(root, recipe, output, [])


def test_preparation_failure_is_recorded_without_launching_solver(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    entry = runpy.run_path(str(ROOT / 'scripts/plan_gold_experiment.py'))['main']
    plan = {'name': 'failure-test', 'status': 'planned', 'cases': [], 'inputs': [],
            'settings': {'common': {'period_start': 2025, 'period_end': 2050},
                         'matrix': {'batch_path': 'batch.toml'}}, 'limitations': []}
    recipe = tmp_path / 'recipe.toml'
    recipe.write_text('# frozen', encoding='utf-8')
    from geocanoe.execution.experiment import digest
    plan['prepared_recipe_sha256'] = digest(recipe)
    (tmp_path / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    monkeypatch.setitem(entry.__globals__, 'find_project_root', lambda: tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('simulated Gold failure')
    monkeypatch.setitem(entry.__globals__, 'prepare', fail)
    monkeypatch.setattr(sys, 'argv', ['plan_gold_experiment.py', '--prepare', str(tmp_path)])
    with pytest.raises(RuntimeError, match='simulated Gold failure'):
        entry()
    record = json.loads((tmp_path / 'preparation.json').read_text())
    assert record['status'] == 'failed'
    assert 'simulated Gold failure' in (tmp_path / 'README.md').read_text()
    with pytest.raises(FileExistsError, match='already attempted'):
        entry()


def test_model_based_plan_contains_cumulative_storage_and_axes(tmp_path):
    import tomllib
    from geocanoe.execution.gold_cases import model_cases
    for name in ['registry/model.toml', 'config/build_profiles/sample_build_profile.toml', 'pyproject.toml']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    model = tomllib.loads((ROOT / 'registry/model.toml').read_text(encoding='utf-8'))
    model['storage'].update(requirement='minimum_cumulative_activity', minimum_cumulative_activity=1000000)
    settings = {'matrix': {'name': 'model-test', 'build_profile': 'config/build_profiles/sample_build_profile.toml',
                           'interaction': 'prompt'}, 'model': model,
                'axes': {'finance.global_discount_rate': [.02, .03]},
                'batch': {'continue_on_failure': True}}
    recipe = tmp_path / 'recipe.toml'
    recipe.write_text(tomlkit.dumps(settings), encoding='utf-8')
    output = tmp_path / 'experiments/test'
    plan = create_plan(tmp_path, recipe, output, list(model_cases(settings)))
    assert plan['kind'] == 'gold_model_matrix'
    assert len(plan['cases']) == 2
    assert plan['cases'][0]['storage_cumulative'] == 1000000
    report = (output / 'README.md').read_text(encoding='utf-8')
    assert 'minimum_cumulative_activity' in report
    assert 'finance.global_discount_rate' in report
    assert plan['settings']['matrix']['batch_path'] == 'experiments/test/batch.toml'


def test_ui_gold_preparation_defers_solver_and_batch_generation(tmp_path, monkeypatch):
    import tomllib
    from geocanoe.execution import temoa_config
    module = runpy.run_path(str(ROOT / 'scripts/prepare_national_gasoline_matrix.py'))
    prepare = module['prepare']
    for name in ['registry/model.toml', 'config/build_profiles/sample_build_profile.toml']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    settings = {'matrix': {'name': 'gold-only', 'build_profile': 'config/build_profiles/sample_build_profile.toml',
                           'interaction': 'prompt', 'defer_temoa_configs': True,
                           'scenario_dir': 'experiment/scenarios', 'solver_dir': 'experiment/solver',
                           'batch_path': 'experiment/batch.toml', 'inventory_path': 'experiment/inventory.json',
                           'log_dir': 'experiment/logs'},
                'model': tomllib.loads((ROOT / 'registry/model.toml').read_text(encoding='utf-8')),
                'batch': {'continue_on_failure': True}}
    recipe = tmp_path / 'recipe.toml'
    recipe.write_text(tomlkit.dumps(settings), encoding='utf-8')
    monkeypatch.setitem(prepare.__globals__, 'find_project_root', lambda: tmp_path)
    def fake_build(command, root, log, **kwargs):
        scenario = tomllib.loads(Path(command[-1]).read_text(encoding='utf-8'))
        database = tmp_path / 'gold.sqlite'
        database.write_bytes(b'No real Gold build')
        database.with_suffix('.manifest.json').write_text(json.dumps({'artifact': {
            'scenario_id': scenario['scenario']['id'], 'fingerprint': 'gold-only'}}))
        log.write_text(f'MATRIX_DATABASE={database}\n', encoding='utf-8')
        return 0
    monkeypatch.setitem(prepare.__globals__, 'run_build', fake_build)
    def forbidden(*args, **kwargs):
        raise AssertionError('Gold-only preparation must not generate TEMOA configs')
    monkeypatch.setattr(temoa_config, 'generate_temoa_config', forbidden)
    inventory = prepare(recipe, build=True)
    assert len(inventory) == 1 and inventory[0]['solver_config'] is None
    assert not (tmp_path / 'experiment/batch.toml').exists()
    assert not list((tmp_path / 'experiment/solver').glob('*.toml'))
