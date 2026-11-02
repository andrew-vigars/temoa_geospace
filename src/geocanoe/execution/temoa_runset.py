"""Generate immutable TEMOA run selections from prepared Gold inventories."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import platform
from importlib.metadata import PackageNotFoundError, version
import re

import tomlkit

from geocanoe.execution.batch import load_batch_config, validate_batch_runs
from geocanoe.execution.experiment import digest, write_json
from geocanoe.execution.temoa_config import temoa_default_document

MAPPED_KEYS = {'scenario', 'input_database', 'output_database'}


def default_run_settings():
    return {key: value for key, value in temoa_default_document().unwrap().items() if key not in MAPPED_KEYS}


def validate_run_settings(settings):
    """Validate template types and known choices without invoking a solver."""
    from temoa.core.modes import TemoaMode
    from temoa.core.solver_spec import SolverSpec
    settings = deepcopy(settings)
    if isinstance(settings.get('solver'), str):
        settings['solver'] = {'name': settings['solver'], 'options': {}}
    defaults = default_run_settings()
    def check(values, reference, prefix=''):
        unknown = set(values) - set(reference)
        if unknown:
            raise ValueError(f'Unsupported TEMOA settings in {prefix or "root"}: {sorted(unknown)}')
        for key, value in values.items():
            path = f'{prefix}.{key}' if prefix else key
            sample = reference[key]
            if path == 'solver.options':
                if not isinstance(value, dict):
                    raise ValueError('solver.options must be a TOML table.')
                continue
            if isinstance(sample, dict):
                if not isinstance(value, dict):
                    raise ValueError(f'{path} must be a table.')
                check(value, sample, path)
            elif path == 'morris.seed':
                if type(value) not in (bool, int) or (type(value) is int and value < 0):
                    raise ValueError('morris.seed must be false or a nonnegative integer.')
            elif type(sample) is bool:
                if type(value) is not bool:
                    raise ValueError(f'{path} must be true or false.')
            elif isinstance(sample, (int, float)):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f'{path} must be a finite number.')
                if type(sample) is int and type(value) is not int:
                    raise ValueError(f'{path} must be a whole number.')
            elif not isinstance(value, type(sample)):
                raise ValueError(f'{path} must have type {type(sample).__name__}.')
    check(settings, defaults)
    merged = deepcopy(defaults)
    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and key != 'options':
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)
    merge(merged, settings)
    if 'solver' in settings and 'options' not in settings['solver'] and settings['solver'].get('name') != defaults['solver']['name']:
        merged['solver']['options'] = {}
    if merged['scenario_mode'].upper() not in TemoaMode.__members__:
        raise ValueError('Choose a TEMOA scenario mode from the dropdown.')
    solver = SolverSpec.parse(merged['solver'])
    if solver.name == 'appsi_highs' and merged['save_duals']:
        raise ValueError('appsi_highs does not support save_duals; disable it or choose another solver.')
    if merged['days_per_period'] <= 0:
        raise ValueError('days_per_period must be positive.')
    if merged['plot_commodity_network'] and not merged['source_trace']:
        raise ValueError('plot_commodity_network requires source_trace.')
    if not all(isinstance(value, str) and value for value in merged['extensions']):
        raise ValueError('extensions must be a list of non-empty extension names.')
    return merged


def prepared_inventory(bundle):
    if not (bundle / 'preparation.json').is_file():
        if (bundle / 'plan.json').is_file():
            raise ValueError('This experiment has a plan, but Gold preparation has not started. '
                             'Click Prepare Gold on tab 2 before opening it here. '
                             'If you restarted the UI, load its recipe.toml and generate a new plan first.')
        raise ValueError('This folder has no Gold preparation record. Choose a successfully prepared experiment directory.')
    record = json.loads((bundle / 'preparation.json').read_text(encoding='utf-8'))
    if record.get('status') != 'prepared':
        raise ValueError(f"Gold preparation status is {record.get('status', 'unknown')}. "
                         'TEMOA mapping requires successfully prepared Gold databases; check the experiment logs.')
    if not (bundle / 'inventory.json').is_file():
        raise ValueError('Gold preparation is marked complete, but inventory.json is missing. Check the experiment logs.')
    inventory = json.loads((bundle / 'inventory.json').read_text(encoding='utf-8'))
    cases = inventory['cases']
    if not cases or len({case['scenario_id'] for case in cases}) != len(cases):
        raise ValueError('Prepared inventory must contain unique Gold scenario IDs.')
    return cases


def create_runset(root, bundle, name, settings, enabled, batch_settings):
    """Create a new revision; no Gold rebuilds and no changes to prior configs."""
    root, bundle = root.resolve(), bundle.resolve()
    bundle.relative_to(root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('Run selection name: use 1–80 letters, digits, underscores or hyphens.')
    settings = validate_run_settings(settings)
    cases = prepared_inventory(bundle)
    if set(enabled) != {case['scenario_id'] for case in cases} or any(type(v) is not bool for v in enabled.values()):
        raise ValueError('Every prepared Gold case needs an explicit run selection.')
    for key in ('continue_on_failure', 'skip_completed', 'retry_failed'):
        if type(batch_settings.get(key)) is not bool:
            raise ValueError(f'Batch setting {key} must be true or false.')
    destination = bundle / 'temoa_runs' / name
    if destination.exists():
        raise FileExistsError('Run selection already exists. Choose a new name to preserve the earlier configs.')
    # Validate every mapping before writing a new revision.
    mappings = []
    for case in cases:
        database, manifest = root / case['gold_database'], root / case['gold_manifest']
        if not database.is_file():
            raise FileNotFoundError(f'Gold database missing: {database}')
        identity = json.loads(manifest.read_text(encoding='utf-8'))['artifact']
        if identity['scenario_id'] != case['scenario_id'] or identity['fingerprint'] != case['schema_fingerprint']:
            raise ValueError(f"Gold manifest does not match inventory: {case['scenario_id']}")
        # TEMOA uses underscores for results labels; Gold's own ID stays unchanged.
        label = case['scenario_id'].replace('-', '_')
        if not re.fullmatch(r'[A-Za-z0-9_]+', label):
            raise ValueError(f'Invalid results label: {label}')
        mappings.append((case, database, manifest, label))
    if len({row[3] for row in mappings}) != len(mappings):
        raise ValueError('Gold IDs map to duplicate TEMOA result labels.')
    destination.mkdir(parents=True)
    write_json(destination / 'status.json', {'status': 'generating'})
    try:
        runs, records = [], []
        for case, database, manifest, label in mappings:
            document = temoa_default_document()
            for key, value in settings.items():
                document[key] = value
            document['scenario'] = label
            document['input_database'] = database.relative_to(root).as_posix()
            document['output_database'] = document['input_database']
            config = destination / 'solver' / f'{label}.toml'
            config.parent.mkdir(exist_ok=True)
            config.write_text(tomlkit.dumps(document), encoding='utf-8')
            snapshot = destination / 'gold_manifests' / f'{label}.json'
            snapshot.parent.mkdir(exist_ok=True)
            snapshot.write_bytes(manifest.read_bytes())
            config_path = config.relative_to(root).as_posix()
            selected = enabled[case['scenario_id']]
            runs.append({'config': config_path, 'enabled': selected})
            records.append({'gold_scenario_id': case['scenario_id'], 'temoa_scenario': label,
                            'gold_database': document['input_database'], 'gold_manifest': case['gold_manifest'],
                            'gold_manifest_snapshot': snapshot.relative_to(root).as_posix(),
                            'gold_manifest_sha256': digest(snapshot), 'schema_fingerprint': case['schema_fingerprint'],
                            'solver_config': config_path, 'solver_config_sha256': digest(config), 'enabled': selected})
        batch = destination / 'batch.toml'
        batch.write_text(tomlkit.dumps({'batch': {'name': name, **batch_settings}, 'runs': runs}), encoding='utf-8')
        _, parsed = load_batch_config(batch)
        validate_batch_runs(parsed)
        packages = {}
        for package in ('geocanoe', 'temoa', 'pyomo', 'gurobipy'):
            try:
                packages[package] = version(package)
            except PackageNotFoundError:
                packages[package] = None
        from temoa.core.solver_spec import SolverSpec, resolve_solver_options
        record = {'format_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
                  'python': platform.python_version(), 'packages': packages,
                  'base_solver_options': resolve_solver_options(SolverSpec.parse(settings['solver'])),
                  'experiment': bundle.relative_to(root).as_posix(), 'settings': settings,
                  'batch_settings': batch_settings, 'batch_sha256': digest(batch),
                  'source_inventory_sha256': digest(bundle / 'inventory.json'), 'cases': records}
        write_json(destination / 'runset.json', record)
        lines = [f'# TEMOA run selection: {name}', '',
                 f'Enabled: {sum(enabled.values())}/{len(cases)}. Gold databases are reused.', '',
                 '| Run | Gold scenario | TEMOA label | Database | Config |', '|---|---|---|---|---|']
        lines += [f"| {'Yes' if r['enabled'] else 'No'} | {r['gold_scenario_id']} | {r['temoa_scenario']} | `{r['gold_database']}` | `{r['solver_config']}` |" for r in records]
        lines += ['', '## Shared TEMOA settings', '', '```toml', tomlkit.dumps(settings), '```', '',
                  '## Run', '', '```powershell',
                  f'.venv/Scripts/python.exe -m geocanoe.execution.batch --config "{batch.relative_to(root).as_posix()}"', '```', '',
                  'runset.json records all mappings, selections and configuration hashes. Solver availability and model-specific mode requirements are checked when running.']
        (destination / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        write_json(destination / 'status.json', {'status': 'generated'})
        return destination
    except Exception as error:
        write_json(destination / 'status.json', {'status': 'failed', 'error': str(error)})
        raise
