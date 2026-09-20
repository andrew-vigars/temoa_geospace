from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.config import load_geospatial_build_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "build_profiles"


@pytest.mark.parametrize(
    ("filename", "expected_label", "expected_id", "expected_grid_types"),
    [
        ("atlantic.toml", "atlantic", "atlantic", ("geographic", "projected")),
        ("national.toml", "national", "national", ("geographic", "projected")),
        ("on-qc.toml", "on_qc", "onqc", ("geographic", "projected")),
        # provinces_only is the lightweight sample profile: it only generates
        # projected (km) basemaps, not the 0.5deg geographic grid.
        ("provinces_only.toml", "provinces_only", "provinces", ("projected",)),
    ],
)
def test_committed_build_profiles_load(
    filename: str,
    expected_label: str,
    expected_id: str,
    expected_grid_types: tuple[str, ...],
) -> None:
    config = load_geospatial_build_config(PROFILE_DIR / filename)

    assert config.study_area.label == expected_label
    assert config.build_id == expected_id
    assert config.study_area.provinces
    assert config.basemaps.grid_types == expected_grid_types
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


def test_profile_rejects_long_artifact_id(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    invalid = source.replace('id = "onqc"', 'id = "identifier-is-too-long"', 1)
    path = tmp_path / "invalid-id.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="1-16"):
        load_geospatial_build_config(path)


def test_profile_derives_projected_grid_type_when_geographic_is_empty(
    tmp_path: Path,
) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    projected_only = source.replace(
        "resolutions = [\n    0.5,\n]",
        "resolutions = []",
        1,
    )
    path = tmp_path / "projected-only.toml"
    path.write_text(projected_only, encoding="utf-8")

    config = load_geospatial_build_config(path)

    assert config.basemaps.geographic_resolutions_deg == ()
    assert config.basemaps.projected_resolutions_km == (10.0, 25.0, 50.0)
    assert config.basemaps.grid_types == ("projected",)


def test_profile_derives_geographic_grid_type_when_projected_is_empty(
    tmp_path: Path,
) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    geographic_only = source.replace(
        "resolutions_km = [\n    10,\n    25,\n    50,\n]",
        "resolutions_km = []",
        1,
    )
    path = tmp_path / "geographic-only.toml"
    path.write_text(geographic_only, encoding="utf-8")

    config = load_geospatial_build_config(path)

    assert config.basemaps.geographic_resolutions_deg == (0.5,)
    assert config.basemaps.projected_resolutions_km == ()
    assert config.basemaps.grid_types == ("geographic",)


def test_profile_rejects_no_basemap_resolutions(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    no_resolutions = source.replace(
        "resolutions = [\n    0.5,\n]",
        "resolutions = []",
        1,
    ).replace(
        "resolutions_km = [\n    10,\n    25,\n    50,\n]",
        "resolutions_km = []",
        1,
    )
    path = tmp_path / "no-basemaps.toml"
    path.write_text(no_resolutions, encoding="utf-8")

    with pytest.raises(ValueError, match="At least one basemap resolution"):
        load_geospatial_build_config(path)


def test_profile_rejects_redundant_grid_types_setting(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "on-qc.toml").read_text(encoding="utf-8")
    redundant = source.replace(
        "[basemaps]",
        '[basemaps]\ngrid_types = ["projected"]',
        1,
    )
    path = tmp_path / "redundant-grid-types.toml"
    path.write_text(redundant, encoding="utf-8")

    with pytest.raises(ValueError, match="grid_types is derived"):
        load_geospatial_build_config(path)
