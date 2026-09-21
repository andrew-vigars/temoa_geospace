"""Bronze-layer execution orchestration for Geospatial-CANOE.

This module contains the reusable execution logic for coordinating raw-data
acquisition for Geospatial-CANOE. It is the importable implementation used by
the command-line entry point in ``scripts/build_bronze.py``.

The bronze layer downloads or copies the external, unmodified source data that
every later stage depends on: Statistics Canada basemap and dissemination-area
boundaries, Census population data, Aboriginal Lands boundaries, National Road
Network GeoPackages, National Hydro Network hydrography, large-facility
emissions data, and an acquired CanCO2 unified-storage release.
The gasoline-demand stage acquires provincial and territorial motor-fuel sales
and population-centre boundaries while reusing dissemination-area population
from the independent residential stage.
Unlike the silver workflow, bronze stages are
mutually independent: none of them reads another stage's output, so this
module runs them without a dependency graph or a build profile.

Individual acquisition logic remains implemented in ``geocanoe.acquisition``.
This module is responsible only for orchestration: stage registration, module
import, stage execution, timing, and a final summary.

Inputs
------
Command-line options
    Optional stage subset, overwrite behaviour, and per-stage output
    directories or CanCO2 source paths.

Outputs
-------
data_files/raw/
    Raw-layer inputs written by the individual acquisition modules.

Notes
-----
This module does not parse command-line arguments. That responsibility
remains with ``scripts/build_bronze.py``.

The bronze layer does not build any silver or gold product. Preprocessing
these raw inputs into standardized intermediate products is a downstream
silver-layer transformation handled by ``geocanoe.execution.silver``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Mapping, Sequence, TypedDict

from geocanoe.paths import find_project_root

# =============================================================================
# Project discovery
# =============================================================================

PROJECT_ROOT = find_project_root()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_FILES = PROJECT_ROOT / "data_files"


# =============================================================================
# Stage registry
# =============================================================================

STAGE_MODULE_NAMES: dict[str, str] = {
    "basemaps": "geocanoe.acquisition.basemaps",
    "residential": "geocanoe.acquisition.residential",
    "gasoline_demand": "geocanoe.acquisition.gasoline_demand",
    "aboriginal_lands": "geocanoe.acquisition.aboriginal_lands",
    "nrn": "geocanoe.acquisition.nrn",
    "nhn": "geocanoe.acquisition.nhn",
    "emissions": "geocanoe.acquisition.emissions",
    "co2_storage": "geocanoe.acquisition.co2_storage",
}

BRONZE_STAGE_ORDER = (
    "basemaps",
    "residential",
    "gasoline_demand",
    "aboriginal_lands",
    "nrn",
    "nhn",
    "emissions",
    "co2_storage",
)


class WorkflowHistoryRecord(TypedDict):
    """One completed bronze-stage execution record."""

    stage: str
    status: str
    elapsed_seconds: float


@dataclass
class BronzeStageOptions:
    """Per-stage arguments threaded through from the command line.

    Attributes
    ----------
    overwrite : bool
        Whether every stage should redownload or recopy outputs that already
        exist.
    raw_basemap_dir : Path | None
        Output directory override for the ``basemaps`` stage.
    raw_residential_dir : Path | None
        Output directory override for the ``residential`` stage.
    raw_gasoline_demand_dir : Path | None
        Output directory override for the ``gasoline_demand`` stage.
    raw_aboriginal_lands_dir : Path | None
        Output directory override for the ``aboriginal_lands`` stage.
    keep_aboriginal_lands_archive : bool
        Whether the ``aboriginal_lands`` stage should retain its source ZIP.
    raw_nrn_dir : Path | None
        Output directory override for the ``nrn`` stage.
    raw_nhn_dir : Path | None
        Output directory override for the ``nhn`` stage.
    keep_nhn_archive : bool
        Whether the ``nhn`` stage should retain its approximately 15 GB ZIP.
    co2_source_gpkg : Path | None
        Exact CanCO2 unified-storage GeoPackage for the ``co2_storage`` stage.
    co2_source_repo : Path | None
        CanCO2 repository root for the ``co2_storage`` stage.
    """

    overwrite: bool = False
    raw_basemap_dir: Path | None = None
    raw_residential_dir: Path | None = None
    raw_gasoline_demand_dir: Path | None = None
    raw_aboriginal_lands_dir: Path | None = None
    keep_aboriginal_lands_archive: bool = False
    raw_nrn_dir: Path | None = None
    raw_nhn_dir: Path | None = None
    keep_nhn_archive: bool = False
    co2_source_gpkg: Path | None = None
    co2_source_repo: Path | None = None


# =============================================================================
# Console helpers
# =============================================================================


def print_header(title: str, character: str = "=") -> None:
    """Print a formatted console section heading."""

    rule = character * max(len(title), 8)
    print(f"\n{rule}\n{title}\n{rule}")


def print_stage_timing(history: Sequence[WorkflowHistoryRecord]) -> None:
    """Print stage status and elapsed time from workflow history."""

    print("\nStage timing summary")
    print("-" * 78)
    for record in history:
        print(
            f"{record['stage']:<28}"
            f"{record['status']:<12}"
            f"{record['elapsed_seconds']:>12,.2f} seconds"
        )


# =============================================================================
# Module import
# =============================================================================


def import_stage_modules(
    module_names: Mapping[str, str],
) -> dict[str, ModuleType]:
    """Import every module registered in the bronze workflow.

    All imports are attempted so the resulting error reports every unavailable
    stage module rather than stopping at the first failure.

    Parameters
    ----------
    module_names : Mapping[str, str]
        Mapping from canonical bronze-stage names to importable module names.

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

    print_header("Bronze-stage module imports")

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

    if failures:
        raise ImportError(
            "One or more bronze-stage modules could not be imported:\n  - "
            + "\n  - ".join(failures)
        )

    return imported_modules


