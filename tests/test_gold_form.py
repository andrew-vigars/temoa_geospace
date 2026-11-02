"""Model settings, independent policies, matrices and actual Tk controls."""
from copy import deepcopy
from pathlib import Path
import runpy
import tomllib

import pytest
import tomlkit

from geocanoe.config import load_model_config
from geocanoe.execution.gold_form import recipe_from_form, validate_model_structure
from geocanoe.execution.gold_cases import model_cases

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    model = tomllib.loads((ROOT / 'registry/model.toml').read_text(encoding='utf-8'))
    values = dict(name='trial', mode='Sensitivity matrix', shares='0, 20, 100',
                  emissions_policy='none + emissions_price', prices='100, 250, 350, 500',
                  storage_requirement='none', storage='0', cumulative='0', start='2025', end='2050',
                  projection='constant', grid_type='projected', resolution='25', impedance='none', roads=True,
                  pipelines=True, disabled='H2_PIPE, H2_TRUCK', fallback='0',
                  global_discount_rate='3', default_loan_rate='4',
                  build_profile='config/build_profiles/sample_build_profile.toml',
                  continue_on_failure=True, skip_completed=True, retry_failed=False)
    values.update({f'{tech}_pipeline_eos_stack_count': '2' for tech in ('co2', 'gsl', 'h2', 'metoh')})
    template = {'matrix': {}, 'model': model, 'batch': {}}
    return values, template


def test_matrix_preserves_model_defaults_and_normalizes_inactive_policy_values(tmp_path):
    values, template = inputs()
    original = deepcopy(template)
    recipe = recipe_from_form(values, template)
    cases = list(model_cases(recipe))
    assert len(cases) == 15  # 3 shares x (one unpriced + four priced cases)
    assert template == original
    assert 'confirmed_co2_outlier_facility_ids' not in recipe['matrix']
    assert recipe['matrix']['interaction'] == 'prompt'
    assert recipe['model']['conventional_gasoline'] == original['model']['conventional_gasoline']
    assert cases[0]['document']['finance'] == {'global_discount_rate': .03, 'default_loan_rate': .04}
    assert cases[0]['document']['transport_modes']['disabled_technologies'] == ['H2_PIPE', 'H2_TRUCK']
    assert set(cases[0]['document']['pipeline_costs']['eos_stack_counts'].values()) == {2}
    for case in cases:
        path = tmp_path / f"{case['stem']}.toml"
        path.write_text(tomlkit.dumps(case['document']), encoding='utf-8')
        load_model_config(ROOT / 'registry/model.toml', path)


@pytest.mark.parametrize('emissions', ['none', 'emissions_price'])
@pytest.mark.parametrize('storage', ['none', 'minimum_point_source_fraction', 'minimum_cumulative_activity'])
def test_single_scenario_allows_independent_storage_and_pricing(emissions, storage, tmp_path):
    values, template = inputs()
    values.update(mode='Single scenario', emissions_policy=emissions, storage_requirement=storage,
                  shares='20.5', prices='250', storage='75', cumulative='1000000')
    cases = list(model_cases(recipe_from_form(values, template)))
    assert len(cases) == 1
    case = cases[0]
    assert case['share'] == .205
    assert case['price'] == (250 if emissions == 'emissions_price' else 0)
    assert case['storage_fraction'] == (.75 if storage == 'minimum_point_source_fraction' else 0)
    assert case['storage_cumulative'] == (1000000 if storage == 'minimum_cumulative_activity' else 0)
    path = tmp_path / 'scenario.toml'
    path.write_text(tomlkit.dumps(case['document']), encoding='utf-8')
    load_model_config(ROOT / 'registry/model.toml', path)


@pytest.mark.parametrize('key,value', [('shares', '20,20'), ('shares', '101'), ('prices', 'nan'),
                                      ('prices', '-10'), ('storage', '101'), ('end', '2020'),
                                      ('resolution', '0'), ('global_discount_rate', '100'),
                                      ('co2_pipeline_eos_stack_count', '1.5')])
def test_invalid_form_inputs_are_rejected(key, value):
    values, template = inputs()
    values[key] = value
    if key == 'storage':
        values['storage_requirement'] = 'minimum_point_source_fraction'
    with pytest.raises(ValueError):
        recipe_from_form(values, template)


