"""Reviewable, isolated experiment bundles for the Gold matrix pilot."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distributions
import json
import platform
import subprocess

import tomlkit

from geocanoe.config import load_geospatial_build_config, load_model_config


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + '\n', encoding='utf-8')


def git_value(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def create_plan(root, recipe, destination, cases):
    """Validate and freeze a new bundle; never overwrite an earlier experiment.

    Planning writes only inside destination. It neither builds Gold nor solves.
    The existing matrix preparer consumes recipe.toml for optional preparation.
    """
    root, recipe, destination = root.resolve(), recipe.resolve(), destination.resolve()
    relative = destination.relative_to(root).as_posix()
    if destination.exists():
        raise FileExistsError(f'Experiment destination already exists: {destination}')
    settings = tomlkit.parse(recipe.read_text(encoding='utf-8'))
    load_geospatial_build_config(root / settings['matrix']['build_profile'])
    # Snapshot controlled inputs and implementation, including uncommitted files.
    inputs = {recipe, root / 'pyproject.toml'}
    for directory, patterns in [('src', ['*.py']), ('scripts', ['*.py']),
                                ('registry', ['*.toml', '*.csv', '*.yaml', '*.yml'])]:
        for pattern in patterns:
            inputs.update((root / directory).rglob(pattern))
    inputs.add(root / settings['matrix']['build_profile'])
    # Follow existing file references in TOML, including nested Silver configs.
    pending = list(inputs)
    while pending:
        path = pending.pop()
        if path.suffix != '.toml':
            continue
        def references(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from references(child)
            elif isinstance(value, list):
                for child in value:
                    yield from references(child)
            elif isinstance(value, str):
                candidate = (root / value).resolve()
                if candidate.is_relative_to(root):
                    relative_path = candidate.relative_to(root)
                    if (relative_path.parts and relative_path.parts[0] in {'config', 'registry', 'src', 'scripts'}
                            and candidate.suffix in {'.toml', '.csv', '.yaml', '.yml', '.json', '.py'}
                            and candidate.is_file()):
                        yield candidate
        for referenced in references(tomlkit.parse(path.read_text(encoding='utf-8'))):
            if referenced not in inputs:
                inputs.add(referenced)
                pending.append(referenced)
    source_records = []
    for path in sorted(inputs):
        source_records.append({'path': path.relative_to(root).as_posix(), 'sha256': digest(path)})
    matrix = settings['matrix']
    matrix['scenario_dir'] = f'{relative}/scenarios'
    matrix['solver_dir'] = f'{relative}/solver'
    matrix['batch_path'] = f'{relative}/batch.toml'
    matrix['inventory_path'] = f'{relative}/inventory.json'
    matrix['log_dir'] = f'{relative}/logs'
    destination.mkdir(parents=True)
    try:
        resolved = []
        for case in cases:
            path = destination / 'scenarios' / f"{case['stem']}.toml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(tomlkit.dumps(case['document']), encoding='utf-8')
            model = load_model_config(root / 'registry/model.toml', path)
            resolved.append({**case, 'resolved_model': asdict(model) if is_dataclass(model) else model,
                             'scenario_path': path.relative_to(root).as_posix()})
        for record in source_records:
            target = destination / 'snapshots' / record['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / record['path']).read_bytes())
        (destination / 'recipe.toml').write_text(tomlkit.dumps(settings), encoding='utf-8')
        plan = {
            'format_version': 1, 'kind': 'gold_model_matrix' if 'model' in settings else 'national_gasoline_gold_matrix', 'status': 'planned',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'name': matrix['name'],
            'original_recipe': recipe.relative_to(root).as_posix(),
            'prepared_recipe_sha256': digest(destination / 'recipe.toml'),
            'git': {'commit': git_value(root, 'rev-parse', 'HEAD'),
                    'status': git_value(root, 'status', '--porcelain')},
            'python': platform.python_version(),
            'packages': sorted([{'name': d.metadata['Name'], 'version': d.version}
                                for d in distributions()], key=lambda d: d['name'].lower()),
            'inputs': source_records, 'settings': settings, 'cases': resolved,
            'limitations': ['Silver datasets are not copied or verified by planning. Gold manifests must identify build inputs.',
                            'Preparation uses the current checkout and installed environment; snapshots preserve controlled inputs for recovery.',
                            'Solver version and execution outcomes belong to the batch runner; this plan launches no solves.'],
        }
        write_json(destination / 'plan.json', plan)
        write_report(destination, plan)
        return plan
    except Exception as error:
        write_json(destination / 'failure.json', {'phase': 'plan', 'error': str(error)})
        raise


def verify_inputs(root, plan):
    """Reject preparation if controlled inputs changed after planning."""
    for record in plan['inputs']:
        path = root / record['path']
        if not path.is_file() or digest(path) != record['sha256']:
            raise ValueError(f"Input changed since planning: {record['path']}")


def write_report(destination, plan, preparation=None):
    settings = plan['settings']
    common = settings.get('common', {})
    time = settings.get('model', {}).get('time', {})
    start = time.get('start_year', common.get('period_start'))
    end = time.get('end_year', common.get('period_end'))
    lines = [f"# {plan['name']}", '', f"Status: {preparation['status'] if preparation else plan['status']}", '',
             f"Cases: {len(plan['cases'])}. Horizon: {start}–{end}.", '',
             'Graph-snap outliers: ' + ('automatically exclude all site/CO2 outliers (explicit experiment setting).'
                                      if settings['matrix'].get('auto_exclude_outliers') else 'ask for each exclusion decision.'), '',
             '## Policy boundaries', '',
             '- Synthetic shares are national minimum mass shares, not fixed blends at every location.',
             '- Carbon prices are CAD2020/t released facility-origin CO2e.',
             '- Storage requirements are independent of emissions pricing and do not imply whole-system net zero.',
             '- Cumulative storage targets are tonnes CO2e across the complete model period.', '',
             '## Matrix dimensions', '',
             *[f'- `{key}`: {values}' for key, values in settings.get('axes', {}).items()], '',
             '## Cases', '',
             '| Scenario | Minimum synthetic share | Emissions policy | Carbon price (CAD2020/t) | Storage requirement | Storage fraction | Cumulative storage (t) |',
             '|---|---:|---|---:|---|---:|---:|']
    for case in plan['cases']:
        storage = case['document'].get('storage', {})
        lines.append(f"| {case['document']['scenario']['id']} | {case['share'] * 100:g}% | {case['policy']} | {case['price']:g} | {storage.get('requirement', 'none')} | {case['storage_fraction'] * 100:g}% | {storage.get('minimum_cumulative_activity', 0):g} |")
    lines += ['', '## Records', '',
              '- `plan.json`: resolved models, shared settings, input hashes, Git state, and installed package versions.',
              '- `snapshots/`: controlled input and Python implementation copies, including local changes.',
              '- `recipe.toml`: preparation recipe with isolated scenario, solver, and batch destinations.',
              '- `preparation.json`: preparation result and output hashes, when preparation is requested.', '',
              '## Preparation and execution', '',
              'From the repository root, prepare this existing plan:', '',
              '```powershell',
              f'.venv/Scripts/python.exe scripts/plan_gold_experiment.py --prepare "{destination.as_posix()}"',
              '```', '', 'After successful preparation, configure and select TEMOA runs on tab 3:' if settings['matrix'].get('defer_temoa_configs') else 'After successful preparation, launch the batch separately:', '', '```powershell',
              (f'.venv/Scripts/python.exe scripts/configure_gold.py --experiment "{destination.as_posix()}"'
               if settings['matrix'].get('defer_temoa_configs') else
               f".venv/Scripts/python.exe -m geocanoe.execution.batch --config \"{plan['settings']['matrix']['batch_path']}\""),
              '```', '', '## Reproducibility limits', '']
    lines += [f'- {item}' for item in plan['limitations']]
    if preparation and preparation.get('error'):
        lines += ['', '## Preparation failure', '', preparation['error']]
    (destination / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
