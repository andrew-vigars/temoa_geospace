from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.config import load_geospatial_build_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "build_profiles"


@pytest.mark.parametrize(
    ("filename", "expected_label"),
    [
        ("atlantic.toml", "atlantic"),
        ("national.toml", "national"),
        ("on-qc.toml", "on_qc"),
        ("provinces_only.toml", "provinces_only"),
    ],
)
def test_committed_build_profiles_load(
    filename: str,
    expected_label: str,
) -> None:
    config = load_geospatial_build_config(PROFILE_DIR / filename)

    assert config.study_area.label == expected_label
    assert config.study_area.provinces
    assert config.schema.road_connection_method in (
        config.road_connectivity.methods
    )
    assert config.road_connectivity.road_layer in config.roads.networks
    assert config.storage.eligibility == "all_mapped"
    assert config.storage.use_capacity_bound is False


def test_profile_rejects_duplicate_provinces(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    invalid = source.replace('    "ON",', '    "ON",\n    "ON",', 1)
    path = tmp_path / "duplicate-province.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_geospatial_build_config(path)


def test_profile_requires_study_area_section(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    invalid = source.replace("[study_area]", "[renamed_study_area]", 1)
    path = tmp_path / "missing-study-area.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="study_area"):
        load_geospatial_build_config(path)


def test_profile_rejects_unknown_storage_eligibility(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "provinces_only.toml").read_text(encoding="utf-8")
    invalid = source.replace(
        'eligibility = "all_mapped"',
        'eligibility = "capacity_guess"',
        1,
    )
    path = tmp_path / "invalid-storage.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="storage.eligibility"):
        load_geospatial_build_config(path)