def test_finance_and_storage_sensitivities_are_crossed_and_deduplicated():
    values, template = inputs()
    values.update(shares='20', prices='100,250', global_discount_rate='2,3',
                  storage_requirement='none + minimum_point_source_fraction', storage='50,75')
    cases = list(model_cases(recipe_from_form(values, template)))
    assert len(cases) == 18  # 3 emissions x 3 storage x 2 discount rates
    assert len({case['stem'] for case in cases}) == 18
    for case in cases:
        if case['storage_requirement'] == 'none':
            assert case['storage_fraction'] == 0


def test_combustion_factor_applies_only_to_priced_matrix_cases():
    values, template = inputs()
    values.update(shares='20', prices='100', fallback='3')
    cases = list(model_cases(recipe_from_form(values, template)))
    assert len(cases) == 2
    assert [case['document']['emissions']['fallback_gasoline_emission_factor'] for case in cases] == [0, 3]


def test_auto_exclusion_is_opt_in_and_saved_in_recipe():
    values, template = inputs()
    assert recipe_from_form(values, template)['matrix']['auto_exclude_outliers'] is False
    values['auto_exclude_outliers'] = True
    assert recipe_from_form(values, template)['matrix']['auto_exclude_outliers'] is True


def test_economic_config_cannot_be_loaded_as_a_model_or_embedded_in_a_recipe():
    _, template = inputs()
    economics = tomllib.loads((ROOT / 'registry/economics.toml').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='economic conversion configuration'):
        validate_model_structure(economics, template['model'], allow_scenario=True)
    contaminated = {**template['model'], **economics}
    with pytest.raises(ValueError, match='economic conversion configuration'):
        validate_model_structure(contaminated, template['model'])


def test_model_structure_accepts_overlays_and_rejects_unknown_nested_settings():
    _, template = inputs()
    validate_model_structure({'scenario': {'id': 'test'}, 'emissions': {'policy': 'none'}},
                             template['model'], allow_scenario=True)
    with pytest.raises(ValueError, match='pipeline_costs.eos_stack_counts'):
        validate_model_structure({'pipeline_costs': {'eos_stack_counts': {'NOT_A_PIPE': 1}}}, template['model'])


def test_ui_defaults_to_model_and_previews_without_building(monkeypatch, tmp_path):
    import tkinter as tk
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    configurator = runpy.run_path(str(ROOT / 'scripts/configure_gold.py'))['GoldConfigurator']
    window = tk.Tk()
    window.withdraw()
    try:
        app = configurator(window, ROOT, ROOT / 'registry/model.toml')
        check_gold_dependencies(app)
        _, cases = app.validated()
        app.show_cases(cases)
        window.update_idletasks()
        assert [app.tabs.tab(tab, 'text') for tab in app.tabs.tabs()] == ['1. Scenario settings', '2. Generate Gold', '3. TEMOA configurations']
        assert app.tabs.select() == str(app.settings_page)
        app.buttons['Review & generate'].invoke()
        assert app.tabs.select() == str(app.run_page)
        app.buttons['Edit settings'].invoke()
        assert app.tabs.select() == str(app.settings_page)
        assert app.settings_canvas.bbox('all')[3] > app.settings_canvas.winfo_height()
        assert len(app.table.get_children()) == 1
        assert app.variables['output'].get().startswith('experiments/')
        assert 'outliers' not in app.variables
        assert 'silver_config' not in app.variables
        assert app.buttons['Prepare Gold'].instate(['disabled'])
        assert app.buttons['Run batch'].instate(['disabled'])
        app.variables['mode'].set('Sensitivity matrix')
        app.variables['shares'].set('0,20,100')
        app.variables['emissions_policy'].set('none + emissions_price')
        app.variables['prices'].set('100,250')
        app.variables['storage_requirement'].set('none + minimum_cumulative_activity')
        app.variables['cumulative'].set('1000000')
        _, cases = app.validated()
        assert len(cases) == 18
        app.show_cases(cases)
        before = {key: variable.get() for key, variable in app.variables.items()}
        before_template = deepcopy(app.template)
        errors = []
        monkeypatch.setattr('tkinter.filedialog.askopenfilename', lambda **_: str(ROOT / 'registry/economics.toml'))
        monkeypatch.setattr('tkinter.messagebox.showerror', lambda title, message: errors.append(message))
        app.load()
        assert errors and 'economic conversion configuration' in errors[0]
        assert app.template == before_template
        assert {key: variable.get() for key, variable in app.variables.items()} == before
        assert len(app.table.get_children()) == 18
        assert window.winfo_reqheight() <= 820
        check_temoa_panel(app, tmp_path)
        check_prompt_worker(app, window, monkeypatch, tmp_path)
    finally:
        window.destroy()


