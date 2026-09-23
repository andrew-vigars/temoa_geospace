from __future__ import annotations

from geocanoe import paths
from geocanoe import regions
from geocanoe import registry
from geocanoe.geospatial import basemaps
from geocanoe.preprocessing import gasoline_demand
from geocanoe.preprocessing import legacy_inputs


def test_registry_reexports_canonical_project_root_finder() -> None:
    assert registry.find_project_root is paths.find_project_root


def test_gasoline_demand_reexports_canonical_region_mappings() -> None:
    assert gasoline_demand.PRUID_TO_CODE is regions.PRUID_TO_CODE
    assert gasoline_demand.PROVINCE_NAME_TO_CODE is regions.PROVINCE_NAME_TO_CODE
    assert basemaps.PROVINCE_NAME_TO_CODE is regions.PROVINCE_NAME_TO_CODE
    assert legacy_inputs.PROVINCE_NAME_TO_CODE is regions.PROVINCE_NAME_TO_CODE
