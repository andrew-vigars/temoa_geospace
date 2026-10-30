"""Draft national gasoline/carbon scenarios and optionally prepare a runnable batch.

No optimizations are launched. --build encodes a fresh Gold database per case,
then writes standard TEMOA solver configs and the existing batch-runner TOML.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tomllib
from unittest.mock import patch

import tomlkit

from geocanoe.config import load_geospatial_build_config, load_model_config
from geocanoe.paths import find_project_root


def scenario_cases(settings):
    """Expand the matrix with explicit, comparable policy boundaries."""
    matrix, common = settings['matrix'], settings['common']
    shares, prices = matrix['synthetic_shares'], matrix['carbon_prices']
    if (not shares or len(set(shares)) != len(shares)
            or not all(type(s) in (int, float) and math.isfinite(s) and 0 <= s <= 1 for s in shares)):
        raise ValueError('Synthetic shares must be unique finite fractions in [0, 1]')
    if (len(set(prices)) != len(prices)
            or not all(type(p) in (int, float) and math.isfinite(p) and p > 0 for p in prices)):
        raise ValueError('Carbon prices must be unique finite positive numbers')
    fraction = matrix['net_zero_point_source_fraction']
    if not 0 < fraction <= 1:
        raise ValueError('Explicit net-zero storage fraction must be in (0, 1]')
    policies = [('baseline', 'base', 0, 0)]
    if matrix['include_net_zero']:
        policies.append(('net_zero', 'nz', 0, fraction))
    policies.extend((f'price_{p:g}', f'p{p:g}', p, 0) for p in prices)
    seen = set()
    for share in shares:
        pct = share * 100
        if not math.isclose(pct, round(pct)):
            raise ValueError('This matrix names shares in whole percentage points')
        pct = round(pct)
        for policy, short, price, storage_fraction in policies:
            stem = f'syn{pct:03d}_{policy}'
            scenario_id = f'ng-s{pct:03d}-{short}'
            if len(scenario_id) > 20 or scenario_id in seen:
                raise ValueError('Matrix scenario ID is too long or duplicated')
            seen.add(scenario_id)
            doc = {
                'scenario': {'id': scenario_id, 'description':
                    f'National gasoline minimum synthetic share {pct}%; {policy}; common national T4/GCC infrastructure'},
                'time': {'start_year': common['period_start'], 'end_year': common['period_end']},
                'synthetic_gasoline': {'minimum_national_share': share},
                'conventional_gasoline': {'silver_config': common['gasoline_silver_config']},
                'emissions': {'projection_method': 'constant',
                              'policy': 'emissions_price' if price else 'none',
                              'price_per_tonne': price,
                              'fallback_gasoline_emission_factor': common['fallback_gasoline_emission_factor']},
                'storage': {'requirement': 'minimum_point_source_fraction' if storage_fraction else 'none',
                            'minimum_cumulative_activity': 0, 'point_source_fraction': storage_fraction},
                'basemap': {'grid_type': common['grid_type'], 'resolution': common['resolution']},
                'transport_modes': {'roads_enabled': common['roads_enabled'],
                                    'pipelines_enabled': common['pipelines_enabled'], 'disabled_technologies': []},
                'pipeline_costs': {'impedance_scope': common['pipeline_impedance_scope'],
                                   'eos_stack_counts': {'CO2_PIPE': common['co2_pipeline_eos_stack_count'],
                                                        'GSL_PIPE': 1, 'H2_PIPE': 1, 'METOH_PIPE': 1}},
            }
            yield {'stem': stem, 'share': share, 'policy': policy, 'price': price,
                   'storage_fraction': storage_fraction, 'document': doc}


def build_one(root, settings, scenario):
    """Accept only the explicitly recorded offshore outlier coordinates."""
    import geopandas as gpd
    import pandas as pd
    from geocanoe.schema.build import (
        ARCHIVED_LEGACY_INPUTS, CO2_SOURCE_PATH, run_schema_build,
    )
    profile = load_geospatial_build_config(root / settings['matrix']['build_profile'])
    configured = set(settings['matrix']['confirmed_co2_outlier_facility_ids'])
    facilities = gpd.read_file(CO2_SOURCE_PATH)
    selected = facilities.loc[facilities.facility_id.isin(configured)]
    if set(selected.facility_id) != configured or len(selected) != len(configured):
        raise ValueError('Configured offshore facility IDs do not match the emissions release')
    expected = {(round(float(x), 6), round(float(y), 6))
                for x, y in zip(selected.longitude, selected.latitude)}
    def respond(prompt):
        if prompt != 'Continue the schema build and exclude these outliers? [y/n]: ':
            raise ValueError(f'Unrecognized interactive build decision: {prompt}')
        audit = pd.read_csv(ARCHIVED_LEGACY_INPUTS / 'co2_facilities_graph_snap_distance_audit.csv')
        actual = {(round(float(x), 6), round(float(y), 6))
                  for x, y in zip(audit.longitude, audit.latitude)}
        if actual != expected or len(audit) != len(expected):
            raise ValueError('Snap outliers differ from the explicitly confirmed matrix boundary')
        print('Matrix confirms only the previously excluded offshore facilities:', sorted(configured), flush=True)
        return 'y'
    with patch('builtins.input', respond):
        database = run_schema_build(profile, scenario)
    gc.collect()
    return database


def prepare(profile_path, build=False):
    root = find_project_root()
    profile_path = (root / profile_path).resolve()
    if not profile_path.is_file():
        raise FileNotFoundError(f'Local matrix recipe not found: {profile_path}. Create it from docs/national-gasoline-matrix.md; run recipes are intentionally untracked.')
    settings = tomllib.loads(profile_path.read_text(encoding='utf-8'))
    matrix = settings['matrix']
    cases = list(scenario_cases(settings))
    scenario_dir = root / matrix['scenario_dir']
    scenario_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        scenario = scenario_dir / f"{case['stem']}.toml"
        text = '# Generated from config/scenario_matrices/national_gasoline.toml.\n'
        text += '# Prices are CAD2020/t released facility-origin CO2e; net_zero retains the explicit storage fraction.\n'
        scenario.write_text(text + tomlkit.dumps(case['document']), encoding='utf-8')
        load_model_config(root / 'registry/model.toml', scenario)
        case['scenario'] = scenario
    print(f'Drafted and validated {len(cases)} scenario overlays.', flush=True)
    if not build:
        return cases
    from geocanoe.execution.temoa_config import generate_temoa_config
    from geocanoe.execution.batch import load_batch_config, validate_batch_runs
    solver_dir = root / matrix['solver_dir']
    solver_dir.mkdir(parents=True, exist_ok=True)
    logs = root / '.tmp/national_gasoline_matrix'
    logs.mkdir(parents=True, exist_ok=True)
    runs, inventory = [], []
    for index, case in enumerate(cases, 1):
        print(f"Preparing {index}/{len(cases)}: {case['stem']}", flush=True)
        log_path = logs / f"{case['stem']}.log"
        with log_path.open('w', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--config', str(profile_path),
                                     '--build-one', str(case['scenario'])], cwd=root,
                                    stdout=stream, stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            raise RuntimeError(f"Gold preparation failed for {case['stem']}; see {log_path}")
        lines = log_path.read_text(encoding='utf-8').splitlines()
        database = Path(next(line.removeprefix('MATRIX_DATABASE=') for line in reversed(lines)
                             if line.startswith('MATRIX_DATABASE=')))
        manifest = json.loads(database.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        if manifest['artifact']['scenario_id'] != case['document']['scenario']['id']:
            raise ValueError('Prepared database identity does not match matrix scenario')
        solver = solver_dir / f"{case['stem']}.toml"
        generate_temoa_config(solver, force=True)
        doc = tomlkit.parse(solver.read_text(encoding='utf-8'))
        doc['scenario'] = case['document']['scenario']['id']
        relative_database = database.relative_to(root).as_posix()
        doc['input_database'] = relative_database
        doc['output_database'] = relative_database
        solver.write_text(tomlkit.dumps(doc), encoding='utf-8')
        runs.append({'config': solver.relative_to(root).as_posix(), 'enabled': True})
        inventory.append({
            'scenario_id': case['document']['scenario']['id'], 'synthetic_share': case['share'],
            'carbon_policy': case['policy'], 'carbon_price_cad2020_per_tonne': case['price'],
            'storage_fraction': case['storage_fraction'], 'gold_database': relative_database,
            'gold_manifest': database.with_suffix('.manifest.json').relative_to(root).as_posix(),
            'solver_config': solver.relative_to(root).as_posix(),
            'schema_fingerprint': manifest['artifact']['fingerprint'],
        })
    batch = root / matrix['batch_path']
    batch.parent.mkdir(parents=True, exist_ok=True)
    batch.write_text('# Prepared national gasoline/carbon matrix. No solves launched during preparation.\n'
                     + tomlkit.dumps({'batch': {'name': matrix['name'], **settings['batch']}, 'runs': runs}),
                     encoding='utf-8')
    _, batch_runs = load_batch_config(batch)
    validate_batch_runs(batch_runs)
    inventory_path = root / matrix['inventory_path']
    inventory_path.write_text(json.dumps({
        'profile': str(profile_path.relative_to(root)),
        'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        'build_profile': matrix['build_profile'], 'batch_path': matrix['batch_path'],
        'confirmed_co2_outlier_facility_ids': matrix['confirmed_co2_outlier_facility_ids'],
        'cases': inventory,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Validated {len(batch_runs)} runnable cases: {batch}', flush=True)
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config/scenario_matrices/national_gasoline.toml'))
    parser.add_argument('--build', action='store_true', help='Prepare all databases/configs/batch; do not optimize.')
    parser.add_argument('--build-one', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.build_one:
        root = find_project_root()
        settings = tomllib.loads((root / args.config).read_text(encoding='utf-8'))
        print(f'MATRIX_DATABASE={build_one(root, settings, args.build_one)}', flush=True)
    else:
        prepare(args.config, args.build)


if __name__ == '__main__':
    main()