def check_temoa_panel(app, directory):
    import json
    cases = []
    for sid in ['ui-a', 'ui-b']:
        database = directory / f'{sid}.sqlite'
        database.write_bytes(b'Gold stand-in for UI mapping test')
        manifest = database.with_suffix('.manifest.json')
        manifest.write_text(json.dumps({'artifact': {'scenario_id': sid, 'fingerprint': sid}}), encoding='utf-8')
        cases.append({'scenario_id': sid, 'gold_database': database.relative_to(ROOT).as_posix(),
                      'gold_manifest': manifest.relative_to(ROOT).as_posix(), 'schema_fingerprint': sid})
    (directory / 'preparation.json').write_text(json.dumps({'status': 'prepared'}), encoding='utf-8')
    (directory / 'inventory.json').write_text(json.dumps({'cases': cases}), encoding='utf-8')
    panel = app.temoa_panel
    check_temoa_dependencies(panel)
    panel.load_bundle(directory)
    assert panel.fields['extensions'][0].get() == '["eos"]'
    panel.table.selection_set('ui-b')
    panel.select(False)
    assert panel.enabled == {'ui-a': True, 'ui-b': False}
    panel.fields['save_excel'][0].set(False)
    panel.generate()
    assert panel.runset is not None
    assert panel.run_button.instate(['!disabled'])
    config = tomllib.loads((panel.runset / 'solver/ui_a.toml').read_text(encoding='utf-8'))
    assert config['save_excel'] is False
    assert config['input_database'] == cases[0]['gold_database']
    assert [run['enabled'] for run in tomllib.loads((panel.runset / 'batch.toml').read_text())['runs']] == [True, False]
    panel.fields['save_lp_file'][0].set(True)
    assert panel.runset is None
    assert panel.bundle == directory
    assert panel.run_button.instate(['disabled'])
    panel.fields['solver.name'][0].set('cbc')
    assert panel.fields['solver.options'][0].get() == '{}'
    app.variables['skip_completed'].set(False)
    assert panel.bundle == directory
    panel.select(False, all_rows=True)
    panel.name.set('all_disabled')
    panel.generate()
    assert panel.runset is not None
    assert panel.run_button.instate(['disabled'])


def check_gold_dependencies(app):
    saved = {key: variable.get() for key, variable in app.variables.items()}
    def widget(key):
        return app.field_widgets[key][0]
    try:
        assert app.variables['auto_exclude_outliers'].get() is False
        app.variables['auto_exclude_outliers'].set(True)
        settings, _ = app.validated()
        assert settings['matrix']['auto_exclude_outliers'] is True
        assert widget('prices').instate(['disabled'])
        assert widget('storage').instate(['disabled'])
        app.variables['emissions_policy'].set('emissions_price')
        assert widget('prices').instate(['!disabled'])
        app.variables['prices'].set('250')
        app.variables['fallback'].set('3')
        app.variables['emissions_policy'].set('none')
        assert widget('fallback').instate(['disabled'])
        assert app.variables['fallback'].get() == '3'
        _, cases = app.validated()
        assert cases[0]['document']['emissions']['fallback_gasoline_emission_factor'] == 0
        app.variables['storage'].set('inactive invalid value')
        app.validated()
        app.variables['storage_requirement'].set('minimum_point_source_fraction')
        assert widget('storage').instate(['!disabled'])
        assert widget('cumulative').instate(['disabled'])
        app.variables['storage'].set('75')
        app.variables['storage_requirement'].set('minimum_cumulative_activity')
        assert widget('storage').instate(['disabled'])
        assert widget('cumulative').instate(['!disabled'])
        assert app.variables['storage'].get() == '75'
        app.variables['storage_requirement'].set('none')
        app.variables['pipelines'].set(False)
        assert widget('impedance').instate(['disabled'])
        assert widget('co2_pipeline_eos_stack_count').instate(['disabled'])
        app.variables['co2_pipeline_eos_stack_count'].set('inactive invalid value')
        app.validated()
        app.variables['pipelines'].set(True)
        app.variables['co2_pipeline_eos_stack_count'].set('1')
        assert widget('impedance').instate(['!disabled'])
        app.busy = True
        app.refresh_buttons()
        assert widget('start').instate(['disabled'])
        app.busy = False
        app.refresh_buttons()
        assert widget('start').instate(['!disabled'])
        assert widget('prices').instate(['disabled'])
    finally:
        app.busy = False
        for key, value in saved.items():
            app.variables[key].set(value)


