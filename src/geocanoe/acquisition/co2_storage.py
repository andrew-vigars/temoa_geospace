"""Acquire the unified CanCO2 storage release from a local sibling checkout.

The CanCO2 storage repository currently produces timestamped Silver artifacts.
From GeoCANOE's perspective those files are external raw inputs.  This module
discovers or accepts an exact unified-storage GeoPackage and copies the complete
release family into ``data_files/raw/canco2_storage/``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
RAW_CO2_STORAGE = PROJECT_ROOT / "data_files" / "raw" / "canco2_storage"

STORAGE_REPO_ENV = "CANCO2_STORAGE_REPO"
STORAGE_GPKG_ENV = "CANCO2_STORAGE_GPKG"
UNIFIED_STORAGE_RELATIVE_DIR = Path("data") / "processed" / "unified_storage"
UNIFIED_STORAGE_PATTERN = "*_CanadaGeologicalStorageUnified*_v*.gpkg"
SELECTED_RELEASE_FILENAME = "selected_release.txt"


def default_storage_repository(project_root: Path = PROJECT_ROOT) -> Path:
    """Return the expected sibling CanCO2 repository path.

    The default layout places ``canco2-storage`` directly under the shared
    ``repos`` directory while GeoCANOE is nested under
    ``repos/temoa-upstream/temoa_geospace``.
    """

    resolved_root = project_root.resolve()
    try:
        repos_root = resolved_root.parents[1]
    except IndexError as exc:
        raise ValueError(
            f"Cannot derive a shared repositories directory from {resolved_root}."
        ) from exc
    return repos_root / "canco2-storage"


def find_latest_unified_storage_gpkg(directory: Path) -> Path:
    """Return the lexicographically newest timestamped unified GeoPackage."""

    candidates = sorted(directory.glob(UNIFIED_STORAGE_PATTERN))
    if not candidates:
        raise FileNotFoundError(
            "No timestamped CanCO2 unified-storage GeoPackage found in "
            f"{directory}. Expected pattern: {UNIFIED_STORAGE_PATTERN}"
        )
    return candidates[-1]


def resolve_source_gpkg(
    *,
    project_root: Path = PROJECT_ROOT,
    source_gpkg: Path | None = None,
    source_repo: Path | None = None,
) -> Path:
    """Resolve the external CanCO2 GeoPackage using explicit and default paths.

    Resolution precedence is: explicit ``source_gpkg`` argument,
    ``CANCO2_STORAGE_GPKG``, explicit ``source_repo`` argument,
    ``CANCO2_STORAGE_REPO``, then the documented sibling-repository layout.
    """

    configured_gpkg = source_gpkg
    if configured_gpkg is None:
        environment_gpkg = os.environ.get(STORAGE_GPKG_ENV)
        configured_gpkg = Path(environment_gpkg) if environment_gpkg else None

    if configured_gpkg is not None:
        resolved_gpkg = configured_gpkg.expanduser().resolve()
        if not resolved_gpkg.is_file():
            raise FileNotFoundError(
                f"Configured CanCO2 storage GeoPackage not found: {resolved_gpkg}"
            )
        if resolved_gpkg.suffix.lower() != ".gpkg":
            raise ValueError(
                f"Configured CanCO2 storage input must be a .gpkg file: {resolved_gpkg}"
            )
        return resolved_gpkg

    configured_repo = source_repo
    if configured_repo is None:
        environment_repo = os.environ.get(STORAGE_REPO_ENV)
        configured_repo = (
            Path(environment_repo)
            if environment_repo
            else default_storage_repository(project_root)
        )

    resolved_repo = configured_repo.expanduser().resolve()
    unified_storage_dir = resolved_repo / UNIFIED_STORAGE_RELATIVE_DIR
    if not unified_storage_dir.is_dir():
        raise FileNotFoundError(
            "CanCO2 unified-storage directory not found: "
            f"{unified_storage_dir}. See README.md for the required repository layout."
        )

    return find_latest_unified_storage_gpkg(unified_storage_dir)


def release_family(source_gpkg: Path) -> list[Path]:
    """Return the GeoPackage and companion files belonging to one release."""

    files = sorted(
        path
        for path in source_gpkg.parent.glob(f"{source_gpkg.stem}*")
        if path.is_file()
        and not path.name.endswith((".gpkg-wal", ".gpkg-shm", ".part"))
    )
    if source_gpkg not in files:
        files.append(source_gpkg)
        files.sort()
    return files


def acquire_canco2_storage(
    *,
    project_root: Path = PROJECT_ROOT,
    source_gpkg: Path | None = None,
    source_repo: Path | None = None,
    destination_dir: Path = RAW_CO2_STORAGE,
    overwrite: bool = False,
) -> Path:
    """Copy one CanCO2 unified-storage release into GeoCANOE raw inputs.

    Files are copied through temporary ``.part`` paths and then replaced so an
    interrupted copy is not mistaken for a complete raw input.
    """

    resolved_source = resolve_source_gpkg(
        project_root=project_root,
        source_gpkg=source_gpkg,
        source_repo=source_repo,
    )
    source_files = release_family(resolved_source)
    destination_dir = destination_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    print(f"Selected CanCO2 release: {resolved_source}")
    print(f"Destination: {destination_dir}")

    for source_path in source_files:
        destination_path = destination_dir / source_path.name
        if destination_path.exists() and not overwrite:
            print(f"[Skip copy] {destination_path.name} already exists.")
            continue

        temporary_path = destination_path.with_suffix(
            destination_path.suffix + ".part"
        )
        temporary_path.unlink(missing_ok=True)
        try:
            shutil.copy2(source_path, temporary_path)
            temporary_path.replace(destination_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise
        print(f"[Copied] {destination_path.name}")

    acquired_gpkg = destination_dir / resolved_source.name
    if not acquired_gpkg.is_file():
        raise RuntimeError(
            f"CanCO2 acquisition did not produce the expected file: {acquired_gpkg}"
        )
    selection_path = destination_dir / SELECTED_RELEASE_FILENAME
    selection_path.write_text(acquired_gpkg.name + "\n", encoding="utf-8")
    print(f"[Selected release] {selection_path.name} -> {acquired_gpkg.name}")
    return acquired_gpkg


def find_latest_raw_storage_gpkg(
    raw_dir: Path = RAW_CO2_STORAGE,
) -> Path:
    """Return the selected, or otherwise newest, acquired CanCO2 GeoPackage."""

    raw_dir = raw_dir.expanduser().resolve()
    selection_path = raw_dir / SELECTED_RELEASE_FILENAME
    if selection_path.is_file():
        selected_name = selection_path.read_text(encoding="utf-8").strip()
        selected_path = (raw_dir / selected_name).resolve()
        if selected_path.parent != raw_dir:
            raise ValueError(
                f"Invalid CanCO2 selected-release path in {selection_path}: "
                f"{selected_name!r}"
            )
        if not selected_path.is_file():
            raise FileNotFoundError(
                f"Selected CanCO2 raw release does not exist: {selected_path}"
            )
        return selected_path
    return find_latest_unified_storage_gpkg(raw_dir)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for local CanCO2 acquisition."""

    parser = argparse.ArgumentParser(
        description="Copy a CanCO2 unified-storage release into GeoCANOE raw data."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--source-gpkg",
        type=Path,
        help="Exact unified-storage GeoPackage to acquire.",
    )
    source_group.add_argument(
        "--source-repo",
        type=Path,
        help="CanCO2 repository root containing data/processed/unified_storage/.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace already acquired files from the selected release.",
    )
    return parser.parse_args()


def main() -> None:
    """Run local CanCO2 storage acquisition."""

    args = parse_args()
    output = acquire_canco2_storage(
        source_gpkg=args.source_gpkg,
        source_repo=args.source_repo,
        overwrite=args.overwrite,
    )
    print(f"Acquired CanCO2 storage GeoPackage: {output}")


if __name__ == "__main__":
    main()
