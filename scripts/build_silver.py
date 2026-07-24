"""Build the Geospatial-CANOE silver preprocessing layer.

This module coordinates the transformations that convert raw and controlled
source datasets into the standardized products stored under
``data_files/processed/``. It replaces the original exploratory notebook with a
repeatable command-line workflow while preserving the existing stage-specific
implementations.

The silver workflow currently includes:

1. Province assignment for legacy site and demand tables.
2. Standardization of large-facility emissions data.
3. Normalization of hydrogen-pipeline engineering cost observations.
4. Fitting and export of hydrogen-pipeline cost-model templates.
5. Construction of profile-specific basemap grids.
6. Construction of region-adjacency graph products.
7. Filtering and merging of National Road Network layers.
8. Mapping of filtered roads onto the regional graph.

Stages are executed in a canonical dependency order. Function-based stages run
inside the current Python process and receive the loaded
``GeospatialBuildConfig`` directly. Script-based stages run in isolated Python
subprocesses from the repository root so their existing command-line behaviour
and module-level state remain contained.

Before processing begins, the orchestrator validates its stage registry,
execution targets, dependency order, and raw external inputs. After processing,
it verifies stage completion and confirms that the expected processed-layer
directories contain files.

Inputs
------
config/provinces_only.toml, or another TOML build profile supplied with
``--config``
data_files/raw/basemaps/*.shp
data_files/raw/emissions/co2_large_facilities_2024/*
data_files/raw/nrn/{PROVINCE}/*_en.gpkg
data_files/sites_full.csv
data_files/demand.csv
data_files/models/cost_models/transport/master_files/
    h2_pipeline_costs_master_v2.xlsx

Outputs
-------
data_files/processed/legacy_inputs/
data_files/processed/emissions/
data_files/processed/costs/
data_files/processed/basemaps/
data_files/processed/graph/
data_files/processed/nrn/
data_files/processed/road_connectivity/

Notes
-----
The silver layer does not encode the final CANOE/TEMOA SQLite database. Schema
construction is a downstream gold-layer transformation performed by
``build_schema.py``.
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any, Literal, TypedDict


# =============================================================================
# Project discovery and import path
# =============================================================================


def find_project_root(start_path: Path | None = None) -> Path:
    """Locate the Geospatial-CANOE repository root.

    The search begins from ``start_path`` when supplied. Otherwise, both the
    current script location and active working directory are searched. Each
    starting location and its parent directories are inspected in order, and the
    first directory containing both ``scripts/`` and ``data_files/`` is treated as
    the repository root.

    Parameters
    ----------
    start_path : Path | None, optional
        Explicit file or directory from which to begin the upward search. File
        paths are converted to their parent directory before searching. When
        omitted, the script location and current working directory are searched.

    Returns
    -------
    Path
        Absolute path to the detected Geospatial-CANOE repository root.

    Raises
    ------
    FileNotFoundError
        If no searched directory contains both the expected ``scripts/`` and
        ``data_files/`` directories.
    """

    search_starts = (
        [start_path.resolve()]
        if start_path is not None
        else [Path(__file__).resolve(), Path.cwd().resolve()]
    )

    for start in search_starts:
        candidate_start = start if start.is_dir() else start.parent

        for candidate in (candidate_start, *candidate_start.parents):
            if (
                (candidate / "scripts").is_dir()
                and (candidate / "data_files").is_dir()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate the Geospatial-CANOE repository root. "
        "Expected to find both scripts/ and data_files/."
    )


PROJECT_ROOT = find_project_root()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_FILES = PROJECT_ROOT / "data_files"
CONFIG_DIR = PROJECT_ROOT / "config"

for import_path in (PROJECT_ROOT, SCRIPTS_DIR):
    import_path_text = str(import_path)
    if import_path_text not in sys.path:
        sys.path.insert(0, import_path_text)

from project_config import (  # pylint: disable=wrong-import-position
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)


# =============================================================================
# Workflow type definitions
# =============================================================================

StageMethod = Literal["function", "script"]
StageCallable = Callable[[GeospatialBuildConfig], Any]


class ExternalInputCheck(TypedDict):
    """One external-input validation result.

    Attributes
    ----------
    stage : str
        Silver stage that consumes the external input.
    input : str
        Human-readable name of the required input.
    path : Path
        Expected input file or directory path.
    status : str
        Validation status, such as ``"found"``, ``"missing"``, or ``"invalid"``.
    details : str | None
        Optional explanation for a missing or invalid result.
    """

    stage: str
    input: str
    path: Path
    status: str
    details: str | None


class WorkflowHistoryRecord(TypedDict):
    """Execution record for one attempted silver stage.

    Attributes
    ----------
    stage : str
        Canonical stage name.
    status : str
        Final stage status, either ``"completed"`` or ``"failed"``.
    elapsed_seconds : float
        Wall-clock execution duration in seconds.
    exception_type : str | None
        Exception class name when execution failed, otherwise ``None``.
    exception_message : str | None
        Exception message when execution failed, otherwise ``None``.
    """

    stage: str
    status: str
    elapsed_seconds: float
    exception_type: str | None
    exception_message: str | None


@dataclass(frozen=True)
class FunctionExecutor:
    """Execution definition for an in-process silver stage.

    Attributes
    ----------
    callable : StageCallable
        Stage function that accepts the loaded geospatial build profile.
    """

    callable: StageCallable
    method: StageMethod = "function"


@dataclass(frozen=True)
class ScriptExecutor:
    """Execution definition for an isolated silver-stage script.

    Attributes
    ----------
    path : Path
        Path to the standalone Python script.
    arguments : tuple[str, ...]
        Additional command-line arguments passed after the script path.
    """

    path: Path
    arguments: tuple[str, ...] = ()
    method: StageMethod = "script"


StageExecutor = FunctionExecutor | ScriptExecutor


@dataclass(frozen=True)
class SilverWorkflowDefinition:
    """Validated registry describing the complete silver workflow.

    Attributes
    ----------
    stage_order : tuple[str, ...]
        Canonical dependency-compatible order in which stages are run.
    stage_modules : Mapping[str, str]
        Imported module name associated with each stage.
    dependencies : Mapping[str, tuple[str, ...]]
        Upstream silver-stage dependencies for each registered stage.
    external_dependencies : Mapping[str, tuple[str, ...]]
        Human-readable raw or controlled inputs produced outside this workflow.
    executors : Mapping[str, StageExecutor]
        Execution strategy and target for each stage.
    """

    stage_order: tuple[str, ...]
    stage_modules: Mapping[str, str]
    dependencies: Mapping[str, tuple[str, ...]]
    external_dependencies: Mapping[str, tuple[str, ...]]
    executors: Mapping[str, StageExecutor]


@dataclass
class SilverWorkflowState:
    """Mutable state accumulated during one orchestrated silver build.

    Attributes
    ----------
    completed_stages : set[str]
        Stages that completed successfully during the current workflow state.
    stage_results : dict[str, Any]
        Return values from function stages and ``CompletedProcess`` records from
        script stages, keyed by stage name.
    history : list[WorkflowHistoryRecord]
        Ordered execution history containing stage status, timing, and exception
        metadata.
    """

    completed_stages: set[str]
    stage_results: dict[str, Any]
    history: list[WorkflowHistoryRecord]

    @classmethod
    def empty(cls) -> SilverWorkflowState:
        """Create an empty workflow state container.

        Returns
        -------
        SilverWorkflowState
            New state with no completed stages, results, or history records.
        """

        return cls(completed_stages=set(), stage_results={}, history=[])

    def reset(self) -> None:
        """Clear all completion markers, results, and history records.

        Returns
        -------
        None
        """

        self.completed_stages.clear()
        self.stage_results.clear()
        self.history.clear()


# =============================================================================
# Canonical workflow registration
# =============================================================================

STAGE_MODULE_NAMES: dict[str, str] = {
    "legacy_inputs": "map_legacy_inputs",
    "emissions": "build_emissions",
    "h2_pipeline_costs": "build_h2_pipeline_costs",
    "h2_pipeline_cost_models": "build_h2_pipeline_cost_models",
    "basemaps": "build_basemaps",
    "adjacency": "build_region_adjacency",
    "roads": "build_roads",
    "road_connectivity": "map_roads",
}

SILVER_STAGE_ORDER = (
    "legacy_inputs",
    "emissions",
    "h2_pipeline_costs",
    "h2_pipeline_cost_models",
    "basemaps",
    "adjacency",
    "roads",
    "road_connectivity",
)

SILVER_STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "legacy_inputs": (),
    "emissions": (),
    "h2_pipeline_costs": (),
    "h2_pipeline_cost_models": ("h2_pipeline_costs",),
    "basemaps": (),
    "adjacency": ("basemaps",),
    "roads": (),
    "road_connectivity": ("basemaps", "adjacency", "roads"),
}

EXTERNAL_INPUT_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "legacy_inputs": (
        "raw provincial boundary shapefile",
        "data_files/sites_full.csv",
        "data_files/demand.csv",
    ),
    "emissions": (
        "raw emissions CSV",
        "raw emissions GeoJSON",
    ),
    "h2_pipeline_costs": ("H2 pipeline master cost workbook",),
    "h2_pipeline_cost_models": (),
    "basemaps": ("raw provincial boundary shapefile",),
    "adjacency": (),
    "roads": ("raw provincial and territorial NRN GeoPackages",),
    "road_connectivity": (),
}


# =============================================================================
# Console helpers
# =============================================================================


def print_header(title: str, character: str = "=") -> None:
    """Print a formatted console section heading.

    Parameters
    ----------
    title : str
        Heading text to print.
    character : str, default="="
        Single-character separator used above and below the heading.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If ``character`` does not contain exactly one character.
    """

    if len(character) != 1:
        raise ValueError("Header separator must contain exactly one character.")

    print("\n" + character * 78)
    print(title)
    print(character * 78)


# =============================================================================
# Module loading and executor construction
# =============================================================================


def import_stage_modules(
    module_names: Mapping[str, str],
) -> dict[str, ModuleType]:
    """Import every module registered in the silver workflow.

    All imports are attempted so the resulting error reports every unavailable
    stage module rather than stopping at the first failure. Successfully imported
    modules are returned under their canonical stage names.

    Parameters
    ----------
    module_names : Mapping[str, str]
        Mapping from canonical silver-stage names to importable module names.

    Returns
    -------
    dict[str, ModuleType]
        Imported modules keyed by canonical stage name.

    Raises
    ------
    ImportError
        If one or more registered stage modules cannot be imported.
    """

    imported_modules: dict[str, ModuleType] = {}
    failures: list[str] = []

    print_header("Silver-stage module imports")

    for stage_name, module_name in module_names.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # Imports may raise dependency-specific errors.
            status = f"failed: {type(exc).__name__}: {exc}"
            failures.append(f"{stage_name} ({module_name}): {status}")
        else:
            imported_modules[stage_name] = module
            status = "imported"

        print(f"{stage_name:<25}{status:<45}{module_name}")

    print("\nImport summary")
    print("-" * 78)
    print(f"Configured modules: {len(module_names):,}")
    print(f"Imported modules:   {len(imported_modules):,}")
    print(f"Import failures:    {len(failures):,}")

    if failures:
        raise ImportError(
            "One or more silver-stage modules could not be imported:\n  - "
            + "\n  - ".join(failures)
        )

    return imported_modules


def require_stage_callable(
    module: ModuleType,
    callable_name: str,
    stage_name: str,
) -> StageCallable:
    """Return and validate a stage function exposed by an imported module.

    Parameters
    ----------
    module : ModuleType
        Imported stage module expected to expose the requested function.
    callable_name : str
        Attribute name of the required stage function.
    stage_name : str
        Canonical stage name used in validation error messages.

    Returns
    -------
    StageCallable
        Validated callable stage function.

    Raises
    ------
    AttributeError
        If the module does not expose ``callable_name``.
    TypeError
        If the exposed attribute is not callable.
    """

    stage_callable = getattr(module, callable_name, None)

    if stage_callable is None:
        raise AttributeError(
            f"Stage {stage_name!r} module {module.__name__!r} does not expose "
            f"{callable_name!r}."
        )

    if not callable(stage_callable):
        raise TypeError(
            f"Stage {stage_name!r} attribute {callable_name!r} is not callable."
        )

    return stage_callable


def build_stage_executors(
    stage_modules: Mapping[str, ModuleType],
) -> dict[str, StageExecutor]:
    """Construct the canonical execution registry for all silver stages.

    Function executors are resolved from imported stage modules. Script executors
    point to the existing standalone scripts and intentionally receive no extra
    command-line arguments because those scripts use their own canonical input and
    output paths.

    Parameters
    ----------
    stage_modules : Mapping[str, ModuleType]
        Imported modules keyed by canonical stage name.

    Returns
    -------
    dict[str, StageExecutor]
        Stage executor definitions keyed by canonical stage name.
    """

    return {
        "legacy_inputs": FunctionExecutor(
            require_stage_callable(
                stage_modules["legacy_inputs"],
                "run_legacy_input_mapping",
                "legacy_inputs",
            )
        ),
        "emissions": ScriptExecutor(SCRIPTS_DIR / "build_emissions.py"),
        "h2_pipeline_costs": ScriptExecutor(
            SCRIPTS_DIR / "build_h2_pipeline_costs.py"
        ),
        "h2_pipeline_cost_models": ScriptExecutor(
            SCRIPTS_DIR / "build_h2_pipeline_cost_models.py"
        ),
        "basemaps": FunctionExecutor(
            require_stage_callable(
                stage_modules["basemaps"],
                "run_basemap_build",
                "basemaps",
            )
        ),
        "adjacency": FunctionExecutor(
            require_stage_callable(
                stage_modules["adjacency"],
                "run_adjacency_build",
                "adjacency",
            )
        ),
        "roads": FunctionExecutor(
            require_stage_callable(
                stage_modules["roads"],
                "run_road_build",
                "roads",
            )
        ),
        "road_connectivity": FunctionExecutor(
            require_stage_callable(
                stage_modules["road_connectivity"],
                "run_road_connectivity_build",
                "road_connectivity",
            )
        ),
    }


# =============================================================================
# Workflow-definition validation
# =============================================================================


def validate_workflow_definition(
    definition: SilverWorkflowDefinition,
) -> None:
    """Validate stage registration, dependencies, order, and execution targets.

    The validation requires every ordered stage to have a module, dependency
    entry, external-dependency entry, and executor. It rejects unknown or
    unordered registry entries, dependencies on unknown stages, dependencies that
    appear after their consumers, missing scripts, and unsupported executor
    objects.

    Parameters
    ----------
    definition : SilverWorkflowDefinition
        Workflow registry to validate before external inputs or processing are
        evaluated.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If stage registries are inconsistent or dependency order is invalid.
    TypeError
        If an executor has an unsupported type or invalid callable.
    FileNotFoundError
        If a script executor points to a missing file.
    """

    ordered = set(definition.stage_order)
    registries: dict[str, set[str]] = {
        "module registry": set(definition.stage_modules),
        "dependency registry": set(definition.dependencies),
        "external-input registry": set(definition.external_dependencies),
        "executor registry": set(definition.executors),
    }

    errors: list[str] = []

    if len(definition.stage_order) != len(ordered):
        errors.append("The canonical stage order contains duplicate stages.")

    for registry_name, registered in registries.items():
        missing = sorted(ordered - registered)
        extra = sorted(registered - ordered)

        if missing:
            errors.append(f"{registry_name} is missing stages: {missing}")
        if extra:
            errors.append(f"{registry_name} contains unordered stages: {extra}")

    unknown_dependencies = sorted(
        {
            dependency
            for dependencies in definition.dependencies.values()
            for dependency in dependencies
            if dependency not in ordered
        }
    )
    if unknown_dependencies:
        errors.append(
            "The dependency graph references unknown stages: "
            f"{unknown_dependencies}"
        )

    positions = {
        stage_name: index
        for index, stage_name in enumerate(definition.stage_order)
    }
    invalid_order = [
        (stage_name, dependency)
        for stage_name, dependencies in definition.dependencies.items()
        for dependency in dependencies
        if (
            stage_name in positions
            and dependency in positions
            and positions[dependency] >= positions[stage_name]
        )
    ]
    if invalid_order:
        errors.append(
            "Dependencies must precede their dependent stages: "
            + ", ".join(
                f"{stage} depends on {dependency}"
                for stage, dependency in invalid_order
            )
        )

    if errors:
        raise ValueError("Invalid silver workflow definition:\n  - " + "\n  - ".join(errors))

    for stage_name, executor in definition.executors.items():
        if isinstance(executor, FunctionExecutor):
            if not callable(executor.callable):
                raise TypeError(
                    f"Function executor for {stage_name!r} is not callable."
                )
        elif isinstance(executor, ScriptExecutor):
            if not executor.path.is_file():
                raise FileNotFoundError(
                    f"Script executor for {stage_name!r} does not exist: "
                    f"{executor.path}"
                )
        else:
            raise TypeError(
                f"Unsupported executor type for {stage_name!r}: "
                f"{type(executor).__name__}"
            )


def print_workflow_summary(definition: SilverWorkflowDefinition) -> None:
    """Print the validated stage order, targets, and dependencies.

    Parameters
    ----------
    definition : SilverWorkflowDefinition
        Validated workflow definition to summarize.

    Returns
    -------
    None
    """

    print_header("Silver workflow definition")

    for index, stage_name in enumerate(definition.stage_order, start=1):
        executor = definition.executors[stage_name]
        stage_dependencies = definition.dependencies[stage_name]
        external_dependencies = definition.external_dependencies[stage_name]

        if isinstance(executor, FunctionExecutor):
            execution_target = executor.callable.__name__
        else:
            execution_target = str(executor.path)

        print(f"\n{index:>2}. {stage_name}")
        print(f"    Execution method:    {executor.method}")
        print(f"    Execution target:    {execution_target}")
        print(
            "    Silver dependencies: "
            + (", ".join(stage_dependencies) or "none")
        )
        print(
            "    External inputs:     "
            + (", ".join(external_dependencies) or "none")
        )

    function_count = sum(
        isinstance(executor, FunctionExecutor)
        for executor in definition.executors.values()
    )
    script_count = len(definition.executors) - function_count

    print("\nWorkflow validation complete.")
    print(f"Registered stages: {len(definition.stage_order):,}")
    print(f"Function stages:   {function_count:,}")
    print(f"Script stages:     {script_count:,}")


# =============================================================================
# External-input validation
# =============================================================================


def add_external_input_check(
    checks: list[ExternalInputCheck],
    *,
    stage_name: str,
    input_name: str,
    path: Path,
    status: str,
    details: str | None = None,
) -> None:
    """Append one structured external-input validation result.

    Parameters
    ----------
    checks : list[ExternalInputCheck]
        Validation result list to update.
    stage_name : str
        Silver stage that consumes the external input.
    input_name : str
        Human-readable input description.
    path : Path
        Expected file or directory path.
    status : str
        Validation result, such as ``"found"``, ``"missing"``, or ``"invalid"``.
    details : str | None, optional
        Additional explanation for the validation result.

    Returns
    -------
    None
    """

    checks.append(
        {
            "stage": stage_name,
            "input": input_name,
            "path": path,
            "status": status,
            "details": details,
        }
    )


def check_required_file(
    checks: list[ExternalInputCheck],
    *,
    stage_name: str,
    input_name: str,
    path: Path,
) -> None:
    """Validate and record one required external file.

    Parameters
    ----------
    checks : list[ExternalInputCheck]
        Validation result list to update.
    stage_name : str
        Silver stage that consumes the file.
    input_name : str
        Human-readable input description.
    path : Path
        Required file path.

    Returns
    -------
    None
    """

    add_external_input_check(
        checks,
        stage_name=stage_name,
        input_name=input_name,
        path=path,
        status="found" if path.is_file() else "missing",
    )


def validate_external_inputs(
    config: GeospatialBuildConfig,
    stage_modules: Mapping[str, ModuleType],
) -> list[ExternalInputCheck]:
    """Validate raw and controlled inputs consumed by silver stages.

    The validation checks for exactly one raw province-boundary shapefile, the
    legacy site and demand tables, both emissions source files, the controlled H2
    pipeline workbook, and exactly one English NRN GeoPackage for every province
    or territory selected by the build profile.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Loaded build profile defining the selected jurisdictions.
    stage_modules : Mapping[str, ModuleType]
        Imported stage modules used to resolve canonical emissions and cost input
        paths.

    Returns
    -------
    list[ExternalInputCheck]
        Complete validation report in stage and input order.

    Raises
    ------
    AttributeError
        If a stage module does not expose a path constant or helper required to
        resolve its canonical external inputs.
    FileNotFoundError
        If one or more required files are missing or ambiguous.
    """

    checks: list[ExternalInputCheck] = []
    raw_boundary_dir = DATA_FILES / "raw" / "basemaps"
    raw_boundary_paths = sorted(raw_boundary_dir.glob("*.shp"))

    if len(raw_boundary_paths) == 1:
        boundary_path = raw_boundary_paths[0]
        boundary_status = "found"
        boundary_details = None
    else:
        boundary_path = raw_boundary_dir
        boundary_status = "invalid"
        boundary_details = (
            "Expected exactly one boundary shapefile, found "
            f"{len(raw_boundary_paths)}: "
            f"{[path.name for path in raw_boundary_paths]}"
        )

    for dependent_stage in ("legacy_inputs", "basemaps"):
        add_external_input_check(
            checks,
            stage_name=dependent_stage,
            input_name="raw provincial boundary shapefile",
            path=boundary_path,
            status=boundary_status,
            details=boundary_details,
        )

    check_required_file(
        checks,
        stage_name="legacy_inputs",
        input_name="legacy site table",
        path=DATA_FILES / "sites_full.csv",
    )
    check_required_file(
        checks,
        stage_name="legacy_inputs",
        input_name="legacy demand table",
        path=DATA_FILES / "demand.csv",
    )

    emissions_module = stage_modules["emissions"]
    raw_emissions_csv = Path(getattr(emissions_module, "CO2_CSV_PATH"))
    raw_emissions_geojson = Path(getattr(emissions_module, "CO2_JSON_PATH"))

    check_required_file(
        checks,
        stage_name="emissions",
        input_name="raw emissions CSV",
        path=raw_emissions_csv,
    )
    check_required_file(
        checks,
        stage_name="emissions",
        input_name="raw emissions GeoJSON",
        path=raw_emissions_geojson,
    )

    cost_module = stage_modules["h2_pipeline_costs"]
    workbook_path_resolver = getattr(cost_module, "default_workbook_path", None)
    if not callable(workbook_path_resolver):
        raise AttributeError(
            "build_h2_pipeline_costs must expose default_workbook_path()."
        )

    check_required_file(
        checks,
        stage_name="h2_pipeline_costs",
        input_name="H2 pipeline master cost workbook",
        path=Path(workbook_path_resolver(PROJECT_ROOT)),
    )

    raw_nrn_dir = DATA_FILES / "raw" / "nrn"
    for province in config.study_area.provinces:
        province_directory = raw_nrn_dir / province
        matches = (
            sorted(province_directory.glob("*_en.gpkg"))
            if province_directory.is_dir()
            else []
        )

        if len(matches) == 1:
            nrn_path = matches[0]
            status = "found"
            details = None
        elif not province_directory.is_dir():
            nrn_path = province_directory
            status = "missing"
            details = "Province or territory NRN directory does not exist."
        else:
            nrn_path = province_directory
            status = "invalid"
            details = (
                "Expected exactly one English NRN GeoPackage matching "
                f"'*_en.gpkg', found {len(matches)}: "
                f"{[path.name for path in matches]}"
            )

        add_external_input_check(
            checks,
            stage_name="roads",
            input_name=f"{province} English NRN GeoPackage",
            path=nrn_path,
            status=status,
            details=details,
        )

    print_external_input_report(checks)

    failures = [check for check in checks if check["status"] != "found"]
    if failures:
        failure_details = "\n".join(
            f"  - {check['stage']} | {check['input']} | {check['path']}"
            for check in failures
        )
        raise FileNotFoundError(
            "One or more required external silver-stage inputs are missing "
            f"or invalid:\n{failure_details}"
        )

    return checks


def print_external_input_report(
    checks: Sequence[ExternalInputCheck],
) -> None:
    """Print a grouped report of external-input validation results.

    Parameters
    ----------
    checks : Sequence[ExternalInputCheck]
        External-input validation records to report.

    Returns
    -------
    None
    """

    print_header("External silver-input validation")
    current_stage: str | None = None

    for result in checks:
        if result["stage"] != current_stage:
            print(f"\n{result['stage']}")
            current_stage = result["stage"]

        print(f"  [{result['status'].upper():<7}] {result['input']}")
        print(f"            {result['path']}")
        if result["details"] is not None:
            print(f"            {result['details']}")

    failures = sum(result["status"] != "found" for result in checks)
    print("\nExternal-input summary")
    print("-" * 78)
    print(f"Input checks:   {len(checks):,}")
    print(f"Inputs found:   {len(checks) - failures:,}")
    print(f"Input failures: {failures:,}")


# =============================================================================
# Stage and workflow execution
# =============================================================================


def validate_stage_dependencies(
    stage_name: str,
    definition: SilverWorkflowDefinition,
    state: SilverWorkflowState,
) -> None:
    """Confirm that all upstream silver stages have completed.

    Parameters
    ----------
    stage_name : str
        Stage that is about to execute.
    definition : SilverWorkflowDefinition
        Validated workflow definition containing the dependency graph.
    state : SilverWorkflowState
        Current workflow state containing completed-stage markers.

    Returns
    -------
    None

    Raises
    ------
    KeyError
        If ``stage_name`` is not registered in the dependency graph.
    RuntimeError
        If one or more required upstream stages have not completed.
    """

    if stage_name not in definition.dependencies:
        raise KeyError(f"Unknown silver stage: {stage_name!r}")

    incomplete = [
        dependency
        for dependency in definition.dependencies[stage_name]
        if dependency not in state.completed_stages
    ]
    if incomplete:
        raise RuntimeError(
            f"Cannot execute {stage_name!r}. The following upstream stages "
            f"have not completed: {incomplete}"
        )


def execute_silver_stage(
    stage_name: str,
    *,
    config: GeospatialBuildConfig,
    definition: SilverWorkflowDefinition,
    state: SilverWorkflowState,
    enforce_dependencies: bool = True,
) -> Any:
    """Execute one registered silver preprocessing stage.

    Function stages execute in the current process and receive ``config``.
    Script stages execute with the active Python interpreter in a subprocess whose
    working directory is the repository root. Successful results are written to
    ``state`` before being returned.

    Parameters
    ----------
    stage_name : str
        Canonical silver-stage name to execute.
    config : GeospatialBuildConfig
        Loaded build profile passed to function stages.
    definition : SilverWorkflowDefinition
        Validated workflow definition containing stage executors and dependencies.
    state : SilverWorkflowState
        Mutable workflow state updated after successful execution.
    enforce_dependencies : bool, default=True
        Whether all registered upstream dependencies must already be completed.

    Returns
    -------
    Any
        Function return value or ``subprocess.CompletedProcess`` record.

    Raises
    ------
    KeyError
        If no executor is registered for ``stage_name``.
    RuntimeError
        If an upstream dependency is incomplete.
    subprocess.CalledProcessError
        If a script stage exits with a nonzero return code.
    """

    if stage_name not in definition.executors:
        raise KeyError(f"No executor is registered for stage {stage_name!r}.")

    if enforce_dependencies:
        validate_stage_dependencies(stage_name, definition, state)

    executor = definition.executors[stage_name]
    print_header(f"Executing silver stage: {stage_name}")
    print(f"Execution method: {executor.method}")

    start_time = perf_counter()
    try:
        if isinstance(executor, FunctionExecutor):
            result = executor.callable(config)
        else:
            result = subprocess.run(
                [sys.executable, str(executor.path), *executor.arguments],
                cwd=PROJECT_ROOT,
                check=True,
            )
    except Exception:
        elapsed_seconds = perf_counter() - start_time
        print_header(f"Stage failed: {stage_name}", character="-")
        print(f"Elapsed time: {elapsed_seconds:,.2f} seconds")
        raise

    elapsed_seconds = perf_counter() - start_time
    state.completed_stages.add(stage_name)
    state.stage_results[stage_name] = result

    print_header(f"Stage completed: {stage_name}", character="-")
    print(f"Elapsed time: {elapsed_seconds:,.2f} seconds")
    return result


def validate_requested_stages(
    stages: Sequence[str],
    canonical_order: Sequence[str],
) -> tuple[str, ...]:
    """Validate and normalize a requested stage sequence.

    Parameters
    ----------
    stages : Sequence[str]
        Requested stage names in intended execution order.
    canonical_order : Sequence[str]
        Complete canonical stage order.

    Returns
    -------
    tuple[str, ...]
        Validated stage sequence.

    Raises
    ------
    ValueError
        If unknown or duplicate stages are requested, or if their order differs
        from the canonical workflow order.
    """

    selected = tuple(stages)
    unknown = [stage for stage in selected if stage not in canonical_order]
    if unknown:
        raise ValueError(f"Requested workflow contains unknown stages: {unknown}")

    if len(selected) != len(set(selected)):
        raise ValueError(f"Requested workflow contains duplicate stages: {selected}")

    positions = [canonical_order.index(stage) for stage in selected]
    if positions != sorted(positions):
        raise ValueError(
            "Requested stages must follow the canonical silver-stage order: "
            f"{selected}"
        )

    return selected


def run_silver_workflow(
    *,
    config: GeospatialBuildConfig,
    definition: SilverWorkflowDefinition,
    state: SilverWorkflowState,
    stages: Sequence[str] | None = None,
    reset_state: bool = True,
) -> dict[str, Any]:
    """Execute the selected silver stages in canonical dependency order.

    Every registered stage runs when ``stages`` is omitted. A subset may be
    supplied, but its dependencies must either appear earlier in the requested
    sequence or already exist in ``state`` when ``reset_state`` is false. Execution
    stops immediately on failure while preserving records for stages that already
    completed.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Loaded build profile used by function stages.
    definition : SilverWorkflowDefinition
        Validated workflow definition.
    state : SilverWorkflowState
        Mutable execution state.
    stages : Sequence[str] | None, optional
        Ordered subset of stages to execute. When omitted, the complete canonical
        workflow is run.
    reset_state : bool, default=True
        Whether prior completion markers, results, and history are cleared first.

    Returns
    -------
    dict[str, Any]
        Results for all stages currently stored in ``state``.

    Raises
    ------
    ValueError
        If the requested stage sequence is invalid.
    RuntimeError
        If a required upstream dependency is incomplete.
    Exception
        Propagates the original exception raised by a failed stage.
    """

    selected_stages = validate_requested_stages(
        definition.stage_order if stages is None else stages,
        definition.stage_order,
    )

    if reset_state:
        state.reset()

    workflow_start = perf_counter()
    print_header("Starting silver-layer workflow")
    print(f"Build profile: {config.source_path}")
    print(f"Study area:    {config.study_area.label}")
    print(f"Stage count:   {len(selected_stages):,}")

    for index, stage_name in enumerate(selected_stages, start=1):
        print(f"\nWorkflow stage {index:,}/{len(selected_stages):,}: {stage_name}")
        stage_start = perf_counter()

        try:
            execute_silver_stage(
                stage_name,
                config=config,
                definition=definition,
                state=state,
            )
        except Exception as exc:
            stage_elapsed = perf_counter() - stage_start
            state.history.append(
                {
                    "stage": stage_name,
                    "status": "failed",
                    "elapsed_seconds": stage_elapsed,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                }
            )

            print_header("Silver-layer workflow failed")
            print(f"Failed stage:   {stage_name}")
            print(f"Exception type: {type(exc).__name__}")
            print(f"Exception:      {exc}")
            print(
                f"Total elapsed:  {perf_counter() - workflow_start:,.2f} seconds"
            )
            raise

        state.history.append(
            {
                "stage": stage_name,
                "status": "completed",
                "elapsed_seconds": perf_counter() - stage_start,
                "exception_type": None,
                "exception_message": None,
            }
        )

    print_header("Silver-layer workflow completed")
    print(
        f"Completed stages: {len(state.completed_stages):,}/"
        f"{len(selected_stages):,}"
    )
    print(f"Total elapsed:    {perf_counter() - workflow_start:,.2f} seconds")
    print_stage_timing(state.history)

    return dict(state.stage_results)


def print_stage_timing(history: Sequence[WorkflowHistoryRecord]) -> None:
    """Print stage status and elapsed time from workflow history.

    Parameters
    ----------
    history : Sequence[WorkflowHistoryRecord]
        Ordered stage execution records.

    Returns
    -------
    None
    """

    print("\nStage timing summary")
    print("-" * 78)
    for record in history:
        print(
            f"{record['stage']:<28}"
            f"{record['status']:<12}"
            f"{record['elapsed_seconds']:>12,.2f} seconds"
        )


# =============================================================================
# Post-run verification
# =============================================================================


def verify_silver_build(
    *,
    config: GeospatialBuildConfig,
    definition: SilverWorkflowDefinition,
    state: SilverWorkflowState,
    expected_stages: Sequence[str],
) -> None:
    """Verify stage completion and populated processed-layer directories.

    Verification checks that each expected stage completed exactly once, produced a
    stored result, and did not create a failed history record. It also confirms the
    existence of the processed root and the major silver-layer subdirectories, then
    inventories all processed files by suffix.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Build profile used for the completed workflow.
    definition : SilverWorkflowDefinition
        Validated workflow definition used to order the stage report.
    state : SilverWorkflowState
        Final execution state to verify.
    expected_stages : Sequence[str]
        Stages expected to have completed during this invocation.

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        If completion records, results, processed directories, or processed files
        fail verification.
    """

    expected = set(expected_stages)
    missing_completed = sorted(expected - state.completed_stages)
    missing_results = sorted(expected - set(state.stage_results))
    failed_records = [record for record in state.history if record["status"] != "completed"]

    history_stage_counts = {
        stage: sum(record["stage"] == stage for record in state.history)
        for stage in expected
    }
    duplicate_history = sorted(
        stage for stage, count in history_stage_counts.items() if count > 1
    )

    processed_root = DATA_FILES / "processed"
    processed_directories = {
        "processed root": processed_root,
        "legacy inputs": processed_root / "legacy_inputs",
        "emissions": processed_root / "emissions",
        "costs": processed_root / "costs",
        "basemaps": processed_root / "basemaps",
        "graph": processed_root / "graph",
        "road networks": processed_root / "nrn",
        "road connectivity": processed_root / "road_connectivity",
    }
    missing_directories = {
        label: path
        for label, path in processed_directories.items()
        if not path.is_dir()
    }
    processed_files = (
        sorted(path for path in processed_root.rglob("*") if path.is_file())
        if processed_root.is_dir()
        else []
    )

    files_by_suffix: dict[str, int] = {}
    for path in processed_files:
        suffix = path.suffix.lower() or "<no suffix>"
        files_by_suffix[suffix] = files_by_suffix.get(suffix, 0) + 1

    print_header("Silver build verification")
    print(f"Build profile:    {config.source_path}")
    print(f"Study area:       {config.study_area.label}")
    print(f"Completed stages: {len(state.completed_stages):,}/{len(expected):,}")
    print(f"Workflow records: {len(state.history):,}")
    print(f"Stored results:   {len(state.stage_results):,}")
    print(f"Processed files:  {len(processed_files):,}")

    print("\nStage status")
    print("-" * 78)
    for stage_name in definition.stage_order:
        if stage_name not in expected:
            status = "not requested"
        elif stage_name in state.completed_stages:
            status = "completed"
        else:
            status = "missing"
        print(f"{stage_name:<28}{status}")

    print_stage_timing(state.history)

    print("\nProcessed directories")
    print("-" * 78)
    for label, path in processed_directories.items():
        status = "found" if path.is_dir() else "missing"
        print(f"{label:<24}{status:<10}{path}")

    print("\nProcessed file types")
    print("-" * 78)
    if files_by_suffix:
        for suffix, count in sorted(files_by_suffix.items()):
            print(f"{suffix:<16}{count:>8,}")
    else:
        print("No processed files found.")

    errors: list[str] = []
    if missing_completed:
        errors.append(f"Missing completed stages: {missing_completed}")
    if missing_results:
        errors.append(f"Missing stage-result entries: {missing_results}")
    if failed_records:
        errors.append(f"Failed workflow records: {failed_records}")
    if duplicate_history:
        errors.append(f"Duplicate workflow records: {duplicate_history}")
    if missing_directories:
        errors.append(
            "Missing processed directories: "
            + ", ".join(
                f"{label}={path}" for label, path in missing_directories.items()
            )
        )
    if not processed_files:
        errors.append(f"No files were found under {processed_root}")

    if errors:
        raise RuntimeError("Silver build verification failed:\n  - " + "\n  - ".join(errors))

    print("\nSilver build verification passed.")


# =============================================================================
# Command-line interface
# =============================================================================


def default_config_path() -> Path:
    """Return the preferred project-level silver build-profile path.

    The current repository layout is checked first for
    ``config/provinces_only.toml``. The newer ``config/build_profiles/`` layout is
    used as a fallback so the orchestrator remains compatible while configuration
    files are reorganized.

    Returns
    -------
    Path
        Preferred default TOML build-profile path.
    """

    candidates = (
        CONFIG_DIR / "provinces_only.toml",
        CONFIG_DIR / "build_profiles" / "provinces_only.toml",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the silver-layer orchestrator.

    Returns
    -------
    argparse.Namespace
        Parsed build-profile path, optional stage subset, and validation-only
        selection.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Validate and build the Geospatial-CANOE silver preprocessing layer."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help=(
            "Path to the shared geospatial TOML build profile. Defaults to the "
            "project provinces_only profile."
        ),
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=SILVER_STAGE_ORDER,
        help=(
            "Ordered subset of silver stages to run. Omit to execute the complete "
            "workflow."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Validate modules, workflow registration, and external inputs without "
            "executing any processing stages."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Validate and execute the configured Geospatial-CANOE silver workflow.

    The command-line build profile is loaded first. Stage modules and executors are
    then registered and validated, followed by raw external inputs. Unless
    ``--validate-only`` is selected, the requested stages are executed and the
    resulting processed layer is verified.

    Returns
    -------
    None
    """

    args = parse_args()
    config_path = args.config.resolve()

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Build-profile TOML file not found: {config_path}"
        )

    print_header("Geospatial-CANOE silver build")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Scripts:      {SCRIPTS_DIR}")
    print(f"Data files:   {DATA_FILES}")
    print(f"Config:       {config_path}")

    build_config = load_geospatial_build_config(config_path)
    print_build_config(build_config)

    stage_modules = import_stage_modules(STAGE_MODULE_NAMES)
    definition = SilverWorkflowDefinition(
        stage_order=SILVER_STAGE_ORDER,
        stage_modules=STAGE_MODULE_NAMES,
        dependencies=SILVER_STAGE_DEPENDENCIES,
        external_dependencies=EXTERNAL_INPUT_DEPENDENCIES,
        executors=build_stage_executors(stage_modules),
    )

    validate_workflow_definition(definition)
    print_workflow_summary(definition)
    validate_external_inputs(build_config, stage_modules)

    if args.validate_only:
        print("\nSilver workflow validation passed. No stages were executed.")
        return

    selected_stages = (
        tuple(args.stages)
        if args.stages is not None
        else definition.stage_order
    )
    state = SilverWorkflowState.empty()

    run_silver_workflow(
        config=build_config,
        definition=definition,
        state=state,
        stages=selected_stages,
    )
    verify_silver_build(
        config=build_config,
        definition=definition,
        state=state,
        expected_stages=selected_stages,
    )


if __name__ == "__main__":
    main()
