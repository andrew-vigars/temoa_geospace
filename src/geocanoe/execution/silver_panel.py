"""Independent, section-oriented Silver profile editor and build panel."""
from __future__ import annotations

import os
from copy import deepcopy
import csv
import io
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import tomlkit

from geocanoe.config import load_geospatial_build_config


DEFAULT_UI_BUILD_PROFILE = 'config/build_profiles/sample_build_profile.toml'


SECTION_NOTES = {
    'profile': 'The profile ID identifies this build. A new filename alone does not isolate processed datasets.',
    'study_area': 'Select the study jurisdictions. The label is used in processed filenames; keep it consistent with the intended study area.',
    'basemaps': 'Resolution changes the spatial representation and model size. Finer grids can also change represented road connectivity.',
    'basemaps.projected': 'Projected grid sizes are in kilometres (EPSG:3347). Leave empty to skip this grid family.',
    'basemaps.geographic': 'Geographic grid sizes are in decimal degrees (EPSG:4326), not kilometres. Leave empty to skip this grid family.',
    'adjacency': 'Rook adjacency links cells along shared sides, not diagonally. This defines the candidate regional graph.',
    'roads': 'Networks are nested: backbone is the narrowest class set; primary freight adds arterial roads; freight access includes local roads. Road classes are retained from the profile and are not edited here.',
    'road_connectivity': 'Strong connectivity is the standard workflow: a shared road segment must intersect both neighbouring cells. This remains a heuristic until road-network preprocessing and routed shortest paths are implemented. Plots are diagnostic outputs.',
    'schema': 'The road network and connectivity method follow the selections above. Boundary buffer is in km; maximum snap distance is the selected grid resolution multiplied by the snap-distance factor.',
    'storage': 'Eligibility selects storage evidence, not a minimum-storage policy. All mapped and qualitative apply no geological capacity cap. Quantitative alone offers P50 capacity limits; disabled quantitative choices are remembered but inactive.',
    'storage.quantitative': 'P50 is the central storage-capacity estimate. Equal weighted imposes per-cell shares; shared imposes one system-wide pool. These assumptions affect the spatial flexibility of storage.',
    'gasoline_demand': 'Sales year fixes the demand evidence year. Density converts litres of gasoline to tonnes; it is not a demand-growth assumption.',
    'gasoline_demand.proxies': 'Bronze uses 2021 StatCan population-centre boundaries, not a municipal city list: centres have at least 1,000 people and 400 people/km². Official classes are small (1,000–29,999), medium (30,000–99,999), and large (100,000+). Selection uses PCCLASS, not an exact population count, so only cutoffs 1,000 / 30,000 / 100,000 are supported. DA-derived population estimates rank candidates; they do not redefine these classes.\nPopulation threshold selects qualifying classes. Supply anchors plus fill applies the candidate cutoff only to extra fill hubs; registered anchors and largest-centre fallbacks can fall below it. If no centre qualifies in a jurisdiction, its largest centre is retained.\nSelected centres are demand proxies at polygon representative points, not verified terminals. Rural areas do not become candidate hubs but their DA demand is assigned to the nearest selected hub within the same jurisdiction by straight-line distance. Provincial sales are allocated by assigned population, then concentrated in the hubs’ grid cells; multiple hubs can map to one cell. More hubs therefore need not mean more demand cells.',
    'gasoline_demand.proxies.additional_hubs': 'Counts are additional hubs beyond registered anchors or the jurisdiction fallback, not total hub counts. Zero adds no extra hubs. Use with supply anchors plus fill; nonzero counts are invalid with population threshold.',
    'pipeline_impedance': 'Select a versioned penalty profile from the registry. These are routing-cost assumptions, not physical exclusions; enabling evidence does not validate its penalty weights.',
    'hydrography': 'Polygon and line evidence are separate geometry families. Permanency filters describe source classifications; unknown does not mean dry or absent.',
    'hydrography.polygons': 'Polygons represent waterbody footprints. Selected classes and area thresholds determine which waterbodies enter the area-based impedance evidence. The sample retains lakes of at least 50 km².',
    'hydrography.polygons.minimum_source_area_km2': 'Area thresholds are km² of the complete source feature before clipping to the study area, not the remaining clipped area.',
    'hydrography.lines': 'Keep lines disabled for the current workflow. River and watercourse centre-lines have no represented width; the polygon-based distance penalty cannot turn a line crossing into a meaningful cost. A formal crossing/width heuristic and calibrated penalty are needed first. Enabling line preprocessing does not include lines in the current NHN impedance calculation.',
    'hydrography.lines.minimum_source_length_km': 'Length thresholds are km of complete source lines before clipping. They filter evidence only; they do not define water width or crossing cost.',
    'renewables': 'Enable only with the required renewable evidence and dependencies. Renewable profiles require projected grids of at least 25 km; the referenced config contains resource-specific settings.',
}


