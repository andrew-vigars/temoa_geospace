"""
Run CANOE/TEMOA from an existing encoded SQLite database.

This module executes a CANOE/TEMOA model run using a database produced by the
upstream Geospatial-CANOE schema workflow. It does not construct or modify the
model schema before execution.

The workflow resolves an encoded SQLite database and TEMOA configuration,
creates an isolated timestamped run directory, records execution provenance,
archives the database before and after solving, executes TEMOA, and exports
solved ``Output*`` tables for downstream inspection and analysis.

Inputs
------
data_files/processed/schema/*.sqlite
temoa/data_files/my_configs/*

Outputs
-------
output_files/{timestamped_run}/
    Input and solved SQLite database copies
    Effective configuration and provenance records
    Solver logs and model outputs
    Excel export of solved ``Output*`` tables
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from geocanoe.analysis.exports import export_output_tables
from geocanoe.paths import find_project_root
from geocanoe.schema.database import update_db_paths


# =============================================================================
# Project discovery
# =============================================================================


PROJECT_ROOT = find_project_root()

MAIN_PATH = PROJECT_ROOT / "temoa" / "main.py"
CONFIG_DIR = PROJECT_ROOT / "temoa" / "data_files" / "my_configs"
SCHEMA_DIR = PROJECT_ROOT / "data_files" / "processed" / "schema"
OUTPUT_ROOT = PROJECT_ROOT / "output_files"


# =============================================================================
# CLI helpers
# =============================================================================

def print_header(title: str) -> None:
    """Print a formatted section header to the console.

    The title is displayed between two horizontal separator lines, with a leading
    blank line to distinguish the section from preceding console output.

    Parameters
    ----------
    title : str
        Text to display as the section heading.

    Returns
    -------
    None
    """

    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def select_file(options: list[Path], label: str) -> Path:
    """Prompt the user to select a file from a numbered list.

    The available paths are displayed with zero-based indices and, for regular
    files, their approximate sizes in megabytes. The user is repeatedly prompted
    until they enter an integer corresponding to a valid option.

    Parameters
    ----------
    options : list[Path]
        File paths available for selection.
    label : str
        Human-readable file category used in the section heading, prompt, and error
        messages.

    Returns
    -------
    Path
        Path selected by the user.

    Raises
    ------
    FileNotFoundError
        If ``options`` is empty.
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
    """Return a filesystem-safe schema tag derived from a database path.

    The filename stem is normalized by removing the standard
    ``CANOE_geospatial_`` prefix and replacing spaces with underscores. The result
    is suitable for use in timestamped output-directory names and related run
    artifacts.

    Parameters
    ----------
    path : Path
        Path whose filename stem will be converted into a schema tag.

    Returns
    -------
    str
        Normalized schema tag derived from ``path``.
    """

    return path.stem.replace("CANOE_geospatial_", "").replace(" ", "_")


def validate_required_paths(db_path: Path, config_path: Path) -> None:
    """Validate the filesystem inputs required for a CANOE/TEMOA run.

    The TEMOA entry-point script, selected SQLite database, and selected
    configuration file are checked before model execution begins. Any missing paths
    are printed to the console before a ``FileNotFoundError`` is raised.

    Parameters
    ----------
    db_path : Path
        Path to the encoded SQLite database selected for the model run.
    config_path : Path
        Path to the TEMOA configuration file selected for the model run.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If the TEMOA main script, selected database, or selected configuration file
        does not exist.
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


def parse_args() -> argparse.Namespace:
    """Parse optional non-interactive model-run inputs.

    The database and TEMOA configuration arguments are optional so the existing
    interactive file-selection workflow remains available. When
    ``--non-interactive`` is supplied, both paths must be provided and TEMOA is
    executed with its silent command-line flag.

    Returns
    -------
    argparse.Namespace
        Parsed database path, configuration path, and non-interactive flag.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Run CANOE/TEMOA from an existing encoded SQLite database."
        )
    )

    parser.add_argument(
        "--database",
        type=Path,
        help=(
            "Path to an encoded CANOE/TEMOA SQLite database. "
            "When omitted, the database is selected interactively."
        ),
    )

    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Path to a TEMOA run configuration. "
            "When omitted, the configuration is selected interactively."
        ),
    )

    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Disable file-selection and TEMOA confirmation prompts. "
            "Requires both --database and --config."
        ),
    )

    return parser.parse_args()

# =============================================================================
# Run batch scripting helpers
# =============================================================================

