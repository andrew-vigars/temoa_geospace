"""Normalize verified EIA Bronze gasoline prices into auditable CAD2020 Silver.

No network, pickle, sibling imports, sibling writes, or Gold changes occur.
The separate fuel_supply_reference command calls the original CANOE functions.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tomllib

import pandas as pd

from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.acquisition.fuel_supply import validate_bronze
from geocanoe.costs.economics import conversion_basis
from geocanoe.paths import find_project_root


PROVINCES = {'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'}
TERRITORIES = {'NT', 'NU', 'YT'}
ALIASES = {'NLLAB': 'NL', 'PEI': 'PE'}


def select_price(frame: pd.DataFrame, year: int) -> float:
    """Require exactly one finite positive selected gasoline observation."""
    values = pd.to_numeric(frame.loc[
        frame['Tech Name'].eq('T_gsl') & frame['period'].astype(int).eq(year), 'value'
    ], errors='raise')
    if len(values) != 1 or not values.map(lambda x: 0 < x < float('inf')).all():
        raise ValueError(f'Expected one finite positive T_gsl price for {year}')
    return float(values.iloc[0])


def normalize_price(source_value, gj_per_mmbtu, heating_value, currency_multiplier):
    """Return CAD/GJ (numerically MCAD/PJ) and CAD/tonne without a time factor."""
    factors = (source_value, gj_per_mmbtu, heating_value, currency_multiplier)
    if not all(math.isfinite(v) and v > 0 for v in factors):
        raise ValueError('Price conversion factors must be finite and positive')
    per_gj = source_value / gj_per_mmbtu * currency_multiplier
    return per_gj, per_gj * heating_value


def load_settings(config_path=None, root=None):
    root = Path(root) if root else find_project_root()
    path = root / (config_path or 'config/canoe_fuel/silver.toml')
    settings = tomllib.loads(path.read_text(encoding='utf-8'))
    start, end, year = (settings[k] for k in ('period_start', 'period_end', 'price_year'))
    if (any(type(v) is not int for v in (start, end, year)) or end <= start
            or year != end or settings['price_selection'] != 'period_end'):
        raise ValueError('Period-end pricing requires integer price_year == period_end > period_start')
    if (settings['source_currency'], settings['source_price_year'], settings['source_units']) != (
            'USD', 2024, '2024 $/MMBtu'):
        raise ValueError('Expected explicit 2024 USD/MMBtu source basis')
    provinces = [ALIASES.get(p, p) for p in settings['province_list']]
    if not provinces or len(set(provinces)) != len(provinces) or not set(provinces) <= PROVINCES:
        raise ValueError('Province list must contain unique recognized jurisdictions')
    policy = settings['territorial_price_policy']
    if policy not in {'common_us_series', 'error'}:
        raise ValueError('Unknown territorial price policy')
    if policy == 'error' and set(provinces) & TERRITORIES:
        raise ValueError('Territorial prices require an explicit common_us_series proxy')
    if settings['price_boundary'] != 'us_sector_delivered_including_embedded_taxes':
        raise ValueError('Only unchanged US sector-delivered price boundary is implemented')
    return root, path, settings, provinces


def prepare_inputs(config_path=None, root=None):
    root, path, settings, provinces = load_settings(config_path, root)
    bronze_path, raw, bronze = validate_bronze(root / settings['bronze_config'], root)
    properties_path = root / settings['properties_config']
    properties = tomllib.loads(properties_path.read_text(encoding='utf-8'))
    if (properties['heating_value_basis'] != 'HHV' or properties['energy_basis_policy'] !=
            'assume_eia_gross_energy_and_common_gasoline_hhv'):
        raise ValueError('Gasoline mass conversion requires the declared HHV assumption')
    basis = conversion_basis(settings['source_currency'], settings['source_price_year'],
                             config=root / settings['economic_config'])
    if basis['target_currency'] != 'CAD2020' or basis['basis']['target_scale'] != 1.0:
        raise ValueError('Fuel Silver target must be CAD2020 dollars, not millions')
    selected = raw.loc[raw.seriesId.eq(settings['series_id']) &
                       raw.unit.eq(settings['source_units'])].copy()
    if selected.empty or not selected.seriesName.eq(settings['series_name']).all():
        raise ValueError('Gasoline EIA series identity mismatch')
    selected['Tech Name'] = 'T_gsl'
    selected['value'] = pd.to_numeric(selected.value, errors='coerce')
    selected['period'] = pd.to_numeric(selected.period, errors='raise').astype(int)
    expected_years = set(range(bronze['source']['start_year'], bronze['source']['end_year'] + 1))
    if (set(selected.period) != expected_years or selected.period.duplicated().any()
            or not selected.value.map(lambda v: math.isfinite(v) and v > 0).all()):
        raise ValueError('Selected gasoline series must have one positive price per release year')
    source_value = select_price(selected, settings['price_year'])
    per_gj, per_tonne = normalize_price(source_value, properties['gj_per_mmbtu'],
                                       properties['heating_value_gj_per_tonne'], basis['multiplier'])
    return (root, path, settings, provinces, bronze_path, raw, bronze,
            properties_path, properties, basis, selected, source_value, per_gj, per_tonne)


def build(config_path=None, root=None):
    (root, path, settings, provinces, bronze_path, raw, bronze,
     properties_path, properties, basis, selected, source_value, per_gj, per_tonne) = prepare_inputs(config_path, root)
    output = root / settings['output_dir']
    output.mkdir(parents=True, exist_ok=True)
    rows = [dict(
        province=p, period=settings['period_start'], period_end=settings['period_end'],
        period_years=settings['period_end']-settings['period_start'], price_year=settings['price_year'],
        commodity='gasoline', source_series_id=settings['series_id'], source_value=source_value,
        source_units=settings['source_units'], source_currency='USD', source_price_year=2024,
        cost=per_tonne, units='CAD2020/t gasoline', cost_cad2020_per_gj=per_gj,
        cost_mcad2020_per_pj=per_gj, heating_value_basis='HHV',
        heating_value_gj_per_tonne=properties['heating_value_gj_per_tonne'],
        gj_per_mmbtu=properties['gj_per_mmbtu'], currency_multiplier=basis['multiplier'],
        price_selection=settings['price_selection'], status='normalized_with_explicit_assumptions',
        price_boundary=settings['price_boundary'], geography_basis='common US EIA series proxy',
        is_territorial_proxy=p in TERRITORIES,
    ) for p in provinces]
    table = pd.DataFrame(rows)
    files = ['eia_table3.parquet', 'eia_gasoline_source_rows.csv',
             'gasoline_supply_prices.csv', 'gasoline_supply_prices.parquet']
    raw.to_parquet(output / files[0], index=False)
    selected.drop(columns='Tech Name').sort_values('period').to_csv(output / files[1], index=False)
    table.to_csv(output / files[2], index=False)
    table.to_parquet(output / files[3], index=False)
    manifest(output / 'manifest.json', {
        'stage': 'Silver conventional gasoline supply', 'schema_version': 2,
        'method': 'native EIA observation / GJ per MMBtu * shared FX/GDP * HHV',
        'status': 'normalized_with_explicit_assumptions',
        'adapter_config': settings, 'adapter_config_sha256': sha256(path),
        'adapter_sha256': sha256(Path(__file__)),
        'source': bronze, 'bronze_path': str(bronze_path.relative_to(root)),
        'bronze_manifest_sha256': sha256(bronze_path.parent / 'manifest.json'),
        'properties': properties, 'properties_sha256': sha256(properties_path),
        'economics': basis, 'rows': len(table),
        'files': {f: sha256(output / f) for f in files},
        'missing_jurisdictions': sorted(PROVINCES - set(provinces)),
        'territorial_price_policy': settings['territorial_price_policy'],
        'annual_basis': 'Unit price; annual demand unchanged; no period-duration multiplication',
        'assumptions': [
            '2050 endpoint price applies to the full representative 2025-2050 period, not a period average.',
            'US sector-delivered gasoline is a proxy for every configured Canadian jurisdiction.',
            'Embedded US taxes and policy costs are retained; no Canadian tax replacement or added delivery charge.',
            'EIA energy prices are treated as gross energy; apply the controlled common gasoline HHV.',
            'ECCC HHV is a mass-price normalization assumption, not a new blend model or a change in demand density.',
            'No emissions factors or Gold costs changed. Upstream reference calculations are separate.',
        ],
    })
    return output


def validate_outputs(config_path=None, root=None):
    """Verify Silver bytes, current dependencies, coverage and conversion results."""
    (root, path, settings, provinces, bronze_path, raw, bronze,
     properties_path, properties, basis, selected, source_value, per_gj, per_tonne) = prepare_inputs(config_path, root)
    output = root / settings['output_dir']
    record = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    required = {'eia_table3.parquet', 'eia_gasoline_source_rows.csv',
                'gasoline_supply_prices.csv', 'gasoline_supply_prices.parquet'}
    if set(record['files']) != required:
        raise ValueError('Fuel Silver output inventory mismatch')
    for name, digest in record['files'].items():
        if sha256(output / name) != digest:
            raise ValueError(f'Fuel Silver checksum mismatch: {name}')
    if (record['schema_version'] != 2 or record['adapter_config'] != settings
            or record['adapter_config_sha256'] != sha256(path)
            or record['adapter_sha256'] != sha256(Path(__file__))
            or record['properties_sha256'] != sha256(properties_path)
            or record['economics'] != basis
            or record['source'] != bronze
            or record['bronze_manifest_sha256'] != sha256(bronze_path.parent / 'manifest.json')):
        raise ValueError('Fuel Silver dependencies changed; rebuild Silver')
    table = pd.read_parquet(output / 'gasoline_supply_prices.parquet')
    pd.testing.assert_frame_equal(table, pd.read_csv(output / 'gasoline_supply_prices.csv'))
    pd.testing.assert_frame_equal(raw, pd.read_parquet(output / 'eia_table3.parquet'))
    if (len(table) != len(provinces) or record['rows'] != len(table)
            or table.province.tolist() != provinces
            or table.duplicated(['province', 'period']).any()
            or not table.units.eq('CAD2020/t gasoline').all()
            or not table.period.eq(settings['period_start']).all()
            or not table.price_year.eq(settings['price_year']).all()
            or not table.cost.eq(per_tonne).all()
            or not table.cost_cad2020_per_gj.eq(per_gj).all()
            or not table.source_value.eq(source_value).all()):
        raise ValueError('Fuel Silver coverage, period or conversion mismatch')
    return table


def run_fuel_supply_build():
    """Config-independent Silver workflow stage."""
    return build()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        print(f'Validated {len(validate_outputs(args.config))} fuel price rows')
    else:
        print(build(args.config))


if __name__ == '__main__':
    main()
