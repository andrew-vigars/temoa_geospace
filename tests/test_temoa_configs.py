from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from geocanoe.execution.temoa_config import (
    default_config_path,
    generate_temoa_config,
    temoa_tutorial_config_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMOA_CONFIG_DIR = PROJECT_ROOT / "config" / "temoav4"
TEMOA_CONFIGS = sorted(TEMOA_CONFIG_DIR.rglob("*.toml"))

TUTORIAL_ROOT_KEYS = {
    "scenario",
    "scenario_mode",
    "extensions",
    "input_database",
    "output_database",
    "price_check",
    "source_trace",
    "plot_commodity_network",
    "check_units",
    "cycle_count_limit",
    "cycle_length_limit",
    "neos",
    "solver",
    "save_excel",
    "save_duals",
    "save_storage_levels",
    "save_lp_file",
    "graphviz_output",
    "output_threshold_capacity",
    "output_threshold_activity",
    "output_threshold_emission",
    "output_threshold_cost",
    "time_sequencing",
    "days_per_period",
    "reserve_margin",
    "sqlite",
    "MGA",
    "myopic",
    "morris",
    "SVMGA",
    "monte_carlo",
}


def test_default_generated_config_maps_to_temoav4_directory() -> None:
    assert default_config_path() == TEMOA_CONFIG_DIR / "config_sample.toml"


def test_packaged_temoa_tutorial_is_the_generation_source(tmp_path: Path) -> None:
    output_path = tmp_path / "config_sample.toml"

    generated = generate_temoa_config(output_path)

    assert generated == output_path.resolve()
    assert "Configuration file for a Temoa Run" in temoa_tutorial_config_text()
    config = tomllib.loads(generated.read_text(encoding="utf-8"))
    expected = tomllib.loads(temoa_tutorial_config_text())
    expected.update(
        {
            "extensions": ["eos"],
            "input_database": (
                "data_files/processed/schema/CANOE_geospatial.sqlite"
            ),
            "output_database": (
                "data_files/processed/schema/CANOE_geospatial.sqlite"
            ),
            "solver": {"name": "gurobi", "options": {"MIPGap": 0.01}},
            "save_duals": False,
            "check_units": False,
        }
    )

    assert config == expected
    assert set(config) == TUTORIAL_ROOT_KEYS
    assert config["extensions"] == ["eos"]
    assert config["solver"] == {
        "name": "gurobi",
        "options": {"MIPGap": 0.01},
    }
    assert config["save_duals"] is False
    assert config["check_units"] is False
    assert config["input_database"] == (
        "data_files/processed/schema/CANOE_geospatial.sqlite"
    )
    assert config["output_database"] == config["input_database"]
    assert "solver_name" not in config
    assert "solver_options" not in config


def test_generated_config_requires_force_to_replace(tmp_path: Path) -> None:
    output_path = tmp_path / "config_sample.toml"
    output_path.write_text("user-owned\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--force"):
        generate_temoa_config(output_path)

    generate_temoa_config(output_path, force=True)
    assert "Configuration file for a Temoa Run" in output_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("config_path", TEMOA_CONFIGS, ids=lambda path: path.stem)
def test_temoa_v4_config_matches_tutorial_contract(config_path: Path) -> None:
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    assert set(config) == TUTORIAL_ROOT_KEYS
    assert config["scenario_mode"] == "perfect_foresight"
    assert config["extensions"] == ["eos"]
    assert config["solver"] == {
        "name": "gurobi",
        "options": {"MIPGap": 0.01},
    }
    assert config["save_duals"] is False
    assert config["check_units"] is False
    assert config["days_per_period"] == 365
    assert config["sqlite"] == {
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "mmap_size": 8589934592,
        "cache_size": -512000,
    }
    assert "solver_name" not in config
    assert "solver_options" not in config
