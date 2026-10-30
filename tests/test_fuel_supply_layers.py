"""Bronze immutability and source-to-Silver gasoline conversion contracts."""
import json
from pathlib import Path
import re
import shutil

import pandas as pd
import pytest

from geocanoe.acquisition import fuel_supply as bronze
from geocanoe.preprocessing import fuel_supply as silver
from geocanoe.costs import economics


def setting(path, key, value):
    path.write_text(re.sub(rf'^{key} = .*$', f'{key} = {json.dumps(value)}',
                           path.read_text(), flags=re.M))


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    for relative in ['config/canoe_fuel/bronze.toml', 'config/canoe_fuel/silver.toml',
                     'registry/gasoline_properties.toml', 'registry/economics.toml']:
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, dest)
    shutil.copytree(root / 'registry/economics', tmp_path / 'registry/economics')
    data = [dict(period=str(year), scenario='ref2025', tableId='3', regionId='1-0',
                 seriesId='prce_real_trn_NA_mgs_NA_NA_y13dlrpmmbtu',
                 seriesName='Energy Prices : Transportation : Motor Gasoline',
                 unit='2024 $/MMBtu', value=str(year - 2030 if year > 2030 else 12))
            for year in range(2024, 2051)]
    payload = dict(response=dict(total=str(len(data)), data=data),
                   request=dict(command='/v2/aeo/2025/data/'))
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(payload))
    config = tmp_path / 'config/canoe_fuel/bronze.toml'
    setting(config, 'source_path', 'source.json')
    setting(config, 'source_sha256', bronze.sha256(source))
    monkeypatch.setattr(economics, 'find_project_root', lambda: tmp_path)
    return tmp_path


def acquire(root):
    return bronze.acquire_fuel_supply(root=root)


def mutate_source(root, change):
    source = root / 'source.json'
    payload = json.loads(source.read_text())
    change(payload)
    source.write_text(json.dumps(payload))
    setting(root / 'config/canoe_fuel/bronze.toml', 'source_sha256', bronze.sha256(source))


def test_offline_rebuild_has_complete_provenance_and_no_sibling_dependency(project):
    raw = acquire(project)
    source_bytes = (project / 'source.json').read_bytes()
    assert (raw / 'eia_aeo_table3.json').read_bytes() == source_bytes
    before = bronze.sha256(raw / 'eia_aeo_table3.json')
    (project / 'source.json').unlink()
    assert acquire(project) == raw  # Verified cache does not need CANOE_CLOUD present.
    output = silver.build(root=project)
    table = silver.validate_outputs(root=project)
    assert set(table.province) == silver.PROVINCES
    assert table.is_territorial_proxy.sum() == 3
    assert table.price_year.unique().tolist() == [2050]
    assert table.source_value.unique().tolist() == [20]
    assert table.cost.iloc[0] == pytest.approx(
        20 / 1.05505585262 * table.currency_multiplier.iloc[0] * 45.8)
    assert table.cost.iloc[0] / table.cost_cad2020_per_gj.iloc[0] == pytest.approx(45.8)
    assert len(pd.read_parquet(output / 'eia_table3.parquet')) == 27
    assert bronze.sha256(raw / 'eia_aeo_table3.json') == before
    first = (output / 'gasoline_supply_prices.csv').read_bytes()
    silver.build(root=project)
    assert (output / 'gasoline_supply_prices.csv').read_bytes() == first


@pytest.mark.parametrize('change,match', [
    (lambda p: p['response'].update(total='999'), 'truncated'),
    (lambda p: p['request'].update(command='/v2/aeo/2026/data/'), 'edition'),
    (lambda p: p['response']['data'][0].update(scenario='other'), 'scope'),
    (lambda p: p['response']['data'][0].update(tableId='4'), 'scope'),
    (lambda p: p['response']['data'][0].update(regionId='2-0'), 'scope'),
    (lambda p: p['response']['data'].__setitem__(0, p['response']['data'][1].copy()), 'duplicated'),
    (lambda p: p['response']['data'][0].update(period='2023'), 'years'),
])
def test_bronze_rejects_wrong_or_incomplete_source(project, change, match):
    mutate_source(project, change)
    with pytest.raises(ValueError, match=match):
        acquire(project)


