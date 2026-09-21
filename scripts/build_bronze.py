"""Command-line entry point for the Geospatial-CANOE bronze build.

This script provides the user-facing command-line interface for acquiring the
Geospatial-CANOE bronze-layer raw inputs: Statistics Canada basemap and
dissemination-area boundaries, Census population data, Aboriginal Lands
boundaries, gasoline sales and population-centre boundaries, National Road
Network GeoPackages, National Hydro Network
hydrography, large-facility emissions data, and an acquired CanCO2
unified-storage release.

Reusable stage registration, execution, timing, and reporting are implemented
in ``geocanoe.execution.bronze``. This script is intentionally limited to
command-line argument handling and invocation of the execution-layer API.

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
This file should remain a thin runnable adapter. Acquisition logic belongs in
the importable ``geocanoe`` package rather than in ``scripts/``.

Unlike ``scripts/build_silver.py`` and ``scripts/build_schema.py``, this
script does not take ``--config``: bronze acquisition does not depend on a
study-area build profile.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from geocanoe.execution.bronze import (
    BRONZE_STAGE_ORDER,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    DATA_FILES,
    BronzeStageOptions,
    print_header,
    run_bronze_workflow,
    validate_requested_stages,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the bronze-layer build.

    Returns
    -------
    argparse.Namespace
        Parsed stage subset, overwrite flag, continue-on-failure flag, and
        per-stage path overrides.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Acquire the Geospatial-CANOE bronze-layer raw inputs."
        )
    )

    parser.add_argument(
        "--stages",
        nargs="+",
        choices=BRONZE_STAGE_ORDER,
        help=(
            "Subset of bronze stages to run. Omit to acquire every stage."
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload or recopy outputs even when they already exist.",
    )

    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help=(
            "Attempt every requested stage even after an earlier stage "
            "fails, then raise a single error summarizing all failures."
        ),
    )

    parser.add_argument(
        "--raw-basemap-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for the basemaps stage. Defaults to "
            "data_files/raw/basemaps."
        ),
    )

    parser.add_argument(
        "--raw-residential-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for the residential stage. Defaults to "
            "data_files/raw/residential."
        ),
    )

    parser.add_argument(
        "--raw-gasoline-demand-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for the gasoline_demand stage. Defaults to "
            "data_files/raw/gasoline_demand."
        ),
    )

    parser.add_argument(
        "--raw-aboriginal-lands-dir",
        type=Path,
        default=None,
        help=(
            "Output directory for the aboriginal_lands stage. Defaults to "
            "data_files/raw/aboriginal_lands."
        ),
    )

    parser.add_argument(
        "--keep-aboriginal-lands-archive",
        action="store_true",
        help="Retain the Aboriginal Lands source ZIP after validation.",
    )

    parser.add_argument(
        "--raw-nrn-dir",
        type=Path,
        default=None,
        help="Output directory for the nrn stage. Defaults to data_files/raw/nrn.",
    )

    parser.add_argument(
        "--raw-nhn-dir",
        type=Path,
        default=None,
        help="Output directory for the nhn stage. Defaults to data_files/raw/nhn.",
    )

    parser.add_argument(
        "--keep-nhn-archive",
        action="store_true",
        help="Retain the approximately 15 GB NHN source ZIP after validation.",
    )

    parser.add_argument(
        "--co2-source-gpkg",
        type=Path,
        default=None,
        help="Exact CanCO2 unified-storage GeoPackage for the co2_storage stage.",
    )

    parser.add_argument(
        "--co2-source-repo",
        type=Path,
        default=None,
        help="CanCO2 repository root for the co2_storage stage.",
    )

    return parser.parse_args()


def main() -> None:
    """Acquire the configured Geospatial-CANOE bronze-layer raw inputs.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If an unrecognized stage name is requested.
    ImportError
        If one or more requested stage modules cannot be imported.
    Exception
        Propagates the first stage failure unless ``--continue-on-failure``
        is given.
    RuntimeError
        If one or more stages failed and ``--continue-on-failure`` is given.
    """

    args = parse_args()
    stages = validate_requested_stages(args.stages or list(BRONZE_STAGE_ORDER))

    print_header("Geospatial-CANOE bronze build")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Scripts:      {SCRIPTS_DIR}")
    print(f"Data files:   {DATA_FILES}")
    print(f"Stages:       {', '.join(stages)}")

    options = BronzeStageOptions(
        overwrite=args.overwrite,
        raw_basemap_dir=args.raw_basemap_dir,
        raw_residential_dir=args.raw_residential_dir,
        raw_gasoline_demand_dir=args.raw_gasoline_demand_dir,
        raw_aboriginal_lands_dir=args.raw_aboriginal_lands_dir,
        keep_aboriginal_lands_archive=args.keep_aboriginal_lands_archive,
        raw_nrn_dir=args.raw_nrn_dir,
        raw_nhn_dir=args.raw_nhn_dir,
        keep_nhn_archive=args.keep_nhn_archive,
        co2_source_gpkg=args.co2_source_gpkg,
        co2_source_repo=args.co2_source_repo,
    )

    run_bronze_workflow(
        stages,
        options,
        continue_on_failure=args.continue_on_failure,
    )


if __name__ == "__main__":
    main()