def parse_profile(text):
    document = tomlkit.parse(text)
    if not isinstance(document.get('profile'), dict):
        raise ValueError('Select a Silver build profile containing [profile].')
    return document


def list_text(values):
    stream = io.StringIO()
    csv.writer(stream, lineterminator='').writerow(values)
    return stream.getvalue()


def category_label(path, option):
    if path in {('gasoline_demand', 'proxies', 'minimum_population'),
                ('gasoline_demand', 'proxies', 'candidate_minimum_population')}:
        return {'1000': 'Small and larger (1,000+)',
                '30000': 'Medium and larger (30,000+)',
                '100000': 'Large (100,000+)'}[str(option)]
    return str(option).replace('_', ' ')


def field_value(path, original, value):
    """Convert ordinary form values without requiring TOML in the UI."""
    if isinstance(original, bool):
        return bool(value)
    if isinstance(original, str):
        return value
    if isinstance(original, list):
        items = next(csv.reader([value], skipinitialspace=True)) if value.strip() else []
        items = [item.strip() for item in items]
        numeric = (original and isinstance(original[0], (int, float))) or path[-1] in {'resolutions', 'resolutions_km'}
        return [float(item) for item in items] if numeric else items
    if isinstance(original, int):
        return int(value)
    if isinstance(original, float):
        return float(value)
    raise ValueError(f'Unsupported field type for {".".join(path)}')


