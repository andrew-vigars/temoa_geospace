"""Map verified Silver conventional gasoline prices into Gold demand regions."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.paths import find_project_root
from geocanoe.preprocessing.fuel_supply import ALIASES, PROVINCES, load_settings, validate_outputs


def load_price_bundle(config_path, model_period, model_end_year, root=None):
    """Read and validate local Silver; never download or import the fuels repo."""
    root = Path(root) if root else find_project_root()
    root, path, settings, _ = load_settings(config_path, root)
    prices = validate_outputs(path, root)
    if (settings['period_start'], settings['period_end'], settings['price_year']) != (
            model_period, model_end_year, model_end_year):
        raise ValueError('Conventional gasoline Silver period does not match Gold; rebuild Silver for this horizon')
    output = root / settings['output_dir']
    record_path = output / 'manifest.json'
    record = json.loads(record_path.read_text(encoding='utf-8'))
    evidence = {
        'silver_config': str(path.resolve()), 'silver_config_sha256': sha256(path),
        'silver_manifest': str(record_path.resolve()), 'silver_manifest_sha256': sha256(record_path),
        'files': record['files'], 'units': 'CAD2020/t gasoline',
        'price_year': settings['price_year'], 'price_boundary': settings['price_boundary'],
        'status': record['status'], 'assumptions': record['assumptions'],
    }
    return prices, evidence


def map_conventional_prices(sites, prices, model_period, model_end_year):
    """Require exactly one province-period delivered price per demand region."""
    required = {'province', 'period', 'period_end', 'price_year', 'cost', 'units',
                'source_series_id', 'price_boundary', 'status', 'is_territorial_proxy'}
    if not required.issubset(prices.columns):
        raise ValueError('Conventional gasoline price table is missing its Silver contract columns')
    if not {'region', 'province', 'demand'}.issubset(sites.columns):
        raise ValueError('Gasoline demand regions require region, province and demand')
    if sites.region.isna().any() or sites.region.duplicated().any():
        raise ValueError('Gasoline demand regions must have unique non-null IDs')
    demand = pd.to_numeric(sites.demand, errors='coerce')
    if not np.isfinite(demand).all() or (demand < 0).any():
        raise ValueError('Gasoline demand must be finite and nonnegative')
    selected = sites.loc[demand > 0, ['region', 'province', 'demand']].copy()
    selected['demand'] = demand.loc[demand > 0]
    selected['province'] = selected.province.replace(ALIASES)
    prices = prices.copy()
    prices['province'] = prices.province.replace(ALIASES)
    if not set(selected.province) <= PROVINCES or not set(prices.province) <= PROVINCES:
        raise ValueError('Unrecognized or missing province in gasoline price mapping')
    if prices.duplicated(['province', 'period']).any():
        raise ValueError('Duplicate province-period gasoline prices')
    if (not prices.period.eq(model_period).all()
            or not prices.period_end.eq(model_end_year).all()
            or not prices.price_year.eq(model_end_year).all()):
        raise ValueError('Conventional gasoline prices do not match the Gold period')
    costs = pd.to_numeric(prices.cost, errors='coerce')
    if (not np.isfinite(costs).all() or (costs <= 0).any()
            or not prices.units.eq('CAD2020/t gasoline').all()
            or not prices.status.eq('normalized_with_explicit_assumptions').all()
            or not prices.price_boundary.eq('us_sector_delivered_including_embedded_taxes').all()):
        raise ValueError('Conventional gasoline prices require positive normalized CAD2020/t delivered costs')
    prices['cost'] = costs
    audit = selected.merge(prices[list(sorted(required))], on='province', how='left',
                           validate='many_to_one', indicator=True)
    missing = audit.loc[audit['_merge'].ne('both'), ['region', 'province']]
    if not missing.empty:
        raise ValueError(f'Unpriced gasoline demand regions: {missing.to_dict("records")}')
    return audit.drop(columns='_merge')


def conventional_cost_rows(audit, model_period, data_id):
    """Encode one unit-efficiency backup supply cost per demand node/vintage."""
    return pd.DataFrame({
        'region': audit.region, 'period': model_period, 'tech': 'GSL_BACKUP',
        'vintage': model_period, 'cost': audit.cost, 'units': audit.units,
        'notes': [f'Conventional gasoline delivered-price proxy; {p}; EIA {y}; embedded US taxes retained'
                  for p, y in zip(audit.province, audit.price_year)],
        'data_source': audit.source_series_id, 'data_id': data_id,
    })


def validate_encoded_gasoline(db, audit, model_period, share):
    """Check price coverage and national blend accounting before database export."""
    costs = db['cost_variable'].loc[db['cost_variable'].tech.eq('GSL_BACKUP')]
    demand = db['demand'].loc[db['demand'].period.eq(model_period)
                              & db['demand'].commodity.eq('d_gsl'), ['region', 'demand']]
    pd.testing.assert_frame_equal(demand.sort_values('region').reset_index(drop=True),
                                  audit[['region', 'demand']].sort_values('region').reset_index(drop=True),
                                  check_dtype=False)
    expected = conventional_cost_rows(audit, model_period, 'unused')
    keys = ['region', 'period', 'tech', 'vintage']
    if costs.duplicated(keys).any() or len(costs) != len(expected):
        raise ValueError('Gold conventional gasoline costs do not uniquely cover demand nodes')
    columns = keys + ['cost', 'units']
    pd.testing.assert_frame_equal(costs[columns].sort_values(keys).reset_index(drop=True),
                                  expected[columns].sort_values(keys).reset_index(drop=True),
                                  check_dtype=False)
    routes = db['efficiency'].loc[db['efficiency'].tech.eq('GSL_BACKUP')]
    if (set(routes.region) != set(audit.region) or len(routes) != len(audit)
            or not routes.input_comm.eq('ethos').all()
            or not routes.output_comm.eq('d_gsl').all()
            or not routes.vintage.eq(model_period).all()
            or not routes.efficiency.eq(1).all()):
        raise ValueError('Gold conventional gasoline requires unit-efficiency demand-node routes')
    from geocanoe.schema.fuel_policy import CONVENTIONAL_GROUP
    limits = db['limit_activity'].loc[db['limit_activity'].tech_or_group.eq(CONVENTIONAL_GROUP)]
    if share == 0:
        if not limits.empty:
            raise ValueError('Zero synthetic minimum must not cap conventional gasoline')
    elif (len(limits) != 1 or limits.region.iloc[0] != 'global'
          or limits.period.iloc[0] != model_period or limits.operator.iloc[0] != 'le'
          or not np.isclose(limits.activity.iloc[0], (1-share)*audit.demand.sum())):
        raise ValueError('Gold national synthetic-share cap does not match annual modeled demand')
    if share > 0:
        members = db['tech_group_member'].loc[db['tech_group_member'].group_name.eq(CONVENTIONAL_GROUP)]
        if len(members) != 1 or members.tech.tolist() != ['GSL_BACKUP']:
            raise ValueError('National gasoline cap must account for the conventional backup route')
