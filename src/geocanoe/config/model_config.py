"""Load canonical non-spatial model settings from the registry TOML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


SUPPORTED_STORAGE_REQUIREMENTS = {
    "none",
    "minimum_annual_activity",
}


@dataclass(frozen=True)
class ModelTimeConfig:
    """Single optimization-period horizon defined by start and end years."""

    start_year: int
    end_year: int

    @property
    def period_years(self) -> int:
        """Return the duration of the single optimization period."""

        return self.end_year - self.start_year


@dataclass(frozen=True)
class ModelFinanceConfig:
    """Discounting and default financing assumptions."""

    global_discount_rate: float
    default_loan_rate: float


@dataclass(frozen=True)
class ModelStorageConfig:
    """Policy requirement for annual geological CO2 injection."""

    requirement: str
    minimum_annual_activity: float


@dataclass(frozen=True)
class ModelConfig:
    """Validated canonical model settings loaded from ``registry/model.toml``."""

    time: ModelTimeConfig
    finance: ModelFinanceConfig
    storage: ModelStorageConfig
    source_path: Path


def _require_table(raw: dict, name: str) -> dict:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration section [{name}] is missing or invalid.")
    return value


def _require_int(table: dict, key: str, section: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"[{section}].{key} must be an integer.")
    return value


def _require_rate(table: dict, key: str, section: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"[{section}].{key} must be a number.")
    value = float(value)
    if not 0 <= value < 1:
        raise ValueError(f"[{section}].{key} must satisfy 0 <= rate < 1.")
    return value


def _require_nonnegative_number(table: dict, key: str, section: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"[{section}].{key} must be a number.")
    value = float(value)
    if value < 0:
        raise ValueError(f"[{section}].{key} must be nonnegative.")
    return value


def load_model_config(config_path: Path | str) -> ModelConfig:
    """Load and validate the canonical single-period model registry."""

    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Model registry configuration not found: {config_path}")

    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    time_raw = _require_table(raw, "time")
    finance_raw = _require_table(raw, "finance")
    storage_raw = _require_table(raw, "storage")

    start_year = _require_int(time_raw, "start_year", "time")
    end_year = _require_int(time_raw, "end_year", "time")
    if end_year <= start_year:
        raise ValueError("[time].end_year must be greater than [time].start_year.")

    requirement = storage_raw.get("requirement")
    if requirement not in SUPPORTED_STORAGE_REQUIREMENTS:
        raise ValueError(
            "[storage].requirement must be one of "
            f"{sorted(SUPPORTED_STORAGE_REQUIREMENTS)}."
        )
    minimum_annual_activity = _require_nonnegative_number(
        storage_raw,
        "minimum_annual_activity",
        "storage",
    )
    if requirement == "none" and minimum_annual_activity != 0:
        raise ValueError(
            "[storage].minimum_annual_activity must be 0 when the requirement "
            "is 'none'."
        )
    if requirement == "minimum_annual_activity" and minimum_annual_activity <= 0:
        raise ValueError(
            "[storage].minimum_annual_activity must be greater than 0 when the "
            "requirement is 'minimum_annual_activity'."
        )

    return ModelConfig(
        time=ModelTimeConfig(start_year=start_year, end_year=end_year),
        finance=ModelFinanceConfig(
            global_discount_rate=_require_rate(
                finance_raw,
                "global_discount_rate",
                "finance",
            ),
            default_loan_rate=_require_rate(
                finance_raw,
                "default_loan_rate",
                "finance",
            ),
        ),
        storage=ModelStorageConfig(
            requirement=requirement,
            minimum_annual_activity=minimum_annual_activity,
        ),
        source_path=config_path,
    )
