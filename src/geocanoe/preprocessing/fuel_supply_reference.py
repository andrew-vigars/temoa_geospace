"""Reproduce CANOE's original price from GeoCANOE Bronze for comparison only.

Uses the sister repository's actual configuration and calculation functions.
Does not run its database aggregator or change GeoCANOE Gold inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pandas as pd

from geocanoe.paths import find_project_root
from geocanoe.acquisition.fuel_supply import validate_bronze


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_price(frame: pd.DataFrame, year: int) -> float:
    """Require exactly one positive transportation-gasoline observation."""
    values = pd.to_numeric(frame.loc[
        frame['Tech Name'].eq('T_gsl') & frame['period'].astype(int).eq(year), 'value'
    ], errors='raise')
    if len(values) != 1 or not values.map(lambda x: 0 < x < float('inf')).all():
        raise ValueError(f'Expected one finite positive T_gsl price for {year}')
    return float(values.iloc[0])


def build(config_path: Path, root: Path | None = None) -> Path:
    root = root or find_project_root()
    config_path = config_path.resolve()
    settings = tomllib.loads(config_path.read_text(encoding='utf-8'))
    repo = (root / settings['repo_dir']).resolve()
    upstream_config = repo / settings['upstream_config']
    revision = subprocess.check_output(['git', '-c', f'safe.directory={repo.as_posix()}', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != settings['expected_commit'] or sha256(upstream_config) != settings['expected_config_sha256']:
        raise ValueError('Upstream reference revision/configuration does not match pin')
    for name, digest in settings['expected_module_sha256'].items():
        if sha256(repo / 'canoe_fuel' / name) != digest:
            raise ValueError(f'Upstream reference module checksum mismatch: {name}')
    bronze_path, raw, provenance = validate_bronze(settings['bronze_config'], root)
    if settings['price_selection'] != 'period_end':
        raise ValueError('Only explicit period_end selection is implemented')
    start, end, year = (int(settings[k]) for k in ('period_start', 'period_end', 'price_year'))
    if end <= start or year != end:
        raise ValueError('Period-end pricing requires price_year == period_end > period_start')

    sys.path.insert(0, str(repo))
    try:
        common = importlib.import_module('canoe_fuel.common')
        setup = importlib.import_module('canoe_fuel.setup')
        costs = importlib.import_module('canoe_fuel.costvariable')
        for module in (common, setup, costs):
            if Path(module.__file__).resolve().parent.parent != repo:
                raise ValueError('Imported canoe_fuel does not match configured sister repo')
        cfg = common.CANOEFuelConfig.validate_from_toml(upstream_config)
        if cfg.eia_year != provenance['source']['aeo_year'] or provenance['source']['scenario'] != f'ref{cfg.eia_year}':
            raise ValueError('EIA Bronze edition/scenario does not match upstream config')
        frame = setup.build_cost_frame(raw, cfg)
        source_value = select_price(frame, year)
        frame['period'] = frame['period'].astype(int)
        frame['value'] = frame['value'].astype(float)
        price = costs._calc_cost('F_T_GSL', 'T_gsl', year, cost_df=frame, cfg=cfg)
    finally:
        sys.path.remove(str(repo))

    if not 0 < price < float('inf'):
        raise ValueError('Nonpositive/nonfinite upstream gasoline cost')
    output = root / settings['output_dir']
    output.mkdir(parents=True, exist_ok=True)
    # CANOE uses a common US-derived series across these jurisdictions. Do not
    # represent these rows as independently observed provincial prices.
    rows = [dict(
        province=p, period=start, period_end=end, period_years=end-start,
        price_year=year, commodity='T_gsl', source_value=source_value,
        source_units=cfg.eia_unit_filter, cost=price, units='2020 MCAD/PJ',
        price_selection=settings['price_selection'], status=settings['output_status'],
        geography_basis='common EIA US series; CANOE Canadian currency adjustment',
    ) for p in cfg.province_list if p != 'CAN']
    table = pd.DataFrame(rows)
    table.to_csv(output / 'gasoline_supply_prices.csv', index=False)
    table.to_parquet(output / 'gasoline_supply_prices.parquet', index=False)
    source_rows = raw[raw['unit'].eq(cfg.eia_unit_filter) & raw['seriesName'].str.contains('Motor Gasoline', case=False)]
    source_rows.to_csv(output / 'eia_gasoline_source_rows.csv', index=False)
    manifest = dict(
        stage='Silver upstream gasoline reference only', upstream_commit=revision,
        adapter_config=settings, upstream_config_sha256=sha256(upstream_config),
        upstream_functions={str(Path(m.__file__).name): sha256(Path(m.__file__)) for m in (common, setup, costs)},
        source=provenance, adapter_config_sha256=sha256(config_path),
        annual_basis='Unit fuel price; no period-duration multiplication. Demand remains annual.',
        missing_jurisdictions=sorted({'AB','BC','MB','NB','NL','NS','NT','NU','ON','PE','QC','SK','YT'} - {dict(NLLAB='NL', PEI='PE').get(p,p) for p in cfg.province_list if p != 'CAN'}),
        caveats=[
            'Upstream conversion multiplies USD/MMBtu by 1.055; dimensional USD/GJ conversion divides. Preserved unchanged for review.',
            'Sector-delivered price: do not add conventional delivery without reconciling boundary.',
            'Fuel taxes and embedded compliance costs require verification before Gold use.',
            'No emissions factors applied, no demand quantities modified, no Gold costs changed.',
            'Province rows share the same source price; this is not a regional wholesale model.',
        ],
    )
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=find_project_root() / 'config/canoe_fuel/reference.toml')
    args = parser.parse_args()
    print(build(args.config))


if __name__ == '__main__':
    main()
