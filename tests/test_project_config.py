from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.config import load_geospatial_build_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "build_profiles"


def test_committed_sample_build_profile_loads() -> None:
    config = load_geospatial_build_config(
        PROFILE_DIR / "sample_build_profile.toml"
    )

    assert config.study_area.label == "provinces_only"
    assert config.build_id == "provinces"
    assert config.study_area.provinces
    assert config.basemaps.grid_types == ("projected",)
    assert config.schema.road_connection_method in (
        config.road_connectivity.methods
    )
    assert config.road_connectivity.road_layer in config.roads.networks
    assert config.storage.eligibility == "all_mapped"
    assert config.storage.use_capacity_bound is False
    assert config.hydrography.enabled is True
    assert config.hydrography.polygons.classes == ("lake",)
    assert config.hydrography.polygons.minimum_source_measure == {"lake": 50.0}
    assert config.hydrography.permanency == (
        "unknown",
        "permanent",
        "intermittent",
    )
    assert config.hydrography.lines.enabled is False


def test_profile_rejects_duplicate_provinces(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "sample_build_profile.toml").read_text(
        encoding="utf-8"
    )
    invalid = source.replace('    "ON",', '    "ON",\n    "ON",', 1)
    path = tmp_path / "duplicate-province.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_geospatial_build_config(path)


def test_profile_requires_study_area_section(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "sample_build_profile.toml").read_text(
        encoding="utf-8"
    )
    invalid = source.replace("[study_area]", "[renamed_study_area]", 1)
    path = tmp_path / "missing-study-area.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="study_area"):
        load_geospatial_build_config(path)


def test_profile_rejects_unknown_storage_eligibility(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "sample_build_profile.toml").read_text(
        encoding="utf-8"
    )
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
    source = (PROFILE_DIR / "sample_build_profile.toml").read_text(
        encoding="utf-8"
    )
    invalid = source.replace(
        'id = "provinces"', 'id = "identifier-is-too-long"', 1
    )
    path = tmp_path / "invalid-id.toml"
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="1-16"):
        load_geospatial_build_config(path)


def test_profile_rejects_redundant_grid_types_setting(tmp_path: Path) -> None:
    source = (PROFILE_DIR / "sample_build_profile.toml").read_text(
        encoding="utf-8"
    )
    redundant = source.replace(
        "[basemaps]",
        '[basemaps]\ngrid_types = ["projected"]',
        1,
    )
    path = tmp_path / "redundant-grid-types.toml"
    path.write_text(redundant, encoding="utf-8")

    with pytest.raises(ValueError, match="grid_types is derived"):
        load_geospatial_build_config(path)


# Grid-type derivation is exercised against a minimal inline profile rather
# than a committed build profile, so it stays valid regardless of which grid
# families the committed sample_build_profile.toml happens to declare.
_DUAL_GRID_FAMILY_PROFILE = """
[profile]
id = "dualgrid"

[study_area]
label = "dual_grid"
provinces = ["ON"]

[basemaps]
keep_method = "centroid"
coordinate_precision = 6

[basemaps.geographic]
resolutions = [0.5]

[basemaps.projected]
resolutions_km = [25, 50]

[adjacency]
method = "rook"
coordinate_precision = 6
no_neighbor_id = "R-999"

[hydrography]
source_id = "nrcan_nhn_hhyd_national_en"
enabled = false
output_crs = "EPSG:3347"
permanency = ["permanent", "intermittent"]

[hydrography.polygons]
enabled = false
classes = []

[hydrography.polygons.minimum_source_area_km2]

[hydrography.lines]
enabled = false
classes = []

[hydrography.lines.minimum_source_length_km]

[roads]
networks = ["backbone"]
export_individual_provinces = false
output_crs = "EPSG:4326"

[roads.classes]
backbone = ["Freeway"]
primary_freight = ["Freeway"]
freight_access = ["Freeway"]

[road_connectivity]
road_layer = "backbone"
methods = ["strong"]
plot_outputs = false

[schema]
road_connection_method = "strong"
interactive_basemap_selection = true
boundary_buffer_km = 50
boundary_simplify_tolerance_km = 5
max_snap_distance_factor = 2.0

[storage]
eligibility = "all_mapped"
use_capacity_bound = false
"""


def test_profile_derives_projected_grid_type_when_geographic_is_empty(
    tmp_path: Path,
) -> None:
    projected_only = _DUAL_GRID_FAMILY_PROFILE.replace(
        "resolutions = [0.5]",
        "resolutions = []",
        1,
    )
    path = tmp_path / "projected-only.toml"
    path.write_text(projected_only, encoding="utf-8")

    config = load_geospatial_build_config(path)

    assert config.basemaps.geographic_resolutions_deg == ()
    assert config.basemaps.projected_resolutions_km == (25.0, 50.0)
    assert config.basemaps.grid_types == ("projected",)


def test_profile_derives_geographic_grid_type_when_projected_is_empty(
    tmp_path: Path,
) -> None:
    geographic_only = _DUAL_GRID_FAMILY_PROFILE.replace(
        "resolutions_km = [25, 50]",
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
    no_resolutions = _DUAL_GRID_FAMILY_PROFILE.replace(
        "resolutions = [0.5]",
        "resolutions = []",
        1,
    ).replace(
        "resolutions_km = [25, 50]",
        "resolutions_km = []",
        1,
    )
    path = tmp_path / "no-basemaps.toml"
    path.write_text(no_resolutions, encoding="utf-8")

    with pytest.raises(ValueError, match="At least one basemap resolution"):
        load_geospatial_build_config(path)
