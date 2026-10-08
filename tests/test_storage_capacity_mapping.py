from pathlib import Path
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest
from pyomo.environ import ConcreteModel, Var, value
from temoa.components.limits import limit_activity_constraint

from geocanoe.config import load_geospatial_build_config
from geocanoe.geospatial.co2_storage import build_storage_capacity_mapping, select_storage_features
from geocanoe.schema.build import (
    load_empty_temoa_v4_tables, rebuild_storage_capacity_limits, validate_storage_build_settings,
)


def mapping_fixture() -> pd.DataFrame:
    crosswalk = pd.DataFrame({
        "storage_feature_id": ["F1", "F2", "F3", "F3", "F4"],
        "storage_unit_id": ["BC", "BC", "N1", "N1", "N2"],
        "region": ["R0", "R0", "R0", "R1", "R1"],
    })
    assessments = pd.DataFrame({
        "storage_assessment_id": ["A", "B", "C", "outside", "invalid"],
        "source_dataset": ["BC_STORAGE_ATLAS", "NATCARB", "NATCARB", "NATCARB", "ATLANTIC_COS"],
        "assessment_scope": ["unit", "feature", "feature", "feature", "feature"],
        "storage_feature_id": [None, "F3", "F4", "outside", "F1"],
        "storage_unit_id": ["BC", "N1", "N2", "outside", "BC"],
        "storage_p50_tonnes": [100, 60, 20, 500, None],
    })
    return build_storage_capacity_mapping(crosswalk, assessments)


def test_mapping_conserves_unique_assessments_and_deduplicates_unit_polygons():
    mapping = mapping_fixture()
    assert mapping.groupby("storage_assessment_id")["equal_weighted_p50_tonnes"].sum().to_dict() == {
        "A": 100, "B": 60, "C": 20,
    }
    assert mapping.groupby("region")["equal_weighted_p50_tonnes"].sum().to_dict() == {
        "R0": 130, "R1": 50,
    }


@pytest.mark.parametrize("mode", ["unlimited", "equal_weighted", "shared"])
def test_capacity_limits_encode_single_period_cumulative_budget(mode):
    db = load_empty_temoa_v4_tables()
    db["efficiency"] = pd.DataFrame({"region": ["R0", "R1"], "tech": ["CO2_INJECT"] * 2})
    # Preserve an existing minimum policy constraint alongside maximum capacity.
    db["limit_activity"] = pd.DataFrame(
        [{"region": "global", "period": 2025, "tech_or_group": "CO2_INJECT",
          "operator": "ge", "activity": 1}], columns=db["limit_activity"].columns,
    )
    rebuild_storage_capacity_limits(db, mapping_fixture(), mode, 2025, 25)
    limits = db["limit_activity"]
    assert limits.loc[limits.operator.eq("ge"), "activity"].tolist() == [1]
    upper = limits.loc[limits.operator.eq("le")].set_index("region").activity.to_dict()
    assert upper == {"unlimited": {}, "equal_weighted": {"R0": 5.2, "R1": 2.0},
                     "shared": {"global": 7.2}}[mode]


def test_corrupted_capacity_mapping_fails_closed():
    db = load_empty_temoa_v4_tables()
    db["efficiency"] = pd.DataFrame({"region": ["R0", "R1"], "tech": ["CO2_INJECT"] * 2})
    mapping = mapping_fixture()
    mapping.loc[0, "equal_weighted_p50_tonnes"] += 1
    with pytest.raises(ValueError, match="conserve"):
        rebuild_storage_capacity_limits(db, mapping, "shared", 2025, 25)
    with pytest.raises(ValueError, match="nonempty"):
        rebuild_storage_capacity_limits(db, None, "equal_weighted", 2025, 25)


@pytest.mark.parametrize("mode", ["unlimited", "equal_weighted", "shared"])
def test_config_capacity_modes(tmp_path: Path, mode: str):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('eligibility = "all_mapped"', 'eligibility = "quantitative"')
    source = source.replace('capacity_mapping = "unlimited"', f'capacity_mapping = "{mode}"')
    source = source.replace("use_capacity_bound = false", "")
    path = tmp_path / "capacity.toml"
    path.write_text(source, encoding="utf-8")
    config = load_geospatial_build_config(path)
    assert config.storage.capacity_mapping == mode
    assert config.storage.use_capacity_bound == (mode != "unlimited")