class SilverPanel(ttk.Frame):
    """Owns Silver state; never creates Gold scenarios or TEMOA runs."""

    def __init__(self, parent, root, config=None, activity_changed=None):
        super().__init__(parent, padding=12)
        self.root = Path(root)
        self.activity_changed = activity_changed or (lambda: None)
        self.busy = False
        self.external_busy = False
        self.messages = queue.Queue()
        self.fields = {}
        self.controls = []
        self.actions = []
        self.path = None
        self.source = tk.StringVar()
        self.status = tk.StringVar(value='Load or edit a build profile. Silver can be built on its own.')
        ttk.Label(self, text='Silver build profiles', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(self, text='Configure Silver → Save profile → Validate inputs or Build Silver. Gold is optional.').pack(anchor='w')
        ttk.Label(self, textvariable=self.source).pack(anchor='w', pady=6)
        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=6)
        for label, command in [('Load profile…', self.load), ('Save profile as…', self.save),
                               ('Validate inputs', lambda: self.start(True)), ('Build Silver', lambda: self.start(False))]:
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side='left', padx=(0, 8))
            self.actions.append(button)
        ttk.Label(self, text='Select categories below. Enter numeric lists as comma-separated values; projected resolutions are km, geographic resolutions are degrees.').pack(anchor='w')
        viewport = ttk.Frame(self)
        viewport.pack(fill='both', expand=True, pady=6)
        self.canvas = tk.Canvas(viewport, highlightthickness=0, height=280)
        scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(fill='both', expand=True)
        self.form = ttk.Frame(self.canvas)
        item = self.canvas.create_window((0, 0), window=self.form, anchor='nw')
        self.form.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(item, width=event.width))
        self.canvas.bind('<MouseWheel>', self.wheel)
        ttk.Label(self, textvariable=self.status, wraplength=1000).pack(anchor='w')
        self.log = tk.Text(self, height=7, state='disabled', wrap='word')
        self.log.pack(fill='x', pady=(6, 0))
        self.load_path(config or self.root / DEFAULT_UI_BUILD_PROFILE)
        self.after(150, self.poll)

    def load_path(self, path):
        path = Path(path)
        text = path.read_text(encoding='utf-8')
        document = parse_profile(text)
        load_geospatial_build_config(path)
        for child in self.form.winfo_children():
            child.destroy()
        self.document = document
        self.fields, self.controls = {}, []
        self.category_controls = {}
        self.path_controls = {}
        values = document.unwrap()
        proxies = values.setdefault('gasoline_demand', {}).setdefault('proxies', {})
        hubs = proxies.setdefault('additional_hubs', {})
        for province in ('NL', 'PE', 'NS', 'NB', 'QC', 'ON', 'MB', 'SK', 'AB', 'BC', 'YT', 'NT', 'NU'):
            hubs.setdefault(province, 0)
        self.add_fields(values)
        for variable, _ in self.fields.values():
            variable.trace_add('write', lambda *_: self.set_busy(self.external_busy))
        self.set_busy(self.external_busy)
        self.canvas.yview_moveto(0)
        self.path = path.resolve()
        self.source.set(str(self.path))
        self.status.set('Profile loaded. Save edits before validation or building.')

    def load(self):
        path = filedialog.askopenfilename(parent=self, title='Load Silver build profile',
                                          initialdir=self.root / 'config/build_profiles', filetypes=[('TOML', '*.toml')])
        if path:
            try:
                self.load_path(path)
            except Exception as error:
                messagebox.showerror('Cannot load Silver profile', str(error), parent=self)

    def text(self):
        document = deepcopy(self.document)
        for path, (variable, original) in self.fields.items():
            try:
                value = field_value(path, original, variable.get())
            except (ValueError, csv.Error) as error:
                raise ValueError(f'[{".".join(path[:-1])}] {path[-1]}: {error}') from error
            if value != original:
                parent = document
                for part in path[:-1]:
                    if part not in parent:
                        parent[part] = tomlkit.table()
                    parent = parent[part]
                parent[path[-1]] = value
        networks = document['roads']['networks']
        if len(networks) == 1:
            document['road_connectivity']['road_layer'] = networks[0]
        methods = document['road_connectivity']['methods']
        if methods and document['schema']['road_connection_method'] not in methods:
            document['schema']['road_connection_method'] = 'strong' if 'strong' in methods else methods[0]
        storage = document['storage']
        if storage['eligibility'] != 'quantitative':
            # Inactive quantitative assumptions remain available for later reuse.
            for options in (storage, storage.get(storage['eligibility'], {})):
                if 'capacity_mapping' in options:
                    options['capacity_mapping'] = 'unlimited'
                if 'use_capacity_bound' in options:
                    options['use_capacity_bound'] = False
        return tomlkit.dumps(document)

    def wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')
        return 'break'

    def add_fields(self, values, prefix=()):
        if prefix == ('roads', 'classes'):
            return
        group = None
        items = sorted(values.items()) if prefix == ('gasoline_demand', 'proxies', 'additional_hubs') else values.items()
        for key, value in items:
            path = (*prefix, key)
            if path in {('basemaps', 'keep_method'),
                        ('road_connectivity', 'road_layer'),
                        ('road_connectivity', 'methods'),
                        ('schema', 'road_connection_method'),
                        ('schema', 'interactive_basemap_selection'),
                        ('schema', 'basemap_stem')}:
                continue
            if isinstance(value, dict):
                self.add_fields(value, path)
                continue
            if group is None:
                group = ttk.LabelFrame(self.form, text=f'[{".".join(prefix)}]', padding=12)
                group.columnconfigure(1, weight=1)
                group.pack(fill='x', padx=(0, 8), pady=(0, 10))
                group.bind('<MouseWheel>', self.wheel)
                note = SECTION_NOTES.get('.'.join(prefix))
                if note:
                    caption = ttk.Label(group, text=note, wraplength=850, justify='left')
                    caption.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
                    caption.bind('<MouseWheel>', self.wheel)
            row = group.grid_size()[1]
            title = key.upper() if prefix == ('gasoline_demand', 'proxies', 'additional_hubs') else key.replace('_', ' ').capitalize()
            label = ttk.Label(group, text=title)
            label.grid(row=row, column=0, sticky='w', padx=(0, 18), pady=3)
            label.bind('<MouseWheel>', self.wheel)
            options = self.category_choices('.'.join(path)) or self.choices('.'.join(path))
            if options:
                self.add_categories(group, row, path, value, options)
                continue
            if isinstance(value, bool):
                variable = tk.BooleanVar(value=value)
                widget = ttk.Checkbutton(group, variable=variable)
            else:
                variable = tk.StringVar(value=list_text(value) if isinstance(value, list) else str(value))
                options = self.choices('.'.join(path))
                widget = (ttk.Combobox(group, textvariable=variable, values=options, state='readonly')
                          if options else ttk.Entry(group, textvariable=variable))
            state = str(widget.cget('state'))
            widget.grid(row=row, column=1, sticky='ew', pady=3)
            widget.bind('<MouseWheel>', self.wheel)
            self.fields[path] = (variable, value)
            self.controls.append((widget, state))
            self.path_controls[path] = [(widget, state)]
            variable.trace_add('write', lambda *_: self.status.set('Settings changed. Save the profile before validation or building.'))

    def add_categories(self, group, row, path, original, options):
        multiple = isinstance(original, list) and path not in {('roads', 'networks'), ('road_connectivity', 'methods')}
        selected = original if isinstance(original, list) else [str(original)]
        options = list(dict.fromkeys([*options, *selected]))
        variable = tk.StringVar(value=list_text(original) if isinstance(original, list) else str(original))
        container = ttk.Frame(group)
        container.grid(row=row, column=1, sticky='ew', pady=3)
        switches = {option: tk.BooleanVar(value=option in selected) for option in options}
        self.category_controls[path] = {}
        self.path_controls[path] = []
        def choose(option):
            if not multiple:
                for name, switch in switches.items():
                    switch.set(name == option)
            chosen = [name for name, switch in switches.items() if switch.get()]
            # Retain source ordering when selection is unchanged.
            if set(chosen) == set(selected):
                chosen = selected
            variable.set(list_text(chosen) if isinstance(original, list) else chosen[0])
        for index, (option, switch) in enumerate(switches.items()):
            button = ttk.Checkbutton(container, text=category_label(path, option), variable=switch,
                                     command=lambda name=option: choose(name))
            button.grid(row=index // 3, column=index % 3, sticky='w', padx=(0, 12), pady=2)
            button.bind('<MouseWheel>', self.wheel)
            button._silver_variable = switch
            self.category_controls[path][option] = button
            self.controls.append((button, 'normal'))
            self.path_controls[path].append((button, 'normal'))
        if path == ('roads', 'networks'):
            ttk.Label(container, text='Choose one nested road network. Selecting a network also sets the connectivity road layer.').grid(
                row=(len(options) + 2) // 3, column=0, columnspan=3, sticky='w')
            if len(selected) > 1:
                ttk.Label(container, text='Loaded profile contains multiple networks; choose one to replace this selection.').grid(
                    row=(len(options) + 2) // 3 + 1, column=0, columnspan=3, sticky='w')
        self.fields[path] = (variable, original)
        if path[-1] == 'capacity_mapping':
            ttk.Label(container, text='Unlimited: no geological capacity cap. Equal weighted: split each assessment’s P50 equally among mapped cells.\nShared: one system-wide pool of assessment P50 totals, available across eligible cells.',
                      wraplength=760).grid(row=(len(options) + 2) // 3, column=0, columnspan=3, sticky='w')
        variable.trace_add('write', lambda *_: self.status.set('Settings changed. Save the profile before validation or building.'))

    @staticmethod
    def category_choices(path):
        from geocanoe.config import project_config as config
        if path in {'gasoline_demand.proxies.minimum_population',
                    'gasoline_demand.proxies.candidate_minimum_population'}:
            return [str(value) for value in sorted(config.SUPPORTED_POPULATION_CENTRE_THRESHOLDS)]
        names = {'study_area.provinces': 'ALL_PROVINCE_CODES',
                 'roads.networks': 'SUPPORTED_ROAD_NETWORKS',
                 'road_connectivity.road_layer': 'SUPPORTED_ROAD_LAYERS',
                 'hydrography.permanency': 'SUPPORTED_HYDROGRAPHY_PERMANENCY',
                 'hydrography.polygons.classes': 'SUPPORTED_HYDROGRAPHY_CLASSES',
                 'hydrography.lines.classes': 'SUPPORTED_HYDROGRAPHY_CLASSES'}
        sources = {'storage.all_mapped.sources': ['NATCARB', 'BC_STORAGE_ATLAS', 'ATLANTIC_COS'],
                   'storage.quantitative.sources': ['NATCARB', 'BC_STORAGE_ATLAS'],
                   'storage.qualitative.sources': ['ATLANTIC_COS']}
        if path in sources:
            return sources[path]
        if path.endswith('.capacity_mapping') and path.startswith('storage.'):
            return ['unlimited', 'equal_weighted', 'shared']
        return sorted(getattr(config, names[path])) if path in names else None

    @staticmethod
    def choices(path):
        from geocanoe.config import project_config as config
        names = {'adjacency.method': 'SUPPORTED_ADJACENCY_METHODS',
                 'schema.road_connection_method': 'SUPPORTED_ROAD_CONNECTIVITY_METHODS',
                 'storage.eligibility': 'SUPPORTED_STORAGE_ELIGIBILITY_MODES',
                 'gasoline_demand.proxies.strategy': 'SUPPORTED_GASOLINE_PROXY_STRATEGIES',
                 'gasoline_demand.proxies.empty_jurisdiction_policy': 'SUPPORTED_GASOLINE_EMPTY_JURISDICTION_POLICIES'}
        return sorted(getattr(config, names[path])) if path in names else None

    def validate_text(self, text):
        parse_profile(text)
        scratch = self.root / '.tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, prefix='silver_profile_') as directory:
            path = Path(directory) / 'profile.toml'
            path.write_text(text, encoding='utf-8')
            load_geospatial_build_config(path)

    def save(self):
        try:
            text = self.text()
            self.validate_text(text)
            path = filedialog.asksaveasfilename(parent=self, title='Save Silver build profile',
                initialdir=self.root / 'config/build_profiles', defaultextension='.toml', filetypes=[('TOML', '*.toml')])
            if not path:
                return False
            Path(path).write_text(text, encoding='utf-8')
            self.path = Path(path).resolve()
            self.source.set(str(self.path))
            self.status.set('Profile saved. Validate inputs or build Silver independently.')
            return True
        except Exception as error:
            messagebox.showerror('Cannot save Silver profile', str(error), parent=self)
            return False

    def set_busy(self, busy):
        self.external_busy = busy
        state = 'disabled' if busy or self.busy else 'normal'
        for widget in self.actions:
            widget.configure(state=state)
        for widget, normal in self.controls:
            widget.configure(state='disabled' if busy or self.busy else normal)
        eligibility = self.fields.get(('storage', 'eligibility'))
        if eligibility:
            mode = eligibility[0].get()
            for path, widgets in self.path_controls.items():
                inactive = path[0] == 'storage' and (
                    (len(path) > 2 and path[1] in {'quantitative', 'qualitative', 'all_mapped'} and path[1] != mode)
                    or (path[-1] in {'capacity_mapping', 'use_capacity_bound'} and mode != 'quantitative'))
                for widget, normal in widgets:
                    widget.configure(state='disabled' if busy or self.busy or inactive else normal)

    def start(self, validate_only):
        if self.busy or self.external_busy:
            return
        try:
            text = self.text()
            self.validate_text(text)
            if self.path.read_text(encoding='utf-8') != text and not self.save():
                return
            command = [sys.executable, '-m', 'geocanoe.execution.silver', '--config', str(self.path)]
            if validate_only:
                command.append('--validate-only')
            label = 'Silver input validation' if validate_only else 'Silver build'
            self.busy = True
            self.set_busy(self.external_busy)
            self.activity_changed()
            self.status.set(f'{label} running. Keep this window open.')
            def work():
                try:
                    with subprocess.Popen(command, cwd=self.root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding='utf-8', errors='replace',
                            env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1'}) as process:
                        for line in process.stdout:
                            self.messages.put(('log', line))
                        self.messages.put(('done', (label, process.wait())))
                except Exception as error:
                    self.messages.put(('log', f'{error}\n'))
                    self.messages.put(('done', (label, 1)))
            threading.Thread(target=work, daemon=True).start()
        except Exception as error:
            messagebox.showerror('Cannot start Silver', str(error), parent=self)

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'log':
                    self.log.configure(state='normal')
                    self.log.insert('end', value)
                    self.log.see('end')
                    self.log.configure(state='disabled')
                else:
                    label, code = value
                    self.busy = False
                    self.status.set(f'{label}: {"complete" if code == 0 else "failed; see progress log"}.')
                    self.set_busy(self.external_busy)
                    self.activity_changed()
        except queue.Empty:
            pass
        self.after(150, self.poll)


def main():
    import argparse
    from geocanoe.paths import find_project_root
    from geocanoe.execution.desktop_branding import set_taskbar_identity, set_window_icon
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    set_taskbar_identity()
    window = tk.Tk()
    set_window_icon(window, find_project_root())
    window.title('GeoCANOE · Silver construction')
    window.geometry('1120x820')
    panel = SilverPanel(window, find_project_root(), args.config)
    panel.pack(fill='both', expand=True)
    def close():
        if panel.busy:
            messagebox.showinfo('Operation in progress', 'Keep this window open until Silver finishes.')
        else:
            window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    window.mainloop()


if __name__ == '__main__':
    main()