# =============================================================================
# Stage execution
# =============================================================================


def execute_bronze_stage(
    stage_name: str,
    module: ModuleType,
    options: BronzeStageOptions,
) -> object:
    """Run one bronze acquisition stage and return its acquisition result.

    Each acquisition module exposes a differently shaped entry function, so
    this dispatcher supplies the arguments appropriate to the requested stage
    rather than assuming a uniform call signature.

    Parameters
    ----------
    stage_name : str
        Canonical bronze-stage name.
    module : ModuleType
        Imported acquisition module for ``stage_name``.
    options : BronzeStageOptions
        Overwrite behaviour and per-stage path overrides.

    Returns
    -------
    object
        The acquisition result returned by the underlying stage function
        (acquired file paths, or a mapping of them).

    Raises
    ------
    ValueError
        If ``stage_name`` is not a recognized bronze stage.
    """

    if stage_name == "basemaps":
        kwargs: dict[str, object] = {"overwrite": options.overwrite}
        if options.raw_basemap_dir is not None:
            kwargs["raw_basemaps"] = options.raw_basemap_dir
        return module.acquire_basemap(**kwargs)

    if stage_name == "residential":
        kwargs = {"overwrite": options.overwrite}
        if options.raw_residential_dir is not None:
            kwargs["raw_residential"] = options.raw_residential_dir
        return module.acquire_residential(**kwargs)

    if stage_name == "gasoline_demand":
        kwargs = {"overwrite": options.overwrite}
        if options.raw_gasoline_demand_dir is not None:
            kwargs["raw_gasoline_demand"] = options.raw_gasoline_demand_dir
        return module.acquire_gasoline_demand(**kwargs)

    if stage_name == "aboriginal_lands":
        kwargs = {
            "overwrite": options.overwrite,
            "keep_archive": options.keep_aboriginal_lands_archive,
        }
        if options.raw_aboriginal_lands_dir is not None:
            kwargs["raw_aboriginal_lands"] = options.raw_aboriginal_lands_dir
        return module.acquire_aboriginal_lands(**kwargs)

    if stage_name == "nrn":
        kwargs = {"overwrite": options.overwrite}
        if options.raw_nrn_dir is not None:
            kwargs["raw_nrn"] = options.raw_nrn_dir
        return module.acquire_nrn(**kwargs)

    if stage_name == "nhn":
        kwargs = {
            "overwrite": options.overwrite,
            "keep_archive": options.keep_nhn_archive,
        }
        if options.raw_nhn_dir is not None:
            kwargs["raw_nhn"] = options.raw_nhn_dir
        return module.acquire_nhn(**kwargs)

    if stage_name == "emissions":
        return module.get_raw_emissions_data(overwrite=options.overwrite)

    if stage_name == "co2_storage":
        return module.acquire_canco2_storage(
            source_gpkg=options.co2_source_gpkg,
            source_repo=options.co2_source_repo,
            overwrite=options.overwrite,
        )

    raise ValueError(f"Unknown bronze stage: {stage_name!r}")


