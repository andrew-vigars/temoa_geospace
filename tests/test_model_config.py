from __future__ import annotations

from pathlib import Path

import pytest

from geocanoe.config import load_model_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_CONFIG_PATH = PROJECT_ROOT / "registry" / "model.toml"


def test_committed_model_registry_is_single_period_2025_to_2050() -> None:
    config = load_model_config(MODEL_CONFIG_PATH)

    assert config.time.start_year == 2025
    assert config.time.end_year == 2050
    assert config.time.period_years == 25
    assert config.finance.global_discount_rate == 0.03
    assert config.finance.default_loan_rate == 0.03
    assert config.storage.requirement == "none"
    assert config.storage.minimum_annual_activity == 0.0


def test_model_registry_loads_minimum_annual_storage_policy(
    tmp_path: Path,
) -> None:
    source = MODEL_CONFIG_PATH.read_text(encoding="utf-8")
    configured = source.replace(
        'requirement = "none"',
        'requirement = "minimum_annual_activity"',
        1,
    ).replace(
        "minimum_annual_activity = 0.0",
        "minimum_annual_activity = 25000000.0",
        1,
    )
    path = tmp_path / "model.toml"
    path.write_text(configured, encoding="utf-8")

    config = load_model_config(path)

    assert config.storage.requirement == "minimum_annual_activity"
    assert config.storage.minimum_annual_activity == 25_000_000.0


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
            "minimum_annual_activity = 0.0",
            "minimum_annual_activity = -1.0",
            "nonnegative",
        ),
        (
            "global_discount_rate = 0.03",
            "global_discount_rate = 1.0",
            "rate",
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