def check_temoa_dependencies(panel):
    def widget(path):
        return panel.field_widgets[path][0]
    assert widget('MGA.iteration_limit').instate(['disabled'])
    panel.fields['scenario_mode'][0].set('mga')
    assert widget('MGA.iteration_limit').instate(['!disabled'])
    assert widget('myopic.view_depth').instate(['disabled'])
    panel.fields['MGA.iteration_limit'][0].set('200')
    panel.fields['scenario_mode'][0].set('myopic')
    assert widget('myopic.view_depth').instate(['!disabled'])
    assert widget('myopic.evolution_script').instate(['disabled'])
    panel.fields['myopic.evolving'][0].set(True)
    assert widget('myopic.evolution_script').instate(['!disabled'])
    panel.fields['scenario_mode'][0].set('perfect_foresight')
    assert widget('myopic.evolution_script').instate(['disabled'])
    assert panel.fields['MGA.iteration_limit'][0].get() == '200'
    panel.fields['MGA.iteration_limit'][0].set('inactive invalid value')
    panel.settings()  # Inactive mode values must not block normal runs.
    panel.fields['MGA.iteration_limit'][0].set('200')
    panel.fields['source_trace'][0].set(False)
    panel.fields['plot_commodity_network'][0].set(True)
    assert widget('plot_commodity_network').instate(['disabled'])
    assert panel.settings()['plot_commodity_network'] is False
    panel.fields['source_trace'][0].set(True)
    assert widget('plot_commodity_network').instate(['!disabled'])
    assert panel.fields['plot_commodity_network'][0].get() is True
    panel.fields['plot_commodity_network'][0].set(False)
    panel.fields['save_duals'][0].set(True)
    panel.fields['solver.name'][0].set('appsi_highs')
    assert widget('save_duals').instate(['disabled'])
    assert panel.settings()['save_duals'] is False
    panel.fields['solver.name'][0].set('gurobi')
    assert widget('save_duals').instate(['!disabled'])
    assert panel.fields['save_duals'][0].get() is True
    panel.fields['save_duals'][0].set(False)
    panel.set_busy(True)
    panel.set_busy(False)
    assert widget('scenario_mode').instate(['!disabled'])
    assert widget('MGA.iteration_limit').instate(['disabled'])



def check_prompt_worker(app, window, monkeypatch, tmp_path):
    import sys
    import threading
    import time
    prompts = []
    main_thread = threading.get_ident()
    def respond(request):
        assert threading.get_ident() == main_thread
        prompts.append(request)
        return len(prompts) == 1
    monkeypatch.setattr(app, 'ask_exclusion', respond)
    code = """import json, sys
for dataset, expected in [('Site points', 'y'), ('CO2 facilities', 'n')]:
    print('GOLD_INPUT_REQUEST=' + json.dumps({'prompt': 'Exclude?', 'context': dataset}), flush=True)
    assert sys.stdin.readline().strip() == expected
"""
    app.start([sys.executable, '-u', '-c', code], 'Test prompts', tmp_path)
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        window.update()
        time.sleep(.01)
    assert not app.busy
    assert [request['context'] for request in prompts] == ['Site points', 'CO2 facilities']
    assert 'complete' in app.status.get()
