"""TEMOA settings form and prepared-Gold run selection for the desktop UI."""
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tomllib

import tomlkit

from geocanoe.execution.temoa_runset import (
    MAPPED_KEYS, create_runset, default_run_settings, prepared_inventory, validate_run_settings,
)


class TemoaPanel(ttk.Frame):
    def __init__(self, parent, root, on_change, on_run, get_batch_settings):
        super().__init__(parent, padding=12)
        self.root, self.on_change = root, on_change
        self.get_batch_settings = get_batch_settings
        self.bundle = self.runset = None
        self.cases, self.enabled, self.fields, self.controls = [], {}, {}, []
        self.field_widgets, self.field_labels, self.groups = {}, {}, {}
        self.busy = False
        ttk.Label(self, text='TEMOAv4 run configurations', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(self, text='Choose prepared Gold cases → Configure solver and run options → Generate configurations and run a batch.').pack(anchor='w', pady=(0, 10))
        self.summary = tk.StringVar(value='Prepare Gold on the Generate Gold tab, or open an existing prepared experiment.')
        ttk.Label(self, textvariable=self.summary, wraplength=1000).pack(anchor='w')
        header = ttk.Frame(self)
        header.pack(fill='x', pady=8)
        self.load_button = ttk.Button(header, text='Open prepared experiment...', command=self.choose_bundle)
        self.load_button.pack(side='left')
        self.load_config_button = ttk.Button(header, text='Load TEMOA settings...', command=self.load_config)
        self.load_config_button.pack(side='left', padx=8)
        ttk.Label(header, text='New run selection name').pack(side='left', padx=8)
        self.name = tk.StringVar(value=f'run_{datetime.now():%Y%m%d_%H%M%S}')
        entry = ttk.Entry(header, textvariable=self.name)
        entry.pack(side='left', fill='x', expand=True)
        self.controls.append((entry, 'normal'))
        self.name.trace_add('write', self.dirty)
        ttk.Label(self, text='Shared TEMOA settings · database paths and results labels are mapped from Gold automatically.').pack(anchor='w')
        viewport = ttk.Frame(self)
        viewport.pack(fill='both', expand=True, pady=6)
        canvas = tk.Canvas(viewport, highlightthickness=0, height=200)
        scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(fill='both', expand=True)
        form = ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=form, anchor='nw')
        form.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
        def wheel(event):
            if event.delta:
                canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')
            return 'break'
        canvas.bind('<MouseWheel>', wheel)
        defaults = default_run_settings()
        def group(title):
            frame = ttk.LabelFrame(form, text=title, padding=8)
            frame.columnconfigure(1, weight=1)
            frame.pack(fill='x', padx=(0, 8), pady=(0, 8))
            frame.bind('<MouseWheel>', wheel)
            return frame
        run = group('Run settings, validation and outputs')
        def fields(values, container, prefix=''):
            for key, value in values.items():
                path = f'{prefix}.{key}' if prefix else key
                if isinstance(value, dict) and path != 'solver.options':
                    child = group(f'[{path}]')
                    self.groups[path] = child
                    fields(value, child, path)
                    continue
                row = container.grid_size()[1]
                label = ttk.Label(container, text=key)
                label.grid(row=row, column=0, sticky='w', padx=(0, 16), pady=2)
                label.bind('<MouseWheel>', wheel)
                if type(value) is bool and path != 'morris.seed':
                    variable = tk.BooleanVar(value=value)
                    widget = ttk.Checkbutton(container, variable=variable)
                    kind, state = 'bool', 'normal'
                else:
                    kind = 'string' if isinstance(value, str) else 'literal'
                    if isinstance(value, dict):
                        literal = tomlkit.inline_table()
                        literal.update(value)
                        text = literal.as_string()
                    else:
                        text = value if kind == 'string' else tomlkit.item(value).as_string()
                    variable = tk.StringVar(value=text)
                    options = self.choices(path)
                    state = 'normal' if path == 'solver.name' or not options else 'readonly'
                    widget = (ttk.Combobox(container, textvariable=variable, values=options, state=state)
                              if options else ttk.Entry(container, textvariable=variable))
                widget.grid(row=row, column=1, sticky='ew', pady=2)
                widget.bind('<MouseWheel>', wheel)
                self.fields[path] = (variable, kind)
                self.field_widgets[path] = (widget, state)
                self.field_labels[path] = label
                self.controls.append((widget, state))
                variable.trace_add('write', self.dirty)
        fields(defaults, run)
        self.previous_solver = self.fields['solver.name'][0].get()
        self.fields['solver.name'][0].trace_add('write', self.solver_changed)
        ttk.Label(self, text='Gold → TEMOA mapping · double-click a row to enable or disable its run.').pack(anchor='w', pady=(4, 0))
        mapping = ttk.Frame(self)
        mapping.pack(fill='x', pady=4)
        mapping.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(mapping, columns=('enabled', 'gold', 'database', 'config'), show='headings', height=5)
        for key, title, width in [('enabled', 'Run', 55), ('gold', 'Gold scenario', 160),
                                  ('database', 'Gold database', 370), ('config', 'TEMOA config', 370)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=50)
        self.table.grid(row=0, column=0, sticky='ew')
        vertical = ttk.Scrollbar(mapping, orient='vertical', command=self.table.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(mapping, orient='horizontal', command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.bind('<Double-1>', self.toggle)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(4, 0))
        self.selection_buttons = []
        for text, command in [('Enable selected', lambda: self.select(True)), ('Disable selected', lambda: self.select(False)),
                              ('Enable all', lambda: self.select(True, all_rows=True)), ('Disable all', lambda: self.select(False, all_rows=True))]:
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side='left', padx=(0, 6))
            self.selection_buttons.append(button)
        self.generate_button = ttk.Button(actions, text='Generate TEMOA configs', command=self.generate)
        self.generate_button.pack(side='left', padx=6)
        self.run_button = ttk.Button(actions, text='Run batch', command=on_run)
        self.run_button.pack(side='right')
        self.log = tk.Text(self, height=3, wrap='word', state='disabled')
        self.log.pack(fill='x', pady=(8, 0))
        self.set_busy(False)

    @staticmethod
    def choices(path):
        if path == 'scenario_mode':
            from temoa.core.modes import TemoaMode
            return [mode.name.lower() for mode in TemoaMode]
        return {'solver.name': ['gurobi', 'appsi_highs', 'cbc', 'cplex', 'glpk'],
                'time_sequencing': ['consecutive_days', 'representative_periods', 'seasonal_timeslices', 'manual'],
                'reserve_margin': ['static', 'dynamic']}.get(path)

    def solver_changed(self, *_):
        name = self.fields['solver.name'][0].get()
        if name != self.previous_solver:
            self.previous_solver = name
            self.fields['solver.options'][0].set('{}')

    def field_enabled(self, path):
        mode = self.fields['scenario_mode'][0].get().lower()
        mode_sections = {'MGA': 'mga', 'myopic': 'myopic', 'morris': 'method_of_morris',
                         'SVMGA': 'svmga', 'monte_carlo': 'monte_carlo'}
        section = path.split('.')[0]
        if section in mode_sections and mode != mode_sections[section]:
            return False
        if path == 'myopic.evolution_script':
            return self.fields['myopic.evolving'][0].get()
        if path == 'plot_commodity_network':
            return self.fields['source_trace'][0].get()
        if path == 'save_duals':
            return self.fields['solver.name'][0].get().lower() != 'appsi_highs'
        return True

    def refresh_dependencies(self):
        for path, (widget, state) in self.field_widgets.items():
            active = not self.busy and self.field_enabled(path)
            widget.configure(state=state if active else 'disabled')
            self.field_labels[path].state(['!disabled'] if active else ['disabled'])
        for path, group in self.groups.items():
            active = not self.busy and any(self.field_enabled(key) for key in self.fields if key.startswith(path + '.'))
            group.state(['!disabled'] if active else ['disabled'])

    def settings(self):
        settings = {}
        for path, (variable, kind) in self.fields.items():
            if not self.field_enabled(path):
                # Keep stashed form values; only active choices affect this run.
                if path in {'plot_commodity_network', 'save_duals'}:
                    settings[path] = False
                continue
            value = variable.get()
            if kind == 'literal':
                try:
                    value = tomllib.loads('value = ' + value)['value']
                except (ValueError, tomllib.TOMLDecodeError) as error:
                    raise ValueError(f'{path}: enter a valid TOML value (lists use brackets, options use braces).') from error
            section = settings
            parts = path.split('.')
            for part in parts[:-1]:
                section = section.setdefault(part, {})
            section[parts[-1]] = value
        return validate_run_settings(settings)

    def load_config(self):
        path = filedialog.askopenfilename(title='Load shared TEMOA settings', initialdir=self.root / 'config/temoav4', filetypes=[('TEMOA config', '*.toml')])
        if not path:
            return
        try:
            raw = tomllib.loads(Path(path).read_text(encoding='utf-8'))
            values = validate_run_settings({k: v for k, v in raw.items() if k not in MAPPED_KEYS})
            for field, (variable, kind) in self.fields.items():
                value = values
                for key in field.split('.'):
                    value = value[key]
                if kind == 'literal':
                    if isinstance(value, dict):
                        inline = tomlkit.inline_table()
                        inline.update(value)
                        value = inline.as_string()
                    else:
                        value = tomlkit.item(value).as_string()
                variable.set(value)
        except Exception as error:
            messagebox.showerror('Cannot load TEMOA settings', str(error))

    def choose_bundle(self):
        path = filedialog.askdirectory(title='Open prepared Gold experiment', initialdir=self.root / 'experiments')
        if path:
            try:
                self.load_bundle(Path(path))
            except Exception as error:
                messagebox.showerror('Cannot open experiment', str(error))

    def load_bundle(self, bundle):
        bundle = bundle.resolve()
        bundle.relative_to(self.root.resolve())
        cases = prepared_inventory(bundle)
        self.bundle, self.cases = bundle, cases
        self.enabled = {case['scenario_id']: True for case in cases}
        self.runset = None
        self.summary.set(f'Prepared experiment: {bundle.relative_to(self.root)} · {len(cases)} Gold databases')
        self.refresh_mapping()
        self.dirty()

    def refresh_mapping(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for case in self.cases:
            sid = case['scenario_id']
            config = (self.runset / 'solver' / f"{sid.replace('-', '_')}.toml").relative_to(self.root).as_posix() if self.runset else '(generate configs)'
            self.table.insert('', 'end', iid=sid, values=('Yes' if self.enabled[sid] else 'No', sid, case['gold_database'], config))
        for row in selected:
            if self.table.exists(row):
                self.table.selection_add(row)

    def select(self, enabled, all_rows=False):
        if self.busy:
            return
        for row in self.table.get_children() if all_rows else self.table.selection():
            self.enabled[row] = enabled
        self.dirty()

    def toggle(self, event):
        row = self.table.identify_row(event.y)
        if row and not self.busy:
            self.enabled[row] = not self.enabled[row]
            self.dirty()

    def dirty(self, *_):
        self.runset = None
        if hasattr(self, 'table'):
            if self.bundle:
                self.summary.set(f'Prepared experiment: {self.bundle.relative_to(self.root)} · unsaved TEMOA settings/selection')
            self.refresh_mapping()
            self.set_busy(self.busy)
        self.on_change()

    def generate(self):
        try:
            self.runset = create_runset(self.root, self.bundle, self.name.get(), self.settings(), self.enabled, self.get_batch_settings())
            self.refresh_mapping()
            self.summary.set(f'TEMOA configs saved: {self.runset.relative_to(self.root)} · {sum(self.enabled.values())}/{len(self.cases)} runs enabled')
            self.set_busy(False)
            self.on_change()
        except Exception as error:
            messagebox.showerror('Cannot generate TEMOA configs', str(error))

    def can_run(self):
        return self.runset is not None and any(self.enabled.values()) and not self.busy

    def set_busy(self, busy):
        self.busy = busy
        for widget, state in self.controls:
            widget.configure(state='disabled' if busy else state)
        for button in [self.load_button, self.load_config_button]:
            button.configure(state='disabled' if busy else 'normal')
        for button in [*self.selection_buttons, self.generate_button]:
            button.configure(state='normal' if self.bundle and not busy else 'disabled')
        self.run_button.configure(state='normal' if self.can_run() else 'disabled')
        self.refresh_dependencies()