def resolve_project_path(path: Path) -> Path:
    """Resolve a configured path against the project root.

    Absolute paths are resolved directly. Relative paths are interpreted from
    ``PROJECT_ROOT`` so commands behave consistently regardless of the active
    working directory.

    Parameters
    ----------
    path : Path
        Absolute or project-relative filesystem path.

    Returns
    -------
    Path
        Resolved absolute path.
    """

    path = path.expanduser()

    if path.is_absolute():
        return path.resolve()

    return (PROJECT_ROOT / path).resolve()


def resolve_run_inputs(
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    """Resolve database and configuration paths for one model run.

    Explicit command-line paths are used when supplied. Missing paths are selected
    interactively from the canonical schema and TEMOA configuration directories.

    Non-interactive execution requires both paths because prompting would otherwise
    block unattended batch execution.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    tuple[Path, Path]
        Resolved SQLite database path and TEMOA configuration path.

    Raises
    ------
    ValueError
        If non-interactive execution is requested without both required paths.
    """

    if args.non_interactive and (
        args.database is None or args.config is None
    ):
        raise ValueError(
            "--non-interactive requires both --database and --config."
        )

    if args.database is not None:
        db_path = resolve_project_path(args.database)
    else:
        schema_options = sorted(SCHEMA_DIR.glob("*.sqlite"))
        db_path = select_file(schema_options, "SQLite schema")

    if args.config is not None:
        config_path = resolve_project_path(args.config)
    else:
        config_options = sorted(CONFIG_DIR.glob("*.toml"))
        config_path = select_file(config_options, "config")

    return db_path, config_path

# =============================================================================
# Run provenance helpers
# =============================================================================

def sha256_file(path: Path) -> str:
    """Calculate the SHA-256 digest of a file.

    The file is read incrementally in 1 MB binary chunks so large SQLite databases
    and archived run inputs can be hashed without loading the complete file into
    memory.

    Parameters
    ----------
    path : Path
        Path to the file to hash.

    Returns
    -------
    str
        Lowercase hexadecimal SHA-256 digest of the file contents.
    """

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def file_record(path: Path) -> dict:
    """Build a provenance record for one file.

    The record captures the file's full path, filename, size in bytes, and SHA-256
    digest for inclusion in the model-run manifest.

    Parameters
    ----------
    path : Path
        Path to the file for which provenance metadata will be collected.

    Returns
    -------
    dict
        Mapping containing the file path, filename, size in bytes, and SHA-256 hash.
    """

    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def run_git_command(args: list[str]) -> str | None:
    """Run a Git command from the project root and return its output.

    The supplied Git arguments are executed with ``PROJECT_ROOT`` as the working
    directory. Standard output is captured, stripped of leading and trailing
    whitespace, and returned when the command succeeds.

    Git failures are treated as non-fatal so provenance collection can continue
    when Git is unavailable, the project is not inside a valid repository, or the
    requested command returns a nonzero exit status.

    Parameters
    ----------
    args : list[str]
        Git command arguments excluding the leading ``git`` executable.

    Returns
    -------
    str | None
        Cleaned standard output from the Git command, or ``None`` if Git cannot be
        executed or the command fails.
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
    """Collect best-effort Git provenance metadata for the active repository.

    The current commit hash, branch name, and short working-tree status are queried
    from ``PROJECT_ROOT``. The dirty-state flag is derived from whether the short
    status output is non-empty.

    Git metadata collection is non-fatal. Individual values may be ``None`` when
    Git is unavailable, the project is not a valid repository, or a command fails.

    Returns
    -------
    dict
        Mapping containing the current commit hash, branch name, dirty-state flag,
        and short Git status output.
    """

    status = run_git_command(["status", "--short"])

    return {
        "commit": run_git_command(["rev-parse", "HEAD"]),
        "branch": run_git_command(["branch", "--show-current"]),
        "dirty": bool(status),
        "status_short": status,
    }


def read_text_file(path: Path) -> str:
    """Read a text file as UTF-8 for inclusion in the run manifest.

    The file is decoded using UTF-8. Any invalid byte sequences are replaced rather
    than raising a decoding error so configuration text can still be archived
    without interrupting the model-run workflow.

    Parameters
    ----------
    path : Path
        Path to the text file to read.

    Returns
    -------
    str
        Decoded file contents with invalid characters replaced.
    """

    return path.read_text(encoding="utf-8", errors="replace")


def write_manifest(path: Path, manifest: dict) -> None:
    """Write a run manifest to formatted JSON.

    The manifest is serialized with stable key ordering and consistent indentation
    so model-run metadata remains human-readable and easy to compare across
    executions. The resulting JSON text is written using UTF-8 encoding.

    Parameters
    ----------
    path : Path
        Destination path for the JSON manifest.
    manifest : dict
        Run metadata to serialize.

    Returns
    -------
    None
    """

    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def extract_objective_from_db(db_path: Path) -> list[dict]:
    """Extract solved objective values from a SQLite database.

    The ``OutputObjective`` table is queried for scenario names, objective names,
    and total system costs. Matching rows are ordered by scenario and objective
    name, then converted into dictionaries suitable for inclusion in the run
    manifest.

    Database and query failures are treated as non-fatal. If the database cannot be
    opened, the table is missing, or the query otherwise fails, an empty list is
    returned.

    Parameters
    ----------
    db_path : Path
        Path to the solved CANOE/TEMOA SQLite database.

    Returns
    -------
    list[dict]
        Objective-result records containing ``scenario``, ``objective_name``, and
        ``total_system_cost`` fields. Returns an empty list if the query fails.
    """

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
    """Run CANOE/TEMOA from an existing encoded SQLite schema.

    The workflow resolves an encoded SQLite database and TEMOA configuration
    either from explicit command-line arguments or through interactive file
    selection. It validates the required paths, creates a timestamped output
    directory, archives the immutable source database, and creates an isolated
    working database for model execution.

    An effective configuration is copied into the run directory and updated to
    reference the working database. TEMOA is executed as a subprocess, with its
    confirmation prompt suppressed when non-interactive execution is requested.

    A run manifest records the command, execution mode, environment, Git state,
    tracked files, input hashes, configuration text, status, return code, and
    elapsed time. Failed solver runs retain their output directory and working
    database for inspection before the subprocess exception is re-raised.

    After a successful solve, the working database is archived as the solved
    database, solved ``Output*`` tables are exported to an Excel workbook,
    objective values are extracted when available, and the final output metadata
    are written to the completed run manifest.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If required model, database, or configuration paths are unavailable.
    FileExistsError
        If the generated timestamped output directory already exists.
    subprocess.CalledProcessError
        If the TEMOA/CANOE subprocess exits with a nonzero return code.
    RuntimeError
        If the solved output export does not produce exactly one Excel workbook.
    """

    args = parse_args()

    print_header("CANOE/TEMOA existing-schema run")
    print(f"Project root: {PROJECT_ROOT}")
    print("This runner does not rebuild the database.")
    print("Use build_schema.py first if the encoded SQLite schema is stale or missing.")

    db_path, config_path = resolve_run_inputs(args)

    validate_required_paths(db_path, config_path)

    schema_tag = safe_name(db_path)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    output_dir = OUTPUT_ROOT / f"{timestamp}_{schema_tag}"
    output_dir.mkdir(parents=True, exist_ok=False)

    input_db_archive = output_dir / f"input_{db_path.name}"
    working_db_path = output_dir / f"working_{db_path.name}"
    solved_db_archive = output_dir / f"solved_{db_path.name}"

    print_header("Run configuration")
    print(f"Database:        {db_path}")
    print(f"Config:          {config_path}")
    print(f"Non-interactive: {args.non_interactive}")
    print(f"Output:          {output_dir}")

    print_header("Preparing run")
    print("Creating effective run config...")
    effective_config_path = output_dir / f"effective_{config_path.name}"
    shutil.copy2(config_path, effective_config_path)

    print("Archiving immutable input database...")
    shutil.copy2(db_path, input_db_archive)
    print(f"Saved: {input_db_archive.name}")

    print("Creating isolated working database...")
    shutil.copy2(db_path, working_db_path)
    print(f"Saved: {working_db_path.name}")

    print("Updating effective config database paths...")
    update_db_paths(
        effective_config_path,
        str(working_db_path),
        create_backup=False,
    )

    command = [
        sys.executable,
        str(MAIN_PATH),
        "--config",
        str(effective_config_path),
        "-o",
        str(output_dir),
    ]

    if args.non_interactive:
        command.append("-s")

    manifest_path = output_dir / "manifest.json"

    manifest = {
        "run": {
            "timestamp": timestamp,
            "status": "started",
            "non_interactive": args.non_interactive,
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
            "source_database": file_record(db_path),
            "working_database": file_record(working_db_path),
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
    shutil.copy2(working_db_path, solved_db_archive)
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

    try:
        working_db_path.unlink()
    except OSError as exc:
        print(
            "WARNING: Could not remove temporary working database: "
            f"{working_db_path}"
        )
        print(f"Reason: {exc}")
    else:
        print(
            "Removed temporary working database: "
            f"{working_db_path.name}"
        )

    print_header("Run complete")
    print(f"Elapsed time: {elapsed / 60:.2f} minutes")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
