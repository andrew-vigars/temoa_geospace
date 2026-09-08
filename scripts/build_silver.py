"""Command-line entry point for the Geospatial-CANOE silver build.

This script provides the user-facing command-line interface for validating and
executing the Geospatial-CANOE silver preprocessing workflow.

Reusable workflow registration, validation, execution, state tracking, timing,
and post-run verification are implemented in
``geocanoe.execution.silver``. This script is intentionally limited to
command-line argument handling, build-profile loading, and invocation of the
execution-layer API.

Inputs
------
config/build_profiles/*.toml
    Geospatial preprocessing build profile selected with ``--config``.
Command-line options
    Optional stage subset and validation-only mode.

Outputs
-------
data_files/processed/
    Silver-layer products written by the stage implementations coordinated
    through ``geocanoe.execution.silver``.

Notes
-----
This file should remain a thin runnable adapter. Scientific transformations,
workflow dependency logic, executor definitions, and validation logic belong in
the importable ``geocanoe`` package rather than in ``scripts/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from geocanoe.config import (
    load_geospatial_build_config,
    print_build_config,
)
from geocanoe.execution.silver import (
    DATA_FILES,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    EXTERNAL_INPUT_DEPENDENCIES,
    SILVER_STAGE_DEPENDENCIES,
    SILVER_STAGE_ORDER,
    STAGE_MODULE_NAMES,
    SilverWorkflowDefinition,
    SilverWorkflowState,
    build_stage_executors,
    import_stage_modules,
    print_header,
    print_workflow_summary,
    run_silver_workflow,
    validate_external_inputs,
    validate_workflow_definition,
    verify_silver_build,
)


# =============================================================================
# Project paths
# =============================================================================

CONFIG_DIR = PROJECT_ROOT / "config"


# =============================================================================
# Command-line interface
# =============================================================================


def default_config_path() -> Path:
    """Return the preferred project-level silver build-profile path.

    The current repository layout is checked first for
    ``config/provinces_only.toml``. The newer
    ``config/build_profiles/provinces_only.toml`` location is used as a fallback
    so the command-line behavior remains consistent with the pre-refactor script.

    Returns
    -------
    Path
        Preferred default TOML build-profile path.
    """

    candidates = (
        CONFIG_DIR / "provinces_only.toml",
        CONFIG_DIR / "build_profiles" / "provinces_only.toml",
    )

    return next(
        (path for path in candidates if path.is_file()),
        candidates[0],
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the silver-layer build.

    Returns
    -------
    argparse.Namespace
        Parsed build-profile path, optional ordered stage subset, and
        validation-only selection.
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
            "project provinces_only build profile."
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

    The selected build profile is loaded first. The reusable execution layer then
    imports registered stage modules, constructs stage executors, validates the
    workflow definition, and validates required external inputs.

    Unless ``--validate-only`` is selected, the requested stage sequence is
    executed and the resulting silver-layer products are verified.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If the selected build-profile TOML file does not exist, a registered
        script target is missing, or a required external input is unavailable.
    ImportError
        If one or more registered silver-stage modules cannot be imported.
    ValueError
        If the workflow definition or requested stage sequence is invalid.
    RuntimeError
        If stage dependencies are incomplete or post-run silver verification
        fails.
    Exception
        Propagates exceptions raised by individual silver-stage implementations.
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

