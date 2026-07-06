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

import hashlib
import json
import platform
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
# Run provenance helpers
# =============================================================================

def sha256_file(path: Path) -> str:
    """Return SHA256 hash for a file."""
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def file_record(path: Path) -> dict:
    """Return basic provenance information for one file."""
    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def run_git_command(args: list[str]) -> str | None:
    """Run a git command from the project root and return stdout, if available."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    return result.stdout.strip()


def get_git_record() -> dict:
    """Return git commit, branch, and dirty-state metadata."""
    status = run_git_command(["status", "--short"])

    return {
        "commit": run_git_command(["rev-parse", "HEAD"]),
        "branch": run_git_command(["branch", "--show-current"]),
        "dirty": bool(status),
        "status_short": status,
    }


def read_text_file(path: Path) -> str:
    """Read a text file for manifest archival."""
    return path.read_text(encoding="utf-8", errors="replace")


def write_manifest(path: Path, manifest: dict) -> None:
    """Write manifest JSON with stable formatting."""
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def extract_objective_from_db(db_path: Path) -> list[dict]:
    """
    Try to extract objective results from the solved SQLite database.

    Returns an empty list if the OutputObjective table is absent or unreadable.
    """
    import sqlite3

    query = """
        SELECT scenario, objective_name, total_system_cost
        FROM OutputObjective
        ORDER BY scenario, objective_name
    """

    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(query).fetchall()
    except sqlite3.Error:
        return []

    return [
        {
            "scenario": scenario,
            "objective_name": objective_name,
            "total_system_cost": total_system_cost,
        }
        for scenario, objective_name, total_system_cost in rows
    ]

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

    schema_tag = safe_name(db_path)q
    timestamp = datetime.today().strftime("%Y-%m-%d_%H%M")

    output_dir = OUTPUT_ROOT / f"{timestamp}_{schema_tag}"
    output_dir.mkdir(parents=True, exist_ok=False)

    input_db_archive = output_dir / f"input_{db_path.name}"
    solved_db_archive = output_dir / f"solved_{db_path.name}"

    print_header("Run configuration")
    print(f"Database: {db_path}")
    print(f"Config:   {config_path}")
    print(f"Output:   {output_dir}")

    print_header("Preparing run")
    print("Creating effective run config...")
    effective_config_path = output_dir / f"effective_{config_path.name}"
    shutil.copy2(config_path, effective_config_path)

    print("Updating effective config database path...")
    update_db_paths(effective_config_path, str(db_path), create_backup=False)

    print("Archiving input database...")
    shutil.copy2(db_path, input_db_archive)
    print(f"Saved: {input_db_archive.name}")

    command = [
        sys.executable,
        str(MAIN_PATH),
        "--config",
        str(effective_config_path),
        "-o",
        str(output_dir),
    ]

    manifest_path = output_dir / "manifest.json"

    manifest = {
        "run": {
            "timestamp": timestamp,
            "status": "started",
            "return_code": None,
            "wall_time_seconds": None,
            "output_dir": str(output_dir),
            "command": command,
        },
        "environment": {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "platform": platform.platform(),
        },
        "code": {
            "git": get_git_record(),
            "tracked_files": {
                "main_run": file_record(Path(__file__).resolve()),
                "temoa_main": file_record(MAIN_PATH),
            },
        },
        "inputs": {
            "database": file_record(db_path),
            "source_config": file_record(config_path),
            "effective_config": file_record(effective_config_path),
        },
        "config_text": {
            "source_config": read_text_file(config_path),
            "effective_config": read_text_file(effective_config_path),
        },
        "results": {
            "objectives": [],
        },
    }

    write_manifest(manifest_path, manifest)
    print(f"Manifest started: {manifest_path.name}")

    print_header("Starting solver")
    print("Command:")
    print(" ".join(command))
    print("\nTEMOA/CANOE output begins below.\n")

    start = time.perf_counter()

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        elapsed = time.perf_counter() - start

        manifest["run"]["status"] = "failed"
        manifest["run"]["return_code"] = exc.returncode
        manifest["run"]["wall_time_seconds"] = elapsed
        write_manifest(manifest_path, manifest)

        print_header("Run failed")
        print(f"Solver exited with return code: {exc.returncode}")
        print(f"Elapsed time: {elapsed / 60:.2f} minutes")
        print(f"Output directory retained for inspection: {output_dir}")
        print(f"Manifest updated: {manifest_path.name}")
        raise

    elapsed = time.perf_counter() - start

    print_header("Archiving solved database")
    shutil.copy2(db_path, solved_db_archive)
    print(f"Saved: {solved_db_archive.name}")

    manifest["run"]["status"] = "success"
    manifest["run"]["return_code"] = 0
    manifest["run"]["wall_time_seconds"] = elapsed

    manifest["outputs"] = {
        "input_database_archive": file_record(input_db_archive),
        "solved_database_archive": file_record(solved_db_archive),
    }

    manifest["results"]["objectives"] = extract_objective_from_db(solved_db_archive)

    write_manifest(manifest_path, manifest)
    print(f"Manifest updated: {manifest_path.name}")

    print_header("Run complete")
    print(f"Elapsed time: {elapsed / 60:.2f} minutes")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
