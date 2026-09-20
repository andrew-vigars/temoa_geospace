from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.config import load_model_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_CONFIG_PATH = PROJECT_ROOT / "registry" / "model.toml"
BASELINE_SCENARIO_PATH = (
    PROJECT_ROOT / "registry" / "scenarios" / "sample_scenario.toml"
)


def test_committed_model_registry_is_single_period_2025_to_2050() -> None:
    config = load_model_config(MODEL_CONFIG_PATH, BASELINE_SCENARIO_PATH)

    assert config.time.start_year == 2025
    assert config.time.end_year == 2050
    assert config.time.period_years == 25
    assert config.emissions.projection_method == "constant"
    assert config.finance.global_discount_rate == 0.03
    assert config.finance.default_loan_rate == 0.03
    assert config.storage.requirement == "none"
    assert config.storage.minimum_cumulative_activity == 0.0
    assert config.basemap.grid_type == "projected"
    assert config.basemap.resolution == 25.0
    assert config.scenario.scenario_id == "baseline-25km"


def test_scenario_overrides_global_model_defaults(tmp_path: Path) -> None:
    scenario_path = tmp_path / "required-storage.toml"
    scenario_path.write_text(
        """[scenario]
id = "required-storage"
description = "Require cumulative storage activity"

[storage]
requirement = "minimum_cumulative_activity"
minimum_cumulative_activity = 7_500_000_000
""",
        encoding="utf-8",
    )

    config = load_model_config(MODEL_CONFIG_PATH, scenario_path)

    assert config.time.period_years == 25
    assert config.emissions.projection_method == "constant"
    assert config.storage.requirement == "minimum_cumulative_activity"
    assert config.storage.minimum_cumulative_activity == 7_500_000_000.0
    assert config.scenario.scenario_id == "required-storage"


def test_scenario_overrides_basemap_resolution(tmp_path: Path) -> None:
    scenario_path = tmp_path / "coarse-resolution.toml"
    scenario_path.write_text(
        """[scenario]
id = "coarse-resolution"
description = "Build Gold from the 40km provinces_only basemap"

[basemap]
grid_type = "projected"
resolution = 40
""",
        encoding="utf-8",
    )

    config = load_model_config(MODEL_CONFIG_PATH, scenario_path)

    assert config.basemap.grid_type == "projected"
    assert config.basemap.resolution == 40.0
    assert config.scenario.scenario_id == "coarse-resolution"


def test_model_registry_loads_minimum_cumulative_storage_policy(
    tmp_path: Path,
) -> None:
    source = MODEL_CONFIG_PATH.read_text(encoding="utf-8")
    configured = source.replace(
        'requirement = "none"',
        'requirement = "minimum_cumulative_activity"',
        1,
    ).replace(
        "minimum_cumulative_activity = 0",
        "minimum_cumulative_activity = 7_500_000_000",
        1,
    )
    path = tmp_path / "model.toml"
    path.write_text(configured, encoding="utf-8")

    config = load_model_config(path)

    assert config.storage.requirement == "minimum_cumulative_activity"
    assert config.storage.minimum_cumulative_activity == 7_500_000_000.0


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("end_year = 2050", "end_year = 2025", "end_year"),
        (
            'requirement = "none"',
            'requirement = "target"',
            "requirement",
        ),
        (
            'projection_method = "constant"',
            'projection_method = "linear"',
            "projection_method",
        ),
        (
            "minimum_cumulative_activity = 0",
            "minimum_cumulative_activity = -1.0",
            "nonnegative",
        ),
        (
            "global_discount_rate = 0.03",
            "global_discount_rate = 1.0",
            "rate",
        ),
        (
            'grid_type = "projected"',
            'grid_type = "utm"',
            "grid_type",
        ),
        (
            "resolution = 25",
            "resolution = 0",
            "resolution",
        ),
    ],
)
def test_model_registry_rejects_invalid_settings(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    source = MODEL_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "invalid-model.toml"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_model_config(path)
