"""Category selection behavior without building research artifacts."""
from pathlib import Path
import tkinter as tk
import tomllib

from geocanoe.execution.silver_panel import SilverPanel


ROOT = Path(__file__).resolve().parents[1]


def test_categories_preserve_profiles_and_road_selection_is_exclusive():
    window = tk.Tk()
    window.withdraw()
    try:
        panel = SilverPanel(window, ROOT, ROOT / 'config/build_profiles/sample_build_profile.toml')
        original = panel.path.read_text(encoding='utf-8')
        assert panel.text() == original
        hub_paths = [path for path in panel.fields if path[:3] == ('gasoline_demand', 'proxies', 'additional_hubs')]
        province_codes = list(panel.category_controls[('study_area', 'provinces')])
        assert [path[-1] for path in hub_paths] == province_codes == sorted(province_codes)
        for path in hub_paths:
            entry = panel.path_controls[path][0][0]
            label = entry.master.grid_slaves(row=entry.grid_info()['row'], column=0)[0]
            assert label.cget('text') == path[-1]
        assert not any(path[:2] == ('roads', 'classes') for path in panel.fields)
        assert ('road_connectivity', 'road_layer') not in panel.fields
        assert ('schema', 'road_connection_method') not in panel.fields
        panel.category_controls[('roads', 'networks')]['backbone'].invoke()
        result = tomllib.loads(panel.text())
        assert result['roads']['networks'] == ['backbone']
        assert result['road_connectivity']['road_layer'] == 'backbone'
        assert result['roads']['classes'] == tomllib.loads(original)['roads']['classes']
        panel.category_controls[('road_connectivity', 'methods')]['weak'].invoke()
        result = tomllib.loads(panel.text())
        assert result['road_connectivity']['methods'] == ['weak']
        assert result['schema']['road_connection_method'] == 'weak'
        panel.category_controls[('roads', 'networks')]['primary_freight'].invoke()
        assert tomllib.loads(panel.text())['roads']['networks'] == ['primary_freight']
        panel.category_controls[('study_area', 'provinces')]['YT'].invoke()
        assert 'YT' in tomllib.loads(panel.text())['study_area']['provinces']
        panel.category_controls[('hydrography', 'lines', 'classes')]['watercourse'].invoke()
        assert tomllib.loads(panel.text())['hydrography']['lines']['classes'] == ['watercourse']
        panel.fields[('basemaps', 'projected', 'resolutions_km')][0].set('25, 50, 75')
        assert tomllib.loads(panel.text())['basemaps']['projected']['resolutions_km'] == [25, 50, 75]
        for key in ('minimum_population', 'candidate_minimum_population'):
            choices = panel.category_controls[('gasoline_demand', 'proxies', key)]
            assert list(choices) == ['1000', '30000', '100000']
            choices['30000'].invoke()
            assert tomllib.loads(panel.text())['gasoline_demand']['proxies'][key] == 30000
            assert sum(button._silver_variable.get() for button in choices.values()) == 1
            choices['30000'].invoke()
            assert choices['30000']._silver_variable.get()
        panel.set_busy(True)
        assert all(str(button.cget('state')) == 'disabled'
                   for choices in panel.category_controls.values() for button in choices.values())
    finally:
        window.destroy()


def test_storage_controls_follow_eligibility_and_preserve_inactive_quantitative_settings():
    window = tk.Tk()
    window.withdraw()
    try:
        panel = SilverPanel(window, ROOT, ROOT / 'config/build_profiles/sample_build_profile.toml')
        mapping = panel.category_controls[('storage', 'quantitative', 'capacity_mapping')]
        assert all(button.instate(['disabled']) for button in mapping.values())
        panel.category_controls[('storage', 'eligibility')]['quantitative'].invoke()
        assert all(not button.instate(['disabled']) for button in mapping.values())
        mapping['shared'].invoke()
        panel.validate_text(panel.text())
        panel.category_controls[('storage', 'eligibility')]['qualitative'].invoke()
        assert all(button.instate(['disabled']) for button in mapping.values())
        result = tomllib.loads(panel.text())
        assert result['storage']['quantitative']['capacity_mapping'] == 'shared'
        panel.validate_text(panel.text())
        panel.category_controls[('storage', 'eligibility')]['quantitative'].invoke()
        assert tomllib.loads(panel.text())['storage']['quantitative']['capacity_mapping'] == 'shared'
    finally:
        window.destroy()


def test_default_uses_existing_t4_gcc_profile_unchanged():
    window = tk.Tk()
    window.withdraw()
    try:
        panel = SilverPanel(window, ROOT)
        assert panel.path == ROOT / 'config/build_profiles/canada_renewables_t4_gcc.toml'
        assert panel.text() == panel.path.read_text(encoding='utf-8')
        profile = tomllib.loads(panel.text())
        assert len(profile['study_area']['provinces']) == 13
        assert profile['basemaps']['projected']['resolutions_km'] == [25]
        assert profile['renewables']['config'] == 'config/renewables/canada_t4_gcc.toml'
        assert not panel.busy
    finally:
        window.destroy()
