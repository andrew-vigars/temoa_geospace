"""Prepared Gold mappings, TEMOA options, revisions and disabled-run handling."""
import json
import tomllib

import pytest

from geocanoe.execution import batch
from geocanoe.execution.temoa_runset import create_runset, default_run_settings, prepared_inventory, validate_run_settings
from geocanoe.execution.temoa_config import temoa_default_document


def prepared(root, bundle):
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / 'preparation.json').write_text(json.dumps({'status': 'prepared'}), encoding='utf-8')
    cases = []
    for sid in ['case-a', 'case-b']:
        database = bundle / f'{sid}.sqlite'
        database.write_bytes(b'unchanged Gold stand-in; no model build or solve')
        manifest = database.with_suffix('.manifest.json')
        manifest.write_text(json.dumps({'artifact': {'scenario_id': sid, 'fingerprint': sid}}), encoding='utf-8')
        cases.append({'scenario_id': sid, 'gold_database': database.relative_to(root).as_posix(),
                      'gold_manifest': manifest.relative_to(root).as_posix(), 'schema_fingerprint': sid})
    (bundle / 'inventory.json').write_text(json.dumps({'cases': cases}), encoding='utf-8')
    return cases


def test_runset_maps_gold_and_records_disabled_cases_without_rebuilding(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, 'PROJECT_ROOT', tmp_path)
    bundle = tmp_path / 'experiments/test'
    cases = prepared(tmp_path, bundle)
    original = (tmp_path / cases[0]['gold_database']).read_bytes()
    settings = default_run_settings()
    settings.update(save_excel=False, save_lp_file=True)
    settings['solver']['options']['MIPGap'] = .02
    selection = {'case-a': True, 'case-b': False}
    policies = dict(continue_on_failure=True, skip_completed=True, retry_failed=False)
    output = create_runset(tmp_path, bundle, 'selection_1', settings, selection, policies)
    document = tomllib.loads((output / 'solver/case_a.toml').read_text(encoding='utf-8'))
    assert document['scenario'] == 'case_a'
    assert document['input_database'] == document['output_database'] == cases[0]['gold_database']
    assert document['save_excel'] is False and document['save_lp_file'] is True
    assert document['solver']['options']['MIPGap'] == .02
    assert set(document) == set(temoa_default_document())
    parsed_batch = tomllib.loads((output / 'batch.toml').read_text(encoding='utf-8'))
    assert [run['enabled'] for run in parsed_batch['runs']] == [True, False]
    record = json.loads((output / 'runset.json').read_text())
    assert record['cases'][1]['enabled'] is False
    assert record['cases'][0]['gold_scenario_id'] == 'case-a'
    assert record['cases'][0]['solver_config_sha256']
    assert (tmp_path / cases[0]['gold_database']).read_bytes() == original
    before = (output / 'solver/case_a.toml').read_bytes()
    with pytest.raises(FileExistsError):
        create_runset(tmp_path, bundle, 'selection_1', settings, selection, policies)
    settings['save_excel'] = True
    create_runset(tmp_path, bundle, 'selection_2', settings, selection, policies)
    assert (output / 'solver/case_a.toml').read_bytes() == before


def test_wrong_gold_identity_stops_generation_before_files_are_written(tmp_path):
    bundle = tmp_path / 'experiment'
    cases = prepared(tmp_path, bundle)
    path = tmp_path / cases[0]['gold_manifest']
    path.write_text(json.dumps({'artifact': {'scenario_id': 'other', 'fingerprint': 'case-a'}}))
    with pytest.raises(ValueError, match='manifest does not match'):
        create_runset(tmp_path, bundle, 'bad', default_run_settings(), {'case-a': True, 'case-b': True},
                      dict(continue_on_failure=True, skip_completed=True, retry_failed=False))
    assert not (bundle / 'temoa_runs/bad').exists()


def test_run_settings_types_and_protected_mapping():
    for update in [{'save_excel': 'false'}, {'input_database': 'elsewhere.sqlite'},
                   {'days_per_period': 0}, {'scenario_mode': 'not_a_mode'},
                   {'solver': {'name': 'appsi_highs', 'options': {}}, 'save_duals': True}]:
        with pytest.raises(ValueError):
            validate_run_settings(update)
    settings = validate_run_settings({'solver': {'name': 'cbc'}})
    assert settings['solver'] == {'name': 'cbc', 'options': {}}
    assert validate_run_settings({'solver': 'cbc'})['solver'] == {'name': 'cbc', 'options': {}}
    assert validate_run_settings({'morris': {'seed': 123}})['morris']['seed'] == 123


def test_plan_only_folder_has_actionable_error(tmp_path):
    (tmp_path / 'plan.json').write_text('{"status": "planned"}', encoding='utf-8')
    with pytest.raises(ValueError, match='Click Prepare Gold on tab 2'):
        prepared_inventory(tmp_path)
    assert not (tmp_path / 'preparation.json').exists()


@pytest.mark.parametrize('status', ['preparing', 'failed'])
def test_unfinished_preparation_reports_its_status(tmp_path, status):
    (tmp_path / 'preparation.json').write_text(json.dumps({'status': status}), encoding='utf-8')
    with pytest.raises(ValueError, match=f'Gold preparation status is {status}'):
        prepared_inventory(tmp_path)