def test_capacity_mode_requires_quantitative(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('capacity_mapping = "unlimited"', 'capacity_mapping = "shared"')
    source = source.replace('[storage.quantitative]', '[storage.all_mapped]').replace(
        '[storage.all_mapped]\n# sources', '# sources'
    )
    path = tmp_path / "invalid.toml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="quantitative"):
        load_geospatial_build_config(path)


def test_temoa_shared_limit_sums_all_storage_regions():
    tech = "CO2_INJECT"
    variables = ConcreteModel()
    variables.injection = Var(["R0", "R1"], initialize=4.0)
    model = SimpleNamespace(
        regions=["R0", "R1"], tech_group_names=[], tech_annual={tech},
        group_active_processes={},
        process_vintages={(r, 2025, tech): [2025] for r in ["R0", "R1"]},
        process_inputs={(r, 2025, tech, 2025): ["co2"] for r in ["R0", "R1"]},
        process_outputs_by_input={(r, 2025, tech, 2025, "co2"): ["co2_stored"]
                                 for r in ["R0", "R1"]},
        v_flow_out_annual={(r, 2025, "co2", tech, 2025, "co2_stored"): variables.injection[r]
                          for r in ["R0", "R1"]},
        limit_activity={("global", 2025, tech, "le"): 7.2},
    )
    assert not value(limit_activity_constraint(model, "global", 2025, tech, "le"))
    variables.injection["R1"].set_value(3.0)
    assert value(limit_activity_constraint(model, "global", 2025, tech, "le"))


@pytest.mark.parametrize("sources", [("NATCARB",), ("BC_STORAGE_ATLAS",),
                                     ("NATCARB", "BC_STORAGE_ATLAS")])
def test_quantitative_source_selection_filters_before_mapping(sources):
    features = pd.DataFrame({
        "storage_feature_id": ["N", "BC", "Atlantic", "BC_no_p50"],
        "source_dataset": ["NATCARB", "BC_STORAGE_ATLAS", "ATLANTIC_COS", "BC_STORAGE_ATLAS"],
    })
    flags = pd.DataFrame({"storage_feature_id": features.storage_feature_id,
                          "has_p50_capacity": [True, True, False, False]})
    selected = select_storage_features(features, flags, "quantitative", sources)
    assert set(selected.source_dataset) == set(sources)
    assert set(selected.storage_feature_id).issubset({"N", "BC"})


@pytest.mark.parametrize("sources", [["NATCARB"], ["BC_STORAGE_ATLAS"],
                                     ["NATCARB", "BC_STORAGE_ATLAS"]])
def test_profile_accepts_quantitative_source_subsets(tmp_path, sources):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('eligibility = "all_mapped"',
                            'eligibility = "quantitative"\nsources = ' + json.dumps(sources))
    path = tmp_path / "sources.toml"
    path.write_text(source, encoding="utf-8")
    assert set(load_geospatial_build_config(path).storage.sources) == set(sources)


@pytest.mark.parametrize("sources", [[], ["unknown"], ["NATCARB", "NATCARB"], ["ATLANTIC_COS"]])
def test_profile_rejects_invalid_quantitative_sources(tmp_path, sources):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('eligibility = "all_mapped"',
                            'eligibility = "quantitative"\nsources = ' + json.dumps(sources))
    path = tmp_path / "sources.toml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="storage.sources"):
        load_geospatial_build_config(path)


def test_gold_rejects_different_silver_source_selection(tmp_path):
    path = tmp_path / "storage.gpkg"
    connection = sqlite3.connect(path)
    pd.DataFrame([{"eligibility": "quantitative", "sources": '["NATCARB"]'}]).to_sql(
        "storage_build_settings", connection, index=False,
    )
    connection.close()
    validate_storage_build_settings(path, "quantitative", ("NATCARB",))
    with pytest.raises(ValueError, match="rebuild Silver"):
        validate_storage_build_settings(path, "quantitative", ("BC_STORAGE_ATLAS",))


def test_nested_storage_options_follow_active_parent(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('eligibility = "all_mapped"', 'eligibility = "quantitative"')
    source = source.replace('capacity_mapping = "unlimited"',
                            'sources = ["BC_STORAGE_ATLAS"]\ncapacity_mapping = "shared"')
    path = tmp_path / "nested.toml"
    path.write_text(source, encoding="utf-8")
    config = load_geospatial_build_config(path)
    assert config.storage.sources == ("BC_STORAGE_ATLAS",)
    assert config.storage.capacity_mapping == "shared"
    source = source.replace('eligibility = "quantitative"', 'eligibility = "qualitative"')
    path.write_text(source, encoding="utf-8")
    config = load_geospatial_build_config(path)
    assert config.storage.sources == ("ATLANTIC_COS",)
    assert config.storage.capacity_mapping == "unlimited"


def test_nested_storage_rejects_duplicate_flat_options(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "config/build_profiles/sample_build_profile.toml").read_text(encoding="utf-8")
    source = source.replace('eligibility = "all_mapped"',
                            'eligibility = "quantitative"\ncapacity_mapping = "unlimited"')
    path = tmp_path / "duplicate.toml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="only once"):
        load_geospatial_build_config(path)