def validate_requested_stages(stages: Sequence[str]) -> tuple[str, ...]:
    """Validate and order a requested subset of bronze stages.

    Parameters
    ----------
    stages : Sequence[str]
        Requested stage names in any order.

    Returns
    -------
    tuple[str, ...]
        The requested stages in canonical ``BRONZE_STAGE_ORDER``.

    Raises
    ------
    ValueError
        If ``stages`` contains an unrecognized stage name.
    """

    unknown = sorted(set(stages) - set(BRONZE_STAGE_ORDER))
    if unknown:
        raise ValueError(
            f"Unknown bronze stage(s): {unknown}. "
            f"Expected a subset of {list(BRONZE_STAGE_ORDER)}."
        )

    return tuple(stage for stage in BRONZE_STAGE_ORDER if stage in stages)


def run_bronze_workflow(
    stages: Sequence[str],
    options: BronzeStageOptions,
    *,
    continue_on_failure: bool = False,
) -> list[WorkflowHistoryRecord]:
    """Run the requested bronze acquisition stages and report their timing.

    Stages are independent, so each is attempted regardless of whether an
    earlier stage failed when ``continue_on_failure`` is true. When it is
    false, the first stage failure is raised immediately.

    Parameters
    ----------
    stages : Sequence[str]
        Ordered subset of ``BRONZE_STAGE_ORDER`` to run.
    options : BronzeStageOptions
        Overwrite behaviour and per-stage path overrides.
    continue_on_failure : bool, default=False
        Whether later stages should still run after an earlier stage fails.

    Returns
    -------
    list[WorkflowHistoryRecord]
        Execution history in the order stages were attempted.

    Raises
    ------
    ImportError
        If one or more requested stage modules cannot be imported.
    Exception
        Propagates the first stage failure when ``continue_on_failure`` is
        false.
    RuntimeError
        If one or more stages failed and ``continue_on_failure`` is true.
    """

    stage_modules = import_stage_modules(
        {name: STAGE_MODULE_NAMES[name] for name in stages}
    )

    print_header("Geospatial-CANOE bronze build")

    history: list[WorkflowHistoryRecord] = []
    failures: list[str] = []

    for stage_name in stages:
        print(f"\n--- {stage_name} ---")
        start = perf_counter()

        try:
            execute_bronze_stage(stage_name, stage_modules[stage_name], options)
        except Exception as exc:
            elapsed = perf_counter() - start
            history.append(
                WorkflowHistoryRecord(
                    stage=stage_name,
                    status="failed",
                    elapsed_seconds=elapsed,
                )
            )
            failures.append(f"{stage_name}: {type(exc).__name__}: {exc}")

            if not continue_on_failure:
                print_stage_timing(history)
                raise

            print(f"Stage {stage_name!r} failed: {exc}")
            continue

        elapsed = perf_counter() - start
        history.append(
            WorkflowHistoryRecord(
                stage=stage_name,
                status="success",
                elapsed_seconds=elapsed,
            )
        )

    print_stage_timing(history)

    if failures:
        raise RuntimeError(
            "One or more bronze stages failed:\n  - " + "\n  - ".join(failures)
        )

    return history
