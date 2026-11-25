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
        assert ('road_connectivity', 'methods') not in panel.fields
        assert ('schema', 'road_connection_method') not in panel.fields
        assert ('schema', 'interactive_basemap_selection') not in panel.fields
        assert ('schema', 'basemap_stem') not in panel.fields
        assert ('basemaps', 'keep_method') not in panel.fields
        assert tomllib.loads(panel.text())['basemaps']['keep_method'] == 'centroid'
        panel.category_controls[('roads', 'networks')]['backbone'].invoke()
        result = tomllib.loads(panel.text())
        assert result['roads']['networks'] == ['backbone']
        assert result['road_connectivity']['road_layer'] == 'backbone'
        assert result['road_connectivity']['methods'] == ['strong']
        assert result['schema']['road_connection_method'] == 'strong'
        assert result['roads']['classes'] == tomllib.loads(original)['roads']['classes']
        assert ('road_connectivity', 'plot_outputs') in panel.fields
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


def test_default_uses_sample_build_profile_unchanged():
    window = tk.Tk()
    window.withdraw()
    try:
        panel = SilverPanel(window, ROOT)
        assert panel.path == ROOT / 'config/build_profiles/sample_build_profile.toml'
        assert panel.text() == panel.path.read_text(encoding='utf-8')
        assert not panel.busy
    finally:
        window.destroy()


def test_renewable_coverage_tracks_master_province_selection(tmp_path):
    window = tk.Tk()
    window.withdraw()
    try:
        panel = SilverPanel(window, ROOT)
        panel.fields[('renewables', 'enabled')][0].set(True)
        assert 'ON' in panel.renewable_scope.get()
        assert 'basemap study area' in panel.renewable_scope.get()
        panel.category_controls[('study_area', 'provinces')]['ON'].invoke()
        assert 'ON' not in panel.renewable_scope.get().split('\n')[0]
        panel.category_controls[('study_area', 'provinces')]['YT'].invoke()
        assert 'YT' in panel.renewable_scope.get()
        saved = tmp_path / 'renewables.toml'
        saved.write_text(panel.text(), encoding='utf-8')
        from geocanoe.config import load_geospatial_build_config
        from geocanoe.preprocessing.renewables import selected_provinces
        profile = load_geospatial_build_config(saved)
        assert selected_provinces(profile, {'province_subset': ['ON']}) == list(profile.study_area.provinces)
        assert 'ON' not in profile.study_area.provinces
        assert 'YT' in profile.study_area.provinces
        panel.fields[('renewables', 'enabled')][0].set(False)
        assert 'disabled' in panel.renewable_scope.get()
    finally:
        window.destroy()
