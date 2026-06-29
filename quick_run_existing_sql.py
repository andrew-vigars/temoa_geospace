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

SCHEMA_DIR = (
    PROJECT_ROOT
    / "data_files"
    / "processed"
    / "schema"
)


# =============================================================================
# Select encoded SQLite schema
# =============================================================================

database_schemas = sorted(
    SCHEMA_DIR.glob("*.sqlite")
)

if not database_schemas:
    raise FileNotFoundError(
        f"No SQLite schemas found in {SCHEMA_DIR}"
    )

print("\nAvailable schemas:")

for i, path in enumerate(database_schemas):
    print(f"[{i}] {path.name}")

while True:

    try:
        selection = int(
            input("\nSelect schema number: ")
        )

        if 0 <= selection < len(database_schemas):
            break

        print("Invalid selection.")

    except ValueError:
        print("Please enter an integer.")

DB_PATH = database_schemas[selection]

schema_name = DB_PATH.stem.replace(
    "CANOE_geospatial_",
    "",
)

OUTPUT_DIR = (
    OUTPUT_ROOT
    / f"{datetime.today().strftime('%Y-%m-%d_%H%M')}_{schema_name}"
)

print(f"\nSelected schema: {DB_PATH.name}")


# =============================================================================
# Validate inputs
# =============================================================================

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