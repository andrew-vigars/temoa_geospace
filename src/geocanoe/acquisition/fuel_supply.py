"""Import a pinned, complete CANOE_CLOUD EIA response into GeoCANOE Bronze.

No sibling caches or databases are modified. Explicit source refreshes use a
new configuration pin and release directory. Verified caches work offline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tomllib

import pandas as pd

from geocanoe.acquisition.era5_renewables import acquisition_lock, manifest, sha256
from geocanoe.paths import find_project_root


def load_settings(config_path=None, root=None):
    root = Path(root) if root else find_project_root()
    path = Path(config_path) if config_path else Path('config/canoe_fuel/bronze.toml')
    path = (root / path).resolve()
    settings = tomllib.loads(path.read_text(encoding='utf-8'))
    if settings['provider'] != 'CANOE_CLOUD':
        raise ValueError('Fuel Bronze requires an explicit CANOE_CLOUD source')
    if Path(settings['filename']).name != settings['filename']:
        raise ValueError('Fuel Bronze filename must be a basename')
    return root, path, settings


def read_response(path, settings):
    """Validate edition, scope, completeness and unique EIA observation keys."""
    if sha256(path) != settings['source_sha256']:
        raise ValueError('EIA source checksum mismatch')
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    response = payload['response']
    request = payload['request']
    if request['command'].strip('/') != f"v2/aeo/{settings['aeo_year']}/data":
        raise ValueError('EIA request edition mismatch')
    frame = pd.DataFrame(response['data'])
    required = {'period', 'scenario', 'tableId', 'regionId', 'seriesId',
                'seriesName', 'unit', 'value'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('EIA response lacks required observations/columns')
    if len(frame) != int(response['total']):
        raise ValueError('EIA response is truncated; acquire all pages before import')
    for column, expected in [('scenario', settings['scenario']),
                             ('tableId', str(settings['table_id'])),
                             ('regionId', str(settings['region_id']))]:
        if set(frame[column].astype(str)) != {expected}:
            raise ValueError(f'EIA {column} scope mismatch')
    keys = ['period', 'scenario', 'regionId', 'seriesId', 'unit']
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError('EIA observation keys are missing or duplicated')
    years = pd.to_numeric(frame['period'], errors='raise')
    expected_years = set(range(settings['start_year'], settings['end_year'] + 1))
    if set(years) != expected_years:
        raise ValueError('EIA response years do not match release scope')
    return frame


def validate_bronze(config_path=None, root=None):
    """Verify a local Bronze cache against its release pin and manifest."""
    root, config_path, settings = load_settings(config_path, root)
    raw = root / settings['raw_dir']
    path = raw / settings['filename']
    record = json.loads((raw / 'manifest.json').read_text(encoding='utf-8'))
    if record['source'] != settings or record['sha256'] != sha256(path):
        raise ValueError('EIA Bronze manifest mismatch')
    frame = read_response(path, settings)
    if record['rows'] != len(frame):
        raise ValueError('EIA Bronze row count mismatch')
    return path, frame, record


def acquire_fuel_supply(config_path=None, root=None):
    root, config_path, settings = load_settings(config_path, root)
    raw = root / settings['raw_dir']
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / settings['filename']
    with acquisition_lock(raw):
        if path.exists():
            validate_bronze(config_path, root)
            return raw
        staged = path.with_suffix('.json.part')
        try:
            shutil.copyfile(root / settings['source_path'], staged)
            frame = read_response(staged, settings)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)
        manifest(raw / 'manifest.json', {
            'stage': 'Bronze EIA fuel supply', 'source': settings,
            'sha256': sha256(path), 'bytes': path.stat().st_size,
            'rows': len(frame), 'config_sha256': sha256(config_path),
            'scope': 'Complete unmodified CANOE_CLOUD EIA response; no conversion',
        })
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    print(acquire_fuel_supply(args.config))


if __name__ == '__main__':
    main()