def test_bronze_rejects_changed_pin_and_corruption(project):
    raw = acquire(project)
    path = raw / 'eia_aeo_table3.json'
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='mismatch'):
        acquire(project)
    with pytest.raises(ValueError, match='mismatch'):
        silver.build(root=project)


def test_unmanifested_bronze_is_not_adopted(project):
    raw = acquire(project)
    (raw / 'manifest.json').unlink()
    with pytest.raises(FileNotFoundError):
        acquire(project)


@pytest.mark.parametrize('key,value,match', [
    ('price_year', 2030, 'Period-end'),
    ('period_end', 2025, 'Period-end'),
    ('source_price_year', 2025, 'source basis'),
    ('source_currency', 'CAD', 'source basis'),
    ('province_list', ['NL', 'NLLAB'], 'unique'),
    ('province_list', ['ZZ'], 'recognized'),
    ('territorial_price_policy', 'error', 'Territorial'),
    ('price_boundary', 'wholesale', 'boundary'),
])
def test_silver_rejects_ambiguous_basis(project, key, value, match):
    acquire(project)
    setting(project / 'config/canoe_fuel/silver.toml', key, value)
    with pytest.raises(ValueError, match=match):
        silver.build(root=project)


def test_aliases_and_province_only_coverage_are_explicit(project):
    acquire(project)
    config = project / 'config/canoe_fuel/silver.toml'
    setting(config, 'province_list', ['NLLAB', 'PEI'])
    setting(config, 'territorial_price_policy', 'error')
    output = silver.build(root=project)
    assert silver.validate_outputs(root=project).province.tolist() == ['NL', 'PE']
    record = json.loads((output / 'manifest.json').read_text())
    assert 'NT' in record['missing_jurisdictions']


@pytest.mark.parametrize('value', ['0', 'nan', 'inf', '-1'])
def test_silver_rejects_invalid_source_price(project, value):
    mutate_source(project, lambda p: p['response']['data'][-1].update(value=value))
    acquire(project)
    with pytest.raises(ValueError, match='positive price'):
        silver.build(root=project)


def test_silver_rejects_changed_physical_input_and_output_tampering(project):
    acquire(project)
    output = silver.build(root=project)
    properties = project / 'registry/gasoline_properties.toml'
    setting(properties, 'heating_value_gj_per_tonne', 44)
    with pytest.raises(ValueError, match='dependencies changed'):
        silver.validate_outputs(root=project)
    silver.build(root=project)
    (output / 'gasoline_supply_prices.csv').write_text('corrupt')
    with pytest.raises(ValueError, match='checksum mismatch'):
        silver.validate_outputs(root=project)


def test_shared_economics_tampering_is_rejected(project):
    acquire(project)
    path = project / 'registry/economics/currency_exchange.csv'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='checksum mismatch'):
        silver.build(root=project)


def test_dimensional_conversion_has_no_million_or_period_factor():
    energy, mass = silver.normalize_price(10, 2, 40, 1.5)
    assert energy == 7.5
    assert mass == 300
    for bad in [0, -1, float('nan'), float('inf')]:
        with pytest.raises(ValueError):
            silver.normalize_price(10, bad, 40, 1.5)


def test_registered_bronze_and_silver_stages():
    from geocanoe.execution import bronze as workflow_bronze, silver as workflow_silver
    assert workflow_bronze.STAGE_MODULE_NAMES['fuel_supply'] == bronze.__name__
    assert 'fuel_supply' in workflow_bronze.BRONZE_STAGE_ORDER
    assert workflow_silver.STAGE_MODULE_NAMES['fuel_supply'] == silver.__name__
    assert workflow_silver.SILVER_STAGE_DEPENDENCIES['fuel_supply'] == ()
    assert 'fuel_supply' in workflow_silver.SILVER_STAGE_ORDER
