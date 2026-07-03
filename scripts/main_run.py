# =============================================================================
# main_run.py
#
# Run CANOE/TEMOA from an existing encoded SQLite database.
#
# This script assumes schema construction has already happened upstream through
# the geospatial preprocessing pipeline and build_schema.py. It does not rebuild
# the database. It only selects a database, selects a config file, points the
# config to the selected database, creates a timestamped output folder, runs
# TEMOA, and archives the database before and after the solve.
# =============================================================================

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# =============================================================================
# Project discovery
# =============================================================================

def find_project_root() -> Path:
    """Return repository root from script location or current working directory."""
    search_starts = [
        Path(__file__).resolve(),
        Path.cwd().resolve(),
    ]

    for start in search_starts:
        for candidate in [start, *start.parents]:
            if (
                (candidate / "temoa" / "main.py").exists()
                and (candidate / "data_files").exists()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate project root. Expected to find temoa/main.py and data_files/."
    )


PROJECT_ROOT = find_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

from db_mgmt import update_db_paths

MAIN_PATH = PROJECT_ROOT / "temoa" / "main.py"
CONFIG_DIR = PROJECT_ROOT / "temoa" / "data_files" / "my_configs"
SCHEMA_DIR = PROJECT_ROOT / "data_files" / "processed" / "schema"
OUTPUT_ROOT = PROJECT_ROOT / "output_files"


# =============================================================================
# CLI helpers
# =============================================================================

def print_header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def select_file(options: list[Path], label: str) -> Path:
    if not options:
        raise FileNotFoundError(f"No {label} files found.")

    print_header(f"Select {label}")

    for i, path in enumerate(options):
        size_mb = path.stat().st_size / 1e6 if path.is_file() else 0.0
        suffix = f"  ({size_mb:.1f} MB)" if size_mb > 0 else ""
        print(f"[{i}] {path.name}{suffix}")

    while True:
        choice = input(f"\nEnter {label} number: ").strip()

        try:
            idx = int(choice)
        except ValueError:
            print("Please enter an integer.")
            continue

        if 0 <= idx < len(options):
            return options[idx]

        print("Invalid selection.")


def safe_name(path: Path) -> str:
    return path.stem.replace("CANOE_geospatial_", "").replace(" ", "_")


def validate_required_paths(db_path: Path, config_path: Path) -> None:
    required = {
        "TEMOA main script": MAIN_PATH,
        "selected SQLite database": db_path,
        "selected config file": config_path,
    }

    missing = [f"{label}: {path}" for label, path in required.items() if not path.exists()]

    if missing:
        print("\nMissing required paths:")
        for item in missing:
            print(f"  - {item}")
        raise FileNotFoundError("One or more required paths are missing.")


# =============================================================================
# Main run workflow
# =============================================================================

def main() -> None:
    print_header("CANOE/TEMOA existing-schema run")
    print(f"Project root: {PROJECT_ROOT}")
    print("This runner does not rebuild the database.")
    print("Use build_schema.py first if the encoded SQLite schema is stale or missing.")

    schema_options = sorted(SCHEMA_DIR.glob("*.sqlite"))
    config_options = sorted(CONFIG_DIR.glob("*.toml"))

    db_path = select_file(schema_options, "SQLite schema")
    config_path = select_file(config_options, "config")

    validate_required_paths(db_path, config_path)

    schema_tag = safe_name(db_path)
    config_tag = safe_name(config_path)
    timestamp = datetime.today().strftime("%Y-%m-%d_%H%M")

    output_dir = OUTPUT_ROOT / f"{timestamp}_{schema_tag}_{config_tag}"
    output_dir.mkdir(parents=True, exist_ok=False)

    input_db_archive = output_dir / f"input_{db_path.name}"
    solved_db_archive = output_dir / f"solved_{db_path.name}"

    print_header("Run configuration")
    print(f"Database: {db_path}")
    print(f"Config:   {config_path}")
    print(f"Output:   {output_dir}")

    print_header("Preparing run")
    print("Updating config database path...")
    update_db_paths(config_path, str(db_path), create_backup=True)

    print("Archiving input database...")
    shutil.copy2(db_path, input_db_archive)
    print(f"Saved: {input_db_archive.name}")

    command = [
        sys.executable,
        str(MAIN_PATH),
        "--config",
        str(config_path),
        "-o",
        str(output_dir),
    ]

    print_header("Starting solver")
    print("Command:")
    print(" ".join(command))
    print("\nTEMOA/CANOE output begins below.\n")

    start = time.perf_counter()

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        elapsed = time.perf_counter() - start
        print_header("Run failed")
        print(f"Solver exited with return code: {exc.returncode}")
        print(f"Elapsed time: {elapsed / 60:.2f} minutes")
        print(f"Output directory retained for inspection: {output_dir}")
        raise

    elapsed = time.perf_counter() - start

    print_header("Archiving solved database")
    shutil.copy2(db_path, solved_db_archive)
    print(f"Saved: {solved_db_archive.name}")

    print_header("Run complete")
    print(f"Elapsed time: {elapsed / 60:.2f} minutes")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
