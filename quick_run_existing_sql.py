# =============================================================================
# quick_run_existing_sql.py
#
# Run CANOE/TEMOA using an existing SQLite database.
# This script does not rebuild the database from CSVs or model_run.py.
# =============================================================================

import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from db_mgmt import update_db_paths


# =============================================================================
# Scenario settings
# =============================================================================

ACTIVE_SCHEMA = "weak_1deg"

DATABASE_SCHEMAS = {
    "baseline": Path("data_files") / "CANOE_geospatial.sqlite",
    "weak_1deg": Path("data_files") / "processed" / "schema" / "CANOE_geospatial_1deg_graph_roads_weak.sqlite",
    "strong_1deg": Path("data_files") / "processed" / "schema" / "CANOE_geospatial_1deg_graph_roads_strong.sqlite",
}


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent

MAIN_PATH = PROJECT_ROOT / "temoa" / "main.py"

CONFIG_PATH = (
    PROJECT_ROOT
    / "temoa"
    / "data_files"
    / "my_configs"
    / "config_sample.toml"
)

OUTPUT_ROOT = PROJECT_ROOT / "output_files"

DB_PATH = PROJECT_ROOT / DATABASE_SCHEMAS[ACTIVE_SCHEMA]

OUTPUT_DIR = (
    OUTPUT_ROOT
    / f"{datetime.today().strftime('%Y-%m-%d_%H%M')}_{ACTIVE_SCHEMA}"
)


# =============================================================================
# Validate inputs
# =============================================================================

if ACTIVE_SCHEMA not in DATABASE_SCHEMAS:
    raise ValueError(f"Unknown ACTIVE_SCHEMA: {ACTIVE_SCHEMA}")

if not DB_PATH.exists():
    raise FileNotFoundError(f"Input database not found: {DB_PATH}")

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

if not MAIN_PATH.exists():
    raise FileNotFoundError(f"TEMOA main.py not found: {MAIN_PATH}")


# =============================================================================
# Prepare run directory
# =============================================================================

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =============================================================================
# Point config to selected existing database
# =============================================================================

update_db_paths(
    CONFIG_PATH,
    str(DB_PATH),
    create_backup=True,
)


# =============================================================================
# Archive input database before solve
# =============================================================================

shutil.copy2(
    DB_PATH,
    OUTPUT_DIR / DB_PATH.name,
)


# =============================================================================
# Run CANOE/TEMOA
# =============================================================================

subprocess.run(
    [
        sys.executable,
        str(MAIN_PATH),
        "--config",
        str(CONFIG_PATH),
        "-o",
        str(OUTPUT_DIR),
    ],
    check=True,
)


# =============================================================================
# Archive database after solve
# =============================================================================

shutil.copy2(
    DB_PATH,
    OUTPUT_DIR / DB_PATH.name,
)

print(f"Run complete: {OUTPUT_DIR}")