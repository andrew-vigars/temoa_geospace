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
from export_output_tables import export_output_tables


# =============================================================================
# Project discovery
# =============================================================================

def find_project_root() -> Path:
    """Find the project root from the script path or current working directory.

    Searches upward from both ``__file__`` and the current working directory,
    returning the first parent folder containing ``temoa/main.py`` and
    ``data_files``. Raises ``FileNotFoundError`` if the repository root cannot
    be located.
    """

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
    """Print a formatted section header for console output."""

    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def select_file(options: list[Path], label: str) -> Path:
    """Prompt the user to select one file from a numbered list.

    Prints available paths with optional file sizes, repeatedly asks for a valid
    integer selection, and returns the selected path. Raises ``FileNotFoundError``
    if no options are available.
    """

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
    """Return a filesystem-friendly schema tag for output folder names.

    Removes the ``CANOE_geospatial_`` prefix from the file stem and replaces
    spaces with underscores.
    """

    return path.stem.replace("CANOE_geospatial_", "").replace(" ", "_")


def validate_required_paths(db_path: Path, config_path: Path) -> None:
    """Validate required run inputs before starting CANOE/TEMOA.

    Checks that the TEMOA main script, selected SQLite database, and selected
    config file exist. Prints any missing paths before raising
    ``FileNotFoundError``.
    """

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
    """Return the SHA-256 hash for a file.

    Reads the file in 1 MB chunks so large SQLite databases and archived run
    inputs can be hashed without loading the full file into memory.
    """

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def file_record(path: Path) -> dict:
    """Return provenance metadata for one file.

    Records the file path, filename, size in bytes, and SHA-256 hash for use in
    the run manifest.
    """

    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def run_git_command(args: list[str]) -> str | None:
    """Run a git command from the project root and return cleaned stdout.

    Returns ``None`` if git is unavailable or the command fails, allowing run
    provenance capture to continue without requiring the repository to be in a
    valid git environment.
    """

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
    """Return best-effort git commit, branch, and dirty-state metadata.

    Captures the current commit hash, branch name, short status output, and a
    boolean dirty-state flag for the run manifest. Values may be ``None`` if git
    is unavailable or a command fails.
    """

    status = run_git_command(["status", "--short"])

    return {
        "commit": run_git_command(["rev-parse", "HEAD"]),
        "branch": run_git_command(["branch", "--show-current"]),
        "dirty": bool(status),
        "status_short": status,
    }


def read_text_file(path: Path) -> str:
    """Read a text file as UTF-8 for manifest archival.

    Invalid characters are replaced so config text can still be captured in the
    run manifest without failing the model run.
    """

    return path.read_text(encoding="utf-8", errors="replace")


def write_manifest(path: Path, manifest: dict) -> None:
    """Write the run manifest to stable, human-readable JSON.

    Serializes the manifest with sorted keys and consistent indentation so run
    metadata is easy to inspect, compare, and track across model executions.
    """

    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def extract_objective_from_db(db_path: Path) -> list[dict]:
    """Extract objective results from a solved SQLite database for the manifest.

    Reads scenario-level objective values from ``OutputObjective`` and returns
    them as dictionaries. Returns an empty list if the table is missing,
    unreadable, or the database cannot be queried.
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
    """Run CANOE/TEMOA from a selected existing SQLite schema.

    Interactively selects an encoded SQLite database and config file, validates
    required paths, creates a timestamped output directory, copies and updates an
    effective run config, archives the input database, writes an initial run
    manifest, executes TEMOA/CANOE, records failure or success metadata, archives
    the solved database, extracts objective results when available, and writes
    the final manifest.
    """

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

    print_header("Exporting solved output workbook")

    exported_output_paths = export_output_tables(
        db_path=solved_db_archive,
        output_dir=output_dir,
    )

    if len(exported_output_paths) != 1:
        raise RuntimeError(
            "Expected export_output_tables() to return exactly one Excel workbook path."
        )

    output_workbook_path = exported_output_paths[0]
    print(f"Exported output workbook: {output_workbook_path.name}")

    manifest["run"]["status"] = "success"
    manifest["run"]["return_code"] = 0
    manifest["run"]["wall_time_seconds"] = elapsed

    manifest["outputs"] = {
        "input_database_archive": file_record(input_db_archive),
        "solved_database_archive": file_record(solved_db_archive),
        "output_workbook": file_record(output_workbook_path),
    }

    manifest["results"]["objectives"] = extract_objective_from_db(solved_db_archive)

    write_manifest(manifest_path, manifest)
    print(f"Manifest updated: {manifest_path.name}")

    print_header("Run complete")
    print(f"Elapsed time: {elapsed / 60:.2f} minutes")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
