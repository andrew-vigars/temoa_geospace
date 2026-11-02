"""Launch the GeoCANOE Silver, Gold and TEMOA workflow configurator."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tomllib
import uuid

import tomlkit

from geocanoe.config import load_geospatial_build_config, load_model_config
from geocanoe.execution.gold_form import recipe_from_form, validate_model_structure
from geocanoe.paths import find_project_root
from geocanoe.execution.gold_prompts import PROMPT_PREFIX
from geocanoe.execution.temoa_panel import TemoaPanel
from geocanoe.execution.silver_panel import DEFAULT_UI_BUILD_PROFILE, SilverPanel
from geocanoe.execution.desktop_branding import set_taskbar_identity, set_window_icon
from prepare_national_gasoline_matrix import scenario_cases


class GoldConfigurator:
    def __init__(self, window, root, recipe):
        self.window, self.root = window, root
        self.template = self.read_template(recipe)
        self.variables, self.controls = {}, []
        self.field_widgets, self.field_labels, self.model_groups = {}, {}, {}
        self.messages = queue.Queue()
        self.bundle = None
        self.busy = False
        window.title('GeoCANOE Interface')
        set_window_icon(window, root)
        window.geometry('1120x820')
        window.minsize(960, 720)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='GeoCANOE Interface', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Independent modules: build Silver, prepare Gold scenarios, or configure TEMOA runs.').pack(anchor='w', pady=(4, 12))
        self.buttons = {}
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill='both', expand=True)
        self.silver_panel = SilverPanel(self.tabs, root, activity_changed=self.refresh_buttons)
        self.tabs.add(self.silver_panel, text='Silver construction')
        handoff = ttk.Button(self.silver_panel, text='Use saved profile in Gold →', command=self.use_silver_profile)
        handoff.pack(anchor='e', pady=(6, 0))
        self.silver_panel.actions.append(handoff)
        self.settings_page = ttk.Frame(self.tabs, padding=12)
        self.run_page = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.settings_page, text='Gold scenario settings')
        self.tabs.add(self.run_page, text='Generate Gold')

        ttk.Label(self.settings_page, text='Gold scenario configurations', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(self.settings_page, text='Define policy and model assumptions → Configure a single scenario or sensitivity matrix → Review cases.').pack(anchor='w', pady=(0, 10))
        experiment = ttk.LabelFrame(self.settings_page, text='Experiment', padding=12)
        experiment.columnconfigure(1, weight=1)
        experiment.pack(fill='x', pady=(0, 10))
        self.field(experiment, 'name', 'Experiment name')
        self.field(experiment, 'mode', 'Configure', ['Single scenario', 'Sensitivity matrix'])
        self.field(experiment, 'build_profile', 'Existing Silver / build profile', self.options('config/build_profiles/*.toml'))
        self.field(experiment, 'output', 'New experiment directory')
        self.check(experiment, 'auto_exclude_outliers', 'Auto-exclude all build outliers (site points and CO2 facilities)')
        load_button = ttk.Button(experiment, text='Load model / scenario / recipe...', command=self.load)
        load_button.grid(row=5, column=1, sticky='e', pady=(6, 0))
        self.buttons['Load recipe…'] = load_button

        navigation = ttk.Frame(self.settings_page)
        navigation.pack(side='bottom', fill='x', pady=(10, 0))
        ttk.Label(navigation, text='Matrix mode: comma-separated numeric values create sensitivity cases.').pack(side='left')
        self.buttons['Review & generate'] = ttk.Button(navigation, text='Review & generate →', command=self.review)
        self.buttons['Review & generate'].pack(side='right')
        viewport = ttk.Frame(self.settings_page)
        viewport.pack(fill='both', expand=True)
        self.settings_canvas = tk.Canvas(viewport, highlightthickness=0, height=270)
        vertical = ttk.Scrollbar(viewport, orient='vertical', command=self.settings_canvas.yview)
        vertical.pack(side='right', fill='y')
        self.settings_canvas.configure(yscrollcommand=vertical.set)
        self.settings_canvas.pack(fill='both', expand=True)
        self.model_settings = ttk.Frame(self.settings_canvas)
        content_window = self.settings_canvas.create_window((0, 0), window=self.model_settings, anchor='nw')
        self.model_settings.bind('<Configure>', lambda _: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>', lambda event: self.settings_canvas.itemconfigure(content_window, width=event.width))

        def section(title):
            group = ttk.LabelFrame(self.model_settings, text=title, padding=12)
            group.columnconfigure(1, weight=1)
            group.pack(fill='x', padx=(0, 8), pady=(0, 10))
            self.model_groups[title] = group
            return group
        p = section('[time]')
        self.field(p, 'start', 'Start year')
        self.field(p, 'end', 'End year (terminal boundary)')
        p = section('[synthetic_gasoline]')
        self.field(p, 'shares', 'Minimum national synthetic share (%)')
        ttk.Label(p, text='Conventional gasoline evidence is inherited from the model configuration.').grid(row=1, column=0, columnspan=2, sticky='w', pady=4)
        p = section('[finance]')
        self.field(p, 'global_discount_rate', 'Global discount rate (%)')
        self.field(p, 'default_loan_rate', 'Default loan rate (%)')
        p = section('[emissions]')
        self.field(p, 'projection', 'Projection method', ['constant'])
        self.field(p, 'emissions_policy', 'Emissions policy', ['none', 'emissions_price', 'none + emissions_price'])
        self.field(p, 'prices', 'Carbon price (CAD2020/t CO2e)')
        self.field(p, 'fallback', 'Conventional gasoline combustion factor (t CO2/t gasoline)')
        p = section('[storage]')
        self.field(p, 'storage_requirement', 'Storage requirement', [
            'none', 'minimum_point_source_fraction', 'minimum_cumulative_activity',
            'none + minimum_point_source_fraction', 'none + minimum_cumulative_activity',
            'minimum_point_source_fraction + minimum_cumulative_activity',
            'none + minimum_point_source_fraction + minimum_cumulative_activity'])
        self.field(p, 'storage', 'Point-source storage fraction (%)')
        self.field(p, 'cumulative', 'Minimum cumulative storage (t CO2e)')
        p = section('[basemap]')
        self.field(p, 'grid_type', 'Grid family', ['projected', 'geographic'])
        self.field(p, 'resolution', 'Resolution (km projected / degrees geographic)')
        p = section('[transport_modes]')
        self.check(p, 'roads', 'Road transport enabled')
        self.check(p, 'pipelines', 'Pipeline transport enabled')
        self.field(p, 'disabled', 'Disabled transport technologies (comma-separated)')
        p = section('[pipeline_costs]')
        self.field(p, 'impedance', 'Pipeline impedance scope', ['none', 'eos_capex_only', 'all_km_dependent'])
        for tech in ('co2', 'gsl', 'h2', 'metoh'):
            self.field(p, f'{tech}_pipeline_eos_stack_count', f'{tech.upper()} pipeline EOS stacks')
        def bind_scroll(widget):
            widget.bind('<MouseWheel>', self.scroll_settings)
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(self.model_settings)
        self.settings_canvas.bind('<MouseWheel>', self.scroll_settings)

        ttk.Label(self.run_page, text='Gold database preparation', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(self.run_page, text='Review scenario cases → Save an experiment plan → Build Gold databases from existing Silver.').pack(anchor='w', pady=(0, 10))
        p = ttk.LabelFrame(self.run_page, text='Batch execution settings', padding=12)
        p.pack(fill='x')
        for key, label in [('continue_on_failure', 'Continue after a failed run'),
                           ('skip_completed', 'Skip completed runs'), ('retry_failed', 'Retry failed runs')]:
            self.check(p, key, label)
        ttk.Label(self.run_page, text='Generate plan saves the scenarios and reproducibility records. Prepare Gold builds the databases.\nAfter Gold preparation, use TEMOA configurations to select cases and generate a runnable batch.').pack(anchor='w', pady=10)
        actions = ttk.Frame(self.run_page)
        actions.pack(fill='x', pady=(0, 10))
        for label, command in [('Edit settings', lambda: self.tabs.select(self.settings_page)),
                               ('Preview cases', self.preview), ('Generate plan', self.generate),
                               ('Prepare Gold', self.prepare), ('Configure TEMOA →', lambda: self.tabs.select(self.temoa_panel))]:
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side='left', padx=(0, 8))
            self.buttons[label] = button
        table_frame = ttk.Frame(self.run_page)
        table_frame.pack(fill='both', expand=True)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ('scenario', 'share', 'policy', 'price', 'requirement', 'storage', 'cumulative')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', height=8)
        for key, title in zip(columns, ('Scenario', 'Synthetic minimum', 'Emissions policy', 'CAD2020/t', 'Storage requirement', 'Storage fraction', 'Cumulative t')):
            self.table.heading(key, text=title)
            self.table.column(key, width=140, minwidth=60)
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(xscrollcommand=horizontal.set)
        horizontal.grid(row=1, column=0, sticky='ew')
        ttk.Label(self.run_page, text='Progress').pack(anchor='w', pady=(10, 0))
        self.log = tk.Text(self.run_page, height=6, wrap='word', state='disabled')
        self.log.pack(fill='x', pady=(4, 0))
        self.status = tk.StringVar(value='Configure your scenario, then choose Review & generate.')
        ttk.Label(frame, textvariable=self.status, wraplength=1050).pack(anchor='w', pady=(10, 0))
        self.temoa_panel = TemoaPanel(self.tabs, root, self.refresh_buttons, self.run_batch, self.batch_settings)
        self.tabs.add(self.temoa_panel, text='TEMOA configurations')
        self.buttons['Run batch'] = self.temoa_panel.run_button
        self.populate()
        for key, variable in self.variables.items():
            variable.trace_add('write', self.batch_edited if key in {'continue_on_failure', 'skip_completed', 'retry_failed'} else self.edited)
        self.refresh_buttons()
        self.tabs.select(self.silver_panel)
        window.after(150, self.poll)
        window.protocol('WM_DELETE_WINDOW', self.close)

    def scroll_settings(self, event):
        if event.delta:
            amount = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
            self.settings_canvas.yview_scroll(amount, 'units')
        return 'break'

    def use_silver_profile(self):
        """Explicitly share a saved profile; never start either builder."""
        panel = self.silver_panel
        try:
            if panel.text() != panel.path.read_text(encoding='utf-8') and not panel.save():
                return
            path = panel.path.relative_to(self.root).as_posix()
            widget, _ = self.field_widgets['build_profile']
            choices = list(widget.cget('values'))
            if path not in choices:
                widget.configure(values=[*choices, path])
            self.variables['build_profile'].set(path)
            self.tabs.select(self.settings_page)
            self.status.set('Silver profile selected for Gold. Build Silver first if its required artifacts do not yet exist.')
        except Exception as error:
            messagebox.showerror('Cannot use Silver profile in Gold',
                                 f'Save the profile inside this repository before using it in Gold.\n{error}')

    def review(self):
        if self.preview():
            self.tabs.select(self.run_page)

    def options(self, glob):
        return [p.relative_to(self.root).as_posix() for p in sorted(self.root.glob(glob))]

    def field(self, parent, key, label, options=None):
        row = parent.grid_size()[1]
        variable = tk.StringVar()
        self.variables[key] = variable
        caption = ttk.Label(parent, text=label)
        caption.grid(row=row, column=0, sticky='w', padx=(0, 18), pady=3)
        widget = ttk.Combobox(parent, textvariable=variable, values=options, state='readonly') if options else ttk.Entry(parent, textvariable=variable)
        widget.grid(row=row, column=1, sticky='ew', pady=3)
        self.controls.append((widget, 'readonly' if options else 'normal'))
        self.field_widgets[key] = (widget, 'readonly' if options else 'normal')
        self.field_labels[key] = caption

    def check(self, parent, key, label):
        variable = tk.BooleanVar()
        self.variables[key] = variable
        widget = ttk.Checkbutton(parent, text=label, variable=variable)
        widget.grid(row=parent.grid_size()[1], column=0, columnspan=2, sticky='w', pady=3)
        self.controls.append((widget, 'normal'))
        self.field_widgets[key] = (widget, 'normal')

    def read_template(self, path):
        raw = tomllib.loads(path.read_text(encoding='utf-8'))
        defaults = tomllib.loads((self.root / 'registry/model.toml').read_text(encoding='utf-8'))
        if 'model' in raw:
            validate_model_structure(raw['model'], defaults)
            for required in ('matrix', 'batch'):
                if not isinstance(raw.get(required), dict):
                    raise ValueError(f'Experiment recipe requires a [{required}] table.')
            return raw
        if 'matrix' in raw:
            raise ValueError('Load model.toml or a model-based experiment recipe. Legacy gasoline recipes remain supported by the CLI.')
        validate_model_structure(raw, defaults, allow_scenario=True)
        # A model file or a scenario overlay can seed the same general form.
        def merge(base, updates):
            for key, value in updates.items():
                if key == 'scenario':
                    continue
                if isinstance(value, dict) and key in base:
                    merge(base[key], value)
                else:
                    base[key] = value
        merge(defaults, raw)
        profiles = self.options('config/build_profiles/*.toml')
        preferred = DEFAULT_UI_BUILD_PROFILE
        return {'matrix': {'name': raw.get('scenario', {}).get('id', 'gold_experiment'),
                           'build_profile': preferred if preferred in profiles else profiles[0],
                           'interaction': 'prompt'},
                'model': defaults, 'axes': {},
                'batch': {'continue_on_failure': True, 'skip_completed': True, 'retry_failed': False}}

    def populate(self):
        m, model, b = self.template['matrix'], self.template['model'], self.template['batch']
        axes = self.template.get('axes', {})
        def numeric(path, scale=1):
            value = model
            for part in path.split('.'):
                value = value[part]
            return ', '.join(f'{v * scale:g}' for v in axes.get(path, [value]))
        def choice(path):
            section, key = path.split('.')
            return ' + '.join(axes.get(path, [model[section][key]]))
        values = dict(mode='Sensitivity matrix' if axes else 'Single scenario', name=m['name'],
                      auto_exclude_outliers=m.get('auto_exclude_outliers', False),
                      build_profile=m['build_profile'],
                      output=f"experiments/trial_{datetime.now():%Y%m%d_%H%M%S}",
                      shares=numeric('synthetic_gasoline.minimum_national_share', 100),
                      emissions_policy=choice('emissions.policy'),
                      prices=numeric('emissions.price_per_tonne'),
                      storage_requirement=choice('storage.requirement'),
                      storage=numeric('storage.point_source_fraction', 100),
                      cumulative=numeric('storage.minimum_cumulative_activity'),
                      start=model['time']['start_year'], end=model['time']['end_year'],
                      projection=model['emissions']['projection_method'],
                      grid_type=model['basemap']['grid_type'], resolution=numeric('basemap.resolution'),
                      impedance=model['pipeline_costs']['impedance_scope'],
                      roads=model['transport_modes']['roads_enabled'], pipelines=model['transport_modes']['pipelines_enabled'],
                      disabled=', '.join(model['transport_modes']['disabled_technologies']),
                      fallback=numeric('emissions.fallback_gasoline_emission_factor'))
        for key in ('global_discount_rate', 'default_loan_rate'):
            values[key] = numeric(f'finance.{key}', 100)
        for tech in ('co2', 'gsl', 'h2', 'metoh'):
            values[f'{tech}_pipeline_eos_stack_count'] = numeric(f'pipeline_costs.eos_stack_counts.{tech.upper()}_PIPE')
        values.update({key: b.get(key, False) for key in ('continue_on_failure', 'skip_completed', 'retry_failed')})
        for key, value in values.items():
            self.variables[key].set(value)

    def edited(self, *_):
        self.bundle = None
        self.table.delete(*self.table.get_children())
        self.status.set('Settings changed. Review the cases, then generate a new plan.')
        self.refresh_buttons()

    def batch_settings(self):
        return {key: self.variables[key].get() for key in ('continue_on_failure', 'skip_completed', 'retry_failed')}

    def batch_edited(self, *_):
        self.temoa_panel.dirty()

    def refresh_buttons(self):
        busy = self.busy or self.silver_panel.busy
        self.silver_panel.set_busy(self.busy)
        for label, button in self.buttons.items():
            enabled = not busy
            if label == 'Prepare Gold':
                enabled &= self.bundle is not None and not (self.bundle / 'preparation.json').exists()
            if label == 'Run batch':
                enabled &= self.temoa_panel.can_run()
            button.configure(state='normal' if enabled else 'disabled')
        if hasattr(self, 'temoa_panel'):
            self.temoa_panel.set_busy(busy)
        self.refresh_field_states()

    def refresh_field_states(self):
        busy = self.busy or self.silver_panel.busy
        priced = 'emissions_price' in self.variables['emissions_policy'].get()
        pipelines = self.variables['pipelines'].get()
        active = {'prices': priced, 'fallback': priced,
                  'storage': 'minimum_point_source_fraction' in self.variables['storage_requirement'].get(),
                  'cumulative': 'minimum_cumulative_activity' in self.variables['storage_requirement'].get(),
                  'impedance': pipelines}
        active.update({f'{tech}_pipeline_eos_stack_count': pipelines for tech in ('co2', 'gsl', 'h2', 'metoh')})
        for key, (widget, state) in self.field_widgets.items():
            enabled = not busy and active.get(key, True)
            widget.configure(state=state if enabled else 'disabled')
            if key in self.field_labels:
                self.field_labels[key].state(['!disabled'] if enabled else ['disabled'])
        self.model_groups['[pipeline_costs]'].state(['!disabled'] if pipelines and not busy else ['disabled'])

    def load(self):
        path = filedialog.askopenfilename(title='Load model, scenario overlay, or experiment recipe', initialdir=self.root / 'registry', filetypes=[('TOML recipe', '*.toml')])
        if path:
            previous = self.template
            previous_values = {key: variable.get() for key, variable in self.variables.items()}
            previous_bundle = self.bundle
            previous_rows = [self.table.item(row, 'values') for row in self.table.get_children()]
            previous_status = self.status.get()
            try:
                template = self.read_template(Path(path))
                self.template = template
                self.populate()
            except Exception as error:
                self.template = previous
                for key, value in previous_values.items():
                    self.variables[key].set(value)
                self.bundle = previous_bundle
                self.table.delete(*self.table.get_children())
                for row in previous_rows:
                    self.table.insert('', 'end', values=row)
                self.status.set(previous_status)
                self.refresh_buttons()
                messagebox.showerror('Cannot load recipe', str(error))

    def validated(self):
        settings = recipe_from_form({k: v.get() for k, v in self.variables.items()}, self.template)
        load_geospatial_build_config(self.root / settings['matrix']['build_profile'])
        cases = list(scenario_cases(settings))
        scratch = self.root / '.tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, prefix='gold_preview_') as directory:
            for case in cases:
                path = Path(directory) / f"{case['stem']}.toml"
                path.write_text(tomlkit.dumps(case['document']), encoding='utf-8')
                load_model_config(self.root / 'registry/model.toml', path)
        return settings, cases

    def show_cases(self, cases):
        self.table.delete(*self.table.get_children())
        for case in cases:
            self.table.insert('', 'end', values=(case['document']['scenario']['id'], f"{case['share'] * 100:g}%",
                              case['policy'], f"{case['price']:g}", case['storage_requirement'],
                              f"{case['storage_fraction'] * 100:g}%", f"{case['storage_cumulative']:g}"))
        self.status.set(f'{len(cases)} distinct cases. Full settings are recorded in each scenario and plan.json.')

    def preview(self):
        try:
            _, cases = self.validated()
            self.show_cases(cases)
            return True
        except Exception as error:
            messagebox.showerror('Check scenario settings', str(error))
            return False

    def generate(self):
        try:
            settings, cases = self.validated()
            destination = (self.root / self.variables['output'].get()).resolve()
            destination.relative_to(self.root)
            if destination.exists():
                raise ValueError('Choose a new experiment directory; this one already exists.')
            folder = self.root / 'config/scenario_matrices'
            folder.mkdir(parents=True, exist_ok=True)
            recipe = folder / f"{settings['matrix']['name']}_{uuid.uuid4().hex[:8]}.toml"
            with recipe.open('x', encoding='utf-8') as stream:
                stream.write(tomlkit.dumps(settings))
            self.show_cases(cases)
            self.start([sys.executable, 'scripts/plan_gold_experiment.py', '--config', str(recipe), '--output', str(destination)], 'Planning', destination)
        except Exception as error:
            messagebox.showerror('Cannot generate plan', str(error))

    def prepare(self):
        self.start([sys.executable, 'scripts/plan_gold_experiment.py', '--prepare', str(self.bundle), '--dialog-prompts'], 'Preparing Gold', self.bundle)

    def run_batch(self):
        self.start([sys.executable, '-m', 'geocanoe.execution.batch', '--config', str(self.temoa_panel.runset / 'batch.toml')], 'Running batch', self.temoa_panel.bundle)

    def start(self, command, label, destination):
        self.busy = True
        for widget, _ in self.controls:
            widget.configure(state='disabled')
        self.refresh_buttons()
        self.status.set(f'{label}… progress appears below.')
        def work():
            try:
                process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding='utf-8', errors='replace',
                                           env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1'})
                for line in process.stdout:
                    if line.startswith(PROMPT_PREFIX):
                        event, response = threading.Event(), []
                        self.messages.put(('prompt', (json.loads(line[len(PROMPT_PREFIX):]), event, response)))
                        event.wait()
                        process.stdin.write(response[0] + '\n')
                        process.stdin.flush()
                    else:
                        self.messages.put(('log', line))
                self.messages.put(('done', (process.wait(), label, destination)))
            except Exception as error:
                self.messages.put(('log', f'{error}\n'))
                self.messages.put(('done', (1, label, destination)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'prompt':
                    request, event, response = value
                    response.append('y' if self.ask_exclusion(request) else 'n')
                    event.set()
                elif kind == 'log':
                    for log in (self.log, self.temoa_panel.log):
                        log.configure(state='normal')
                        log.insert('end', value)
                        log.see('end')
                        log.configure(state='disabled')
                else:
                    code, label, destination = value
                    self.busy = False
                    for widget, state in self.controls:
                        widget.configure(state=state)
                    if label in {'Planning', 'Preparing Gold'} and (destination / 'plan.json').exists():
                        self.bundle = destination
                    if code == 0 and label == 'Preparing Gold':
                        try:
                            self.temoa_panel.load_bundle(destination)
                            self.tabs.select(self.temoa_panel)
                        except Exception as error:
                            self.messages.put(('log', f'Could not load Gold mappings: {error}\n'))
                    self.status.set(f'{label} {"complete" if code == 0 else "failed — see log"}. Experiment: {destination}')
                    self.refresh_buttons()
        except queue.Empty:
            pass
        self.window.after(150, self.poll)

    def ask_exclusion(self, request):
        dialog = tk.Toplevel(self.window)
        dialog.title('Gold build — review exclusion decision')
        dialog.geometry('900x540')
        dialog.transient(self.window)
        content = ttk.Frame(dialog)
        content.pack(fill='both', expand=True)
        text = tk.Text(content, wrap='word', padx=12, pady=12)
        scroll = ttk.Scrollbar(content, orient='vertical', command=text.yview)
        scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill='both', expand=True)
        text.insert('end', request.get('context', '') + '\n\n' + request['prompt'])
        text.configure(state='disabled')
        result = [False]
        def answer(accepted):
            result[0] = accepted
            dialog.destroy()
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Yes — exclude and continue', command=lambda: answer(True)).pack(side='left')
        audit_lines = [line.removeprefix('Audit written to: ') for line in request.get('context', '').splitlines()
                       if line.startswith('Audit written to: ')]
        if audit_lines:
            audit = (self.root / audit_lines[-1]).resolve()
            def open_audit():
                try:
                    os.startfile(str(audit))
                except OSError as error:
                    messagebox.showerror('Cannot open audit', str(error), parent=dialog)
            ttk.Button(buttons, text='Open audit CSV', command=open_audit).pack(side='left', padx=12)
        ttk.Button(buttons, text='No — stop this build', command=lambda: answer(False)).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW', lambda: answer(False))
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result[0]

    def close(self):
        if self.busy or self.silver_panel.busy:
            messagebox.showinfo('Operation in progress', 'Keep this window open until the current operation finishes.')
        else:
            self.window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('registry/model.toml'))
    parser.add_argument('--experiment', type=Path, help='Open a prepared experiment on the TEMOA configurations tab.')
    parser.add_argument('--silver-config', type=Path, help='Load a Silver build profile in the first tab.')
    parser.add_argument('--tab', choices=['silver', 'gold', 'temoa'], default='silver', help='Module to show initially.')
    args = parser.parse_args()
    root = find_project_root()
    set_taskbar_identity()
    window = tk.Tk()
    app = GoldConfigurator(window, root, root / args.config)
    if args.silver_config:
        app.silver_panel.load_path(root / args.silver_config)
    app.tabs.select({'silver': app.silver_panel, 'gold': app.settings_page, 'temoa': app.temoa_panel}[args.tab])
    if args.experiment:
        app.temoa_panel.load_bundle(root / args.experiment)
        app.tabs.select(app.temoa_panel)
    window.mainloop()


if __name__ == '__main__':
    main()
