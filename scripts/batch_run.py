"""Run an ordered batch of CANOE/TEMOA model configurations.

This script loads a Geospatial-CANOE batch configuration, validates each enabled
TEMOA run configuration and its referenced SQLite database, and executes the
runs sequentially through ``main_run.py``.

Each model run is launched as an independent subprocess. A failed run therefore
does not terminate the batch unless ``continue_on_failure`` is disabled. Batch
status is written incrementally to JSON and CSV so completed work remains
recorded if the batch is interrupted.

Inputs
------
config/*.toml

Outputs
-------
output_files/batches/{batch_name}_{timestamp}/
    batch_manifest.json
    batch_results.csv
    batch.log

Notes
-----
- Run order follows the order of ``[[runs]]`` entries in the batch TOML file.
- Disabled runs are recorded but not executed.
- Individual model outputs remain managed by ``main_run.py``.
- ``main_run.py`` must support non-interactive ``--database`` and ``--config``
  command-line arguments.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
import tomllib
from typing import TextIO


# =============================================================================
# Project discovery
# =============================================================================

def find_project_root() -> Path:
    """Locate and return the Geospatial-CANOE repository root.

    The search begins from both the current script path and active working
    directory. Each location and its parent directories are inspected until a
    directory containing ``scripts/main_run.py``, ``temoa/main.py``, and
    ``data_files/`` is found.

    Returns
    -------
    Path
        Absolute path to the detected repository root.

    Raises
    ------
    FileNotFoundError
        If no searched directory contains the expected project structure.
    """

    search_starts = [
        Path(__file__).resolve(),
        Path.cwd().resolve(),
    ]

    for start in search_starts:
        candidate_start = start if start.is_dir() else start.parent

        for candidate in [candidate_start, *candidate_start.parents]:
            if (
                (candidate / "scripts" / "main_run.py").exists()
                and (candidate / "temoa" / "main.py").exists()
                and (candidate / "data_files").is_dir()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate the Geospatial-CANOE project root. "
        "Expected scripts/main_run.py, temoa/main.py, and data_files/."
    )


PROJECT_ROOT = find_project_root()

MAIN_RUN_PATH = PROJECT_ROOT / "scripts" / "main_run.py"
OUTPUT_ROOT = PROJECT_ROOT / "output_files"
DEFAULT_BATCH_CONFIG = (
    PROJECT_ROOT
    / "config"
    / "batch_profiles"
    / "batch_run.toml"
)


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class BatchSettings:
    """Validated batch-level execution settings.

    Attributes
    ----------
    name : str
        Filename-safe identifier for the batch.
    continue_on_failure : bool
        Whether later runs should proceed after a failed model run.
    skip_completed : bool
        Whether previously successful runs should be skipped when resuming an
        existing batch directory.
    retry_failed : bool
        Whether previously failed runs should be attempted again when resuming.
    """

    name: str
    continue_on_failure: bool
    skip_completed: bool
    retry_failed: bool


@dataclass(frozen=True)
class BatchRun:
    """One validated model-run entry from the batch configuration.

    Attributes
    ----------
    sequence : int
        Zero-based position of the run in the batch configuration.
    config_path : Path
        Path to the individual TEMOA solver configuration.
    database_path : Path
        SQLite database referenced by the solver configuration.
    scenario : str
        Scenario name declared in the solver configuration.
    enabled : bool
        Whether the run should be executed.
    """

    sequence: int
    config_path: Path
    database_path: Path
    scenario: str
    enabled: bool


@dataclass
class BatchResult:
    """Execution record for one batch run.

    Attributes
    ----------
    sequence : int
        Zero-based position in the batch configuration.
    scenario : str
        TEMOA scenario name.
    config_path : str
        Solver-configuration path.
    database_path : str
        Input SQLite database path.
    status : str
        Current or final batch-run status.
    return_code : int | None
        Child-process return code, when available.
    started_at : str | None
        ISO-formatted start timestamp.
    finished_at : str | None
        ISO-formatted completion timestamp.
    elapsed_seconds : float | None
        Wall-clock execution time.
    """

    sequence: int
    scenario: str
    config_path: str
    database_path: str
    status: str
    return_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None


# =============================================================================
# Console helpers
# =============================================================================

def print_header(title: str) -> None:
    """Print a formatted console section heading."""

    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def write_log(log_file: TextIO, message: str) -> None:
    """Write one timestamped message to the console and batch log.

    Parameters
    ----------
    log_file : TextIO
        Open batch log file.
    message : str
        Message to record.

    Returns
    -------
    None
    """

    timestamp = datetime.now().isoformat(timespec="seconds")
    formatted = f"[{timestamp}] {message}"

    print(formatted)
    log_file.write(formatted + "\n")
    log_file.flush()


# =============================================================================
# Path and TOML helpers
# =============================================================================

def resolve_project_path(raw_path: str, source_path: Path) -> Path:
    """Resolve a configured path against the project or source directory.

    Absolute paths are returned directly. Relative paths are first interpreted
    relative to ``PROJECT_ROOT``. If that location does not exist, the path is
    interpreted relative to the TOML file containing it.

    Parameters
    ----------
    raw_path : str
        Configured filesystem path.
    source_path : Path
        TOML file containing the path.

    Returns
    -------
    Path
        Resolved absolute filesystem path.
    """

    configured_path = Path(raw_path).expanduser()

    if configured_path.is_absolute():
        return configured_path.resolve()

    project_candidate = (PROJECT_ROOT / configured_path).resolve()
    source_candidate = (source_path.parent / configured_path).resolve()

    if project_candidate.exists():
        return project_candidate

    return source_candidate


def load_toml(path: Path) -> dict:
    """Load and return one TOML document.

    Parameters
    ----------
    path : Path
        TOML file to load.

    Returns
    -------
    dict
        Parsed TOML mapping.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the TOML root is not represented as a dictionary.
    """

    if not path.exists():
        raise FileNotFoundError(f"TOML file not found: {path}")

    with path.open("rb") as file:
        raw = tomllib.load(file)

    if not isinstance(raw, dict):
        raise ValueError(f"TOML root must be a table: {path}")

    return raw


def require_non_empty_string(
    table: dict,
    key: str,
    context: str,
) -> str:
    """Return a required non-empty string from a mapping."""

    value = table.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{context} requires a non-empty string value for {key!r}."
        )

    return value.strip()


def require_bool(
    table: dict,
    key: str,
    context: str,
    default: bool,
) -> bool:
    """Return an optional boolean setting with a default value."""

    value = table.get(key, default)

    if not isinstance(value, bool):
        raise ValueError(
            f"{context}.{key} must be true or false."
        )

    return value


# =============================================================================
# Batch configuration loading
# =============================================================================

def load_solver_config(
    config_path: Path,
) -> tuple[str, Path]:
    """Load the scenario and input database from one TEMOA configuration.

    Parameters
    ----------
    config_path : Path
        Path to an individual TEMOA solver configuration.

    Returns
    -------
    tuple[str, Path]
        Scenario name and resolved input SQLite database path.

    Raises
    ------
    FileNotFoundError
        If the solver configuration does not exist.
    ValueError
        If the scenario or input database entry is missing or invalid.
    """

    raw = load_toml(config_path)

    scenario = require_non_empty_string(
        raw,
        "scenario",
        str(config_path),
    )
    input_database = require_non_empty_string(
        raw,
        "input_database",
        str(config_path),
    )

    database_path = resolve_project_path(
        input_database,
        source_path=config_path,
    )

    return scenario, database_path


def load_batch_config(
    batch_config_path: Path,
) -> tuple[BatchSettings, list[BatchRun]]:
    """Load and validate a batch-run configuration.

    Run order is preserved from the ``[[runs]]`` array in the TOML file. Each
    enabled run configuration is loaded to obtain its scenario name and
    referenced SQLite database.

    Parameters
    ----------
    batch_config_path : Path
        Path to the batch TOML configuration.

    Returns
    -------
    tuple[BatchSettings, list[BatchRun]]
        Validated batch settings and ordered run entries.

    Raises
    ------
    ValueError
        If required tables or fields are missing, invalid, or duplicated.
    """

    raw = load_toml(batch_config_path)

    batch_table = raw.get("batch")
    if not isinstance(batch_table, dict):
        raise ValueError(
            f"{batch_config_path} requires a [batch] table."
        )

    settings = BatchSettings(
        name=require_non_empty_string(
            batch_table,
            "name",
            "[batch]",
        ),
        continue_on_failure=require_bool(
            batch_table,
            "continue_on_failure",
            "batch",
            default=True,
        ),
        skip_completed=require_bool(
            batch_table,
            "skip_completed",
            "batch",
            default=True,
        ),
        retry_failed=require_bool(
            batch_table,
            "retry_failed",
            "batch",
            default=False,
        ),
    )

    raw_runs = raw.get("runs")

    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError(
            f"{batch_config_path} requires at least one [[runs]] entry."
        )

    runs: list[BatchRun] = []
    observed_configs: set[Path] = set()
    observed_scenarios: set[str] = set()

    for sequence, raw_run in enumerate(raw_runs):
        context = f"runs[{sequence}]"

        if not isinstance(raw_run, dict):
            raise ValueError(f"{context} must be a TOML table.")

        enabled = require_bool(
            raw_run,
            "enabled",
            context,
            default=True,
        )

        config_value = require_non_empty_string(
            raw_run,
            "config",
            context,
        )

        config_path = resolve_project_path(
            config_value,
            source_path=batch_config_path,
        )

        if config_path in observed_configs:
            raise ValueError(
                f"Duplicate run configuration in batch: {config_path}"
            )

        scenario, database_path = load_solver_config(config_path)

        if scenario in observed_scenarios:
            raise ValueError(
                f"Duplicate scenario name in batch: {scenario!r}"
            )

        runs.append(
            BatchRun(
                sequence=sequence,
                config_path=config_path,
                database_path=database_path,
                scenario=scenario,
                enabled=enabled,
            )
        )

        observed_configs.add(config_path)
        observed_scenarios.add(scenario)

    return settings, runs


# =============================================================================
# Validation
# =============================================================================

def normalize_schema_name(value: str) -> list[str]:
    """Normalize a scenario or database name for schema matching."""

    normalized = value.lo
    r().replace("-", "_")

    normalized = normalized.removeprefix("canoe_geospatial_")

    parts = [
        part
        for part in normalized.split("_")
        if part and part != "basemap"
    ]

    normalized_parts: list[str] = []

    for part in parts:
        degree_match = re.fullmatch(
            r"(\d+(?:\.\d+)?)deg",
            part,
        )

        if degree_match is not None:
            resolution = Decimal(degree_match.group(1)).normalize()
            normalized_parts.append(f"{resolution}deg")
        else:
            normalized_parts.append(part)

    return normalized_parts


def validate_scenario_database_match(run: BatchRun) -> None:
    """Check that a scenario appears consistent with its database filename.

    Common filename-only terms such as ``CANOE_geospatial`` and ``basemap`` are
    removed before comparison. Periods and hyphens are normalized to support
    resolution labels such as ``0.25deg``.

    Parameters
    ----------
    run : BatchRun
        Batch run to validate.

    Raises
    ------
    ValueError
        If the normalized scenario and database names do not match.
    """

    scenario_parts = normalize_schema_name(run.scenario)
    database_parts = normalize_schema_name(run.database_path.stem)

    if scenario_parts != database_parts:
        raise ValueError(
            "Scenario and database naming mismatch:\n"
            f"  Scenario: {run.scenario}\n"
            f"  Database: {run.database_path.name}\n"
            f"  Scenario tag: {'_'.join(scenario_parts)}\n"
            f"  Database tag: {'_'.join(database_parts)}"
        )

def validate_batch_runs(runs: list[BatchRun]) -> None:
    """Validate all enabled run files before batch execution.

    Parameters
    ----------
    runs : list[BatchRun]
        Ordered batch runs.

    Raises
    ------
    FileNotFoundError
        If ``main_run.py``, a solver configuration, or an enabled database is
        missing.
    ValueError
        If a solver configuration does not correspond to its database filename.
    """

    if not MAIN_RUN_PATH.exists():
        raise FileNotFoundError(
            f"Single-run script not found: {MAIN_RUN_PATH}"
        )

    for run in runs:
        if not run.config_path.exists():
            raise FileNotFoundError(
                f"Run config not found: {run.config_path}"
            )

        if run.enabled and not run.database_path.exists():
            raise FileNotFoundError(
                f"Run database not found: {run.database_path}"
            )

        if run.enabled:
            validate_scenario_database_match(run)


# =============================================================================
# Result persistence
# =============================================================================

RESULT_FIELDNAMES = [
    "sequence",
    "scenario",
    "config_path",
    "database_path",
    "status",
    "return_code",
    "started_at",
    "finished_at",
    "elapsed_seconds",
]


def write_results_csv(
    path: Path,
    results: list[BatchResult],
) -> None:
    """Write current batch-run results to CSV."""

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=RESULT_FIELDNAMES,
        )
        writer.writeheader()

        for result in results:
            writer.writerow(asdict(result))


def write_batch_manifest(
    path: Path,
    settings: BatchSettings,
    batch_config_path: Path,
    results: list[BatchResult],
    status: str,
) -> None:
    """Write the current batch state to JSON."""

    manifest = {
        "batch": {
            "name": settings.name,
            "status": status,
            "source_config": str(batch_config_path),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "settings": asdict(settings),
        "runs": [
            asdict(result)
            for result in results
        ],
    }

    path.write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def persist_batch_state(
    manifest_path: Path,
    results_path: Path,
    settings: BatchSettings,
    batch_config_path: Path,
    results: list[BatchResult],
    status: str,
) -> None:
    """Write both batch JSON and CSV state files."""

    write_batch_manifest(
        path=manifest_path,
        settings=settings,
        batch_config_path=batch_config_path,
        results=results,
        status=status,
    )
    write_results_csv(
        path=results_path,
        results=results,
    )


# =============================================================================
# Execution
# =============================================================================

def build_main_run_command(run: BatchRun) -> list[str]:
    """Build the non-interactive ``main_run.py`` command for one run."""

    return [
        sys.executable,
        str(MAIN_RUN_PATH),
        "--database",
        str(run.database_path),
        "--config",
        str(run.config_path),
        "--non-interactive",
    ]

def execute_batch(
    settings: BatchSettings,
    runs: list[BatchRun],
    batch_config_path: Path,
) -> int:
    """Execute all configured runs sequentially.

    Parameters
    ----------
    settings : BatchSettings
        Batch-level execution settings.
    runs : list[BatchRun]
        Ordered run definitions.
    batch_config_path : Path
        Source batch TOML path.

    Returns
    -------
    int
        Zero when every enabled run succeeds, otherwise one.
    """

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    batch_dir = (
        OUTPUT_ROOT
        / "batches"
        / f"{settings.name}_{timestamp}"
    )
    batch_dir.mkdir(parents=True, exist_ok=False)

    manifest_path = batch_dir / "batch_manifest.json"
    results_path = batch_dir / "batch_results.csv"
    log_path = batch_dir / "batch.log"

    results = [
        BatchResult(
            sequence=run.sequence,
            scenario=run.scenario,
            config_path=str(run.config_path),
            database_path=str(run.database_path),
            status="pending" if run.enabled else "skipped_disabled",
        )
        for run in runs
    ]

    persist_batch_state(
        manifest_path=manifest_path,
        results_path=results_path,
        settings=settings,
        batch_config_path=batch_config_path,
        results=results,
        status="running",
    )

    batch_failed = False

    with log_path.open("a", encoding="utf-8") as log_file:
        write_log(
            log_file,
            f"Starting batch {settings.name!r} with {len(runs)} run(s).",
        )
        write_log(
            log_file,
            f"Batch output directory: {batch_dir}",
        )

        try:
            for run, result in zip(runs, results, strict=True):
                if not run.enabled:
                    write_log(
                        log_file,
                        f"Skipping disabled run: {run.scenario}",
                    )
                    continue

                result.status = "running"
                result.started_at = datetime.now().isoformat(
                    timespec="seconds"
                )

                persist_batch_state(
                    manifest_path=manifest_path,
                    results_path=results_path,
                    settings=settings,
                    batch_config_path=batch_config_path,
                    results=results,
                    status="running",
                )

                print_header(
                    f"Batch run {run.sequence + 1}/{len(runs)}: "
                    f"{run.scenario}"
                )

                command = build_main_run_command(run)

                write_log(
                    log_file,
                    "Command: " + subprocess.list2cmdline(command),
                )

                start = perf_counter()

                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    check=False,
                )

                elapsed = perf_counter() - start

                result.return_code = completed.returncode
                result.elapsed_seconds = elapsed
                result.finished_at = datetime.now().isoformat(
                    timespec="seconds"
                )

                if completed.returncode == 0:
                    result.status = "success"
                    write_log(
                        log_file,
                        f"Run succeeded: {run.scenario} "
                        f"({elapsed / 60:.2f} minutes).",
                    )
                else:
                    result.status = "failed"
                    batch_failed = True

                    write_log(
                        log_file,
                        f"Run failed: {run.scenario}; "
                        f"return code {completed.returncode}; "
                        f"elapsed {elapsed / 60:.2f} minutes.",
                    )

                persist_batch_state(
                    manifest_path=manifest_path,
                    results_path=results_path,
                    settings=settings,
                    batch_config_path=batch_config_path,
                    results=results,
                    status="running",
                )

                if (
                    completed.returncode != 0
                    and not settings.continue_on_failure
                ):
                    for pending_result in results[run.sequence + 1:]:
                        if pending_result.status == "pending":
                            pending_result.status = "skipped_after_failure"

                    write_log(
                        log_file,
                        "Stopping batch because "
                        "continue_on_failure is false.",
                    )
                    break

        except KeyboardInterrupt:
            write_log(
                log_file,
                "Batch interrupted by user.",
            )

            for result in results:
                if result.status == "running":
                    result.status = "interrupted"
                    result.finished_at = datetime.now().isoformat(
                        timespec="seconds"
                    )

            persist_batch_state(
                manifest_path=manifest_path,
                results_path=results_path,
                settings=settings,
                batch_config_path=batch_config_path,
                results=results,
                status="interrupted",
            )

            return 130

        final_status = "failed" if batch_failed else "success"

        persist_batch_state(
            manifest_path=manifest_path,
            results_path=results_path,
            settings=settings,
            batch_config_path=batch_config_path,
            results=results,
            status=final_status,
        )

        write_log(
            log_file,
            f"Batch completed with status: {final_status}.",
        )

    return 1 if batch_failed else 0


# =============================================================================
# Command-line interface
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse the batch-run configuration path."""

    parser = argparse.ArgumentParser(
        description=(
            "Run an ordered batch of CANOE/TEMOA solver configurations."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_BATCH_CONFIG,
        help=(
            "Path to the batch-run TOML configuration. "
            f"Default: {DEFAULT_BATCH_CONFIG}"
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Load, validate, and execute a CANOE/TEMOA batch."""

    args = parse_args()
    batch_config_path = args.config.resolve()

    print_header("Geospatial-CANOE batch runner")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Batch config: {batch_config_path}")

    settings, runs = load_batch_config(batch_config_path)
    validate_batch_runs(runs)

    print_header("Validated batch queue")
    print(f"Batch: {settings.name}")
    print(f"Runs:  {len(runs)}")

    for run in runs:
        state = "enabled" if run.enabled else "disabled"
        print(
            f"  [{run.sequence}] {run.scenario} "
            f"({state})\n"
            f"      Config:   {run.config_path}\n"
            f"      Database: {run.database_path}"
        )

    return_code = execute_batch(
        settings=settings,
        runs=runs,
        batch_config_path=batch_config_path,
    )

    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
