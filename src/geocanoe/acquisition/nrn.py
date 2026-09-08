"""
get_raw_nrn.py

Batch script for National Road Network Files (2024)

Download, extract, flatten, and validate the raw National Road Network (NRN)
GeoPackage files used by the geospatial preprocessing pipeline.

Source:
    https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f

Inputs:
    Public NRN ZIP archives from:
        https://geo.statcan.gc.ca/nrn_rrn/{province}/nrn_rrn_{province}_GPKG.zip

Outputs:
    data_files/raw/nrn/{PROVINCE}/
        NRN_{PROVINCE}_*_GPKG_en.gpkg
        RRN_{PROVINCE}_*_GPKG_fr.gpkg

Notes:
    - Downloads are sequential and polite by default.
    - Archives are deleted after successful extraction and validation.
    - Nested archive folders are flattened into each province/territory folder.
"""

from __future__ import annotations

import argparse
import shutil
import time
import zipfile
from pathlib import Path
from typing import Dict, Iterable

import requests

from geocanoe.paths import find_project_root


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = find_project_root()
DATA_FILES = PROJECT_ROOT / "data_files"
RAW_NRN = DATA_FILES / "raw" / "nrn"


# =============================================================================
# Download settings
# =============================================================================

HEADERS = {
    "User-Agent": (
        "Geospatial-CANOE/0.1.0 "
        "(University of Toronto Academic Research)" # Change to your name or organization if desired.
    )
}

REQUEST_TIMEOUT = 120          # seconds
MAX_RETRIES = 3
DOWNLOAD_DELAY = 2             # seconds between successful downloads
RETRY_DELAY = 5                # seconds between failed attempts
CHUNK_SIZE = 1024 * 1024       # 1 MB streaming chunks


# =============================================================================
# NRN source definition
# =============================================================================

NRN_SOURCE_PAGE = (
    "https://open.canada.ca/data/en/dataset/"
    "3d282116-e556-400c-9306-ca1a3cada77f"
)

NRN_BASE_URL = "https://geo.statcan.gc.ca/nrn_rrn"

NRN_PROVINCE_CODES = {
    "AB": "ab",
    "BC": "bc",
    "MB": "mb",
    "NB": "nb",
    "NL": "nl",
    "NS": "ns",
    "NT": "nt",
    "NU": "nu",
    "ON": "on",
    "PE": "pe",
    "QC": "qc",
    "SK": "sk",
    "YT": "yt",
}


def build_nrn_resources(raw_nrn: Path) -> Dict[str, dict]:
    """Build NRN download metadata for each province and territory.

    A metadata dictionary is created from ``NRN_PROVINCE_CODES``. Each provincial
    or territorial code is mapped to its source archive URL, destination directory,
    and expected archive filename.

    Parameters
    ----------
    raw_nrn : Path
        Root directory where province- and territory-specific NRN files will be
        downloaded and extracted.

    Returns
    -------
    dict[str, dict]
        Download metadata keyed by province or territory code. Each value contains
        the source URL, output directory, and archive filename.
    """

    return {
        province: {
            "url": f"{NRN_BASE_URL}/{code}/nrn_rrn_{code}_GPKG.zip",
            "output_dir": raw_nrn / province,
            "archive_name": f"nrn_rrn_{code}_GPKG.zip",
        }
        for province, code in NRN_PROVINCE_CODES.items()
    }


# =============================================================================
# Helpers
# =============================================================================


def ensure_directories(raw_nrn: Path, provinces: Iterable[str]) -> None:
    """Create the raw NRN directory structure for selected jurisdictions.

    The raw NRN root directory is created when needed, followed by one child
    directory for each supplied province or territory code. Existing directories
    are preserved without modification.

    Parameters
    ----------
    raw_nrn : Path
        Root directory for raw National Road Network files.
    provinces : Iterable[str]
        Province and territory codes for which subdirectories should be created.

    Returns
    -------
    None
    """

    raw_nrn.mkdir(parents=True, exist_ok=True)

    for province in provinces:
        (raw_nrn / province).mkdir(parents=True, exist_ok=True)


def download_file(
    url: str,
    destination: Path,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download an NRN archive with reuse, overwrite, and retry handling.

    The destination directory is created when needed. Existing files are reused
    unless ``overwrite`` is true, in which case the destination is removed before
    downloading. The response body is streamed directly to disk in configured chunk
    sizes to avoid loading the complete archive into memory.

    If an attempt fails, any partial destination file is deleted before the next
    attempt. Failed attempts are separated by the configured retry delay. A
    ``RuntimeError`` chained from the final exception is raised when all attempts
    fail.

    Parameters
    ----------
    url : str
        URL of the NRN archive to download.
    destination : Path
        Local path where the downloaded archive will be written.
    overwrite : bool, default=False
        Whether to replace an existing destination file.
    max_retries : int, default=MAX_RETRIES
        Maximum number of download attempts.

    Returns
    -------
    Path
        Path to the existing or successfully downloaded archive.

    Raises
    ------
    RuntimeError
        If the archive cannot be downloaded after all configured attempts.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not overwrite:
        print(f"[Skip download] {destination.name} already exists.")
        return destination

    if destination.exists() and overwrite:
        destination.unlink()

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Download] {destination.name} (attempt {attempt}/{max_retries})")

            with requests.get(
                url,
                headers=HEADERS,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response.raise_for_status()

                with open(destination, "wb") as file:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if chunk:
                            file.write(chunk)

            print(f"[Complete download] {destination.name}")
            time.sleep(DOWNLOAD_DELAY)
            return destination

        except (requests.RequestException, OSError) as exc:
            last_error = exc

            try:
                destination.unlink(missing_ok=True)
            except OSError as cleanup_error:
                print(
                    f"[Cleanup warning] Could not remove partial file "
                    f"{destination}: {cleanup_error}"
                )

            if attempt < max_retries:
                print(f"[Retry] {destination.name}: {exc}")
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"Failed to download {url}") from last_error


def extract_flatten_and_clean(
    zip_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> list[Path]:
    """Extract, flatten, validate, and clean up one provincial NRN archive.

    The ZIP archive is extracted into ``output_dir`` and any GeoPackage files stored
    in nested archive directories are moved to the province or territory directory
    root. Temporary extraction directories are removed after flattening, and the
    expected English and French NRN GeoPackages are validated before the source
    archive is deleted.

    When GeoPackage files already exist and ``overwrite`` is false, the existing
    outputs are validated and reused without extracting the archive. When
    ``overwrite`` is true, existing GeoPackages are removed before extraction and
    conflicting destination files may be replaced during flattening.

    Parameters
    ----------
    zip_path : Path
        Path to the downloaded provincial or territorial NRN ZIP archive.
    output_dir : Path
        Province- or territory-specific directory where the flattened GeoPackages
        will be stored.
    overwrite : bool, default=False
        Whether to replace existing GeoPackage outputs.

    Returns
    -------
    list[Path]
        Validated GeoPackage paths present in ``output_dir`` after extraction or
        reuse.

    Raises
    ------
    FileNotFoundError
        If archive extraction produces no GeoPackage files or required provincial
        outputs are missing during validation.
    ValueError
        If the extracted files do not satisfy the expected provincial NRN output
        structure.
    zipfile.BadZipFile
        If ``zip_path`` is not a valid ZIP archive.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    province = output_dir.name

    existing_gpkgs = sorted(output_dir.glob("*.gpkg"))

    if existing_gpkgs and not overwrite:
        print(f"[Skip extract] {province} already has GeoPackage file(s).")
        validate_province_outputs(output_dir)
        return existing_gpkgs

    if existing_gpkgs and overwrite:
        for path in existing_gpkgs:
            path.unlink()

    print(f"[Extract] {zip_path.name} → {province}")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(output_dir)

    all_gpkgs = sorted(output_dir.rglob("*.gpkg"))

    if not all_gpkgs:
        raise FileNotFoundError(
            f"No GeoPackage files found after extracting {zip_path.name}"
        )

    for source_path in all_gpkgs:
        destination_path = output_dir / source_path.name

        if source_path.parent == output_dir:
            continue

        if destination_path.exists():
            if overwrite:
                destination_path.unlink()
            else:
                print(f"[Skip move] {destination_path.name} already exists.")
                continue

        shutil.move(str(source_path), str(destination_path))
        print(f"[Move] {source_path.name} → {destination_path.name}")

    for path in sorted(output_dir.iterdir()):
        if path.is_dir():
            shutil.rmtree(path)

    final_gpkgs = validate_province_outputs(output_dir)

    if zip_path.exists():
        zip_path.unlink()
        print(f"[Delete] {zip_path.name}")

    print(f"[Complete] {province}: {len(final_gpkgs)} GeoPackage file(s)")

    return final_gpkgs


def validate_province_outputs(output_dir: Path) -> list[Path]:
    """Validate the provincial English and French NRN GeoPackage outputs.

    The province or territory code is inferred from ``output_dir``. The directory
    must contain exactly one English National Road Network GeoPackage matching the
    configured NRN naming convention and exactly one French Réseau routier national
    GeoPackage matching the corresponding RRN convention.

    Parameters
    ----------
    output_dir : Path
        Province- or territory-specific directory containing extracted NRN
        GeoPackages.

    Returns
    -------
    list[Path]
        Sorted paths to all GeoPackage files present in ``output_dir``.

    Raises
    ------
    ValueError
        If the directory does not contain exactly one English NRN GeoPackage or
        exactly one French RRN GeoPackage.
    """

    province = output_dir.name

    final_gpkgs = sorted(output_dir.glob("*.gpkg"))
    english_gpkgs = sorted(output_dir.glob(f"NRN_{province}_*_GPKG_en.gpkg"))
    french_gpkgs = sorted(output_dir.glob(f"RRN_{province}_*_GPKG_fr.gpkg"))

    if len(english_gpkgs) != 1:
        raise ValueError(
            f"{province}: expected 1 English NRN GPKG, found {len(english_gpkgs)}"
        )

    if len(french_gpkgs) != 1:
        raise ValueError(
            f"{province}: expected 1 French RRN GPKG, found {len(french_gpkgs)}"
        )

    return final_gpkgs


def acquire_nrn(raw_nrn: Path = RAW_NRN, overwrite: bool = False) -> dict[str, list[Path]]:
    """Acquire and validate raw NRN GeoPackages for all jurisdictions.

    Download metadata are built for each configured province and territory, and the
    required raw-data directory structure is created. Each jurisdictional ZIP
    archive is then downloaded, extracted, flattened, and validated before its
    GeoPackage paths are stored in the result mapping.

    Existing archives and extracted outputs are reused by default. When
    ``overwrite`` is true, existing downloads and GeoPackages are replaced according
    to the underlying acquisition helpers.

    Parameters
    ----------
    raw_nrn : Path, default=RAW_NRN
        Root directory containing province- and territory-specific raw NRN folders.
    overwrite : bool, default=False
        Whether to replace existing downloaded archives and extracted GeoPackages.

    Returns
    -------
    dict[str, list[Path]]
        Validated GeoPackage paths keyed by province or territory code.

    Raises
    ------
    RuntimeError
        If a jurisdictional archive cannot be downloaded after all retry attempts.
    FileNotFoundError
        If extraction produces no GeoPackage files or required outputs are missing.
    ValueError
        If a jurisdiction does not contain exactly one English NRN and one French
        RRN GeoPackage.
    zipfile.BadZipFile
        If a downloaded jurisdictional archive is not a valid ZIP file.
    """

    resources = build_nrn_resources(raw_nrn)
    ensure_directories(raw_nrn, resources.keys())

    print("Acquiring National Road Network GeoPackages...\n")
    print(f"Source page: {NRN_SOURCE_PAGE}")
    print(f"Raw NRN folder: {raw_nrn}\n")

    acquired_files: dict[str, list[Path]] = {}

    for province, resource in resources.items():
        output_dir = resource["output_dir"]
        archive_path = output_dir / resource["archive_name"]

        downloaded_archive = download_file(
            url=resource["url"],
            destination=archive_path,
            overwrite=overwrite,
        )

        acquired_files[province] = extract_flatten_and_clean(
            zip_path=downloaded_archive,
            output_dir=output_dir,
            overwrite=overwrite,
        )

    return acquired_files


def print_summary(acquired_files: dict[str, list[Path]]) -> None:
    """Print a jurisdiction-level summary of acquired NRN GeoPackages.

    Each province or territory is listed with the number of validated GeoPackage
    files acquired for that jurisdiction, followed by the individual filenames. The
    total number of processed provincial and territorial archives is reported at
    the end.

    Parameters
    ----------
    acquired_files : dict[str, list[Path]]
        Validated GeoPackage paths keyed by province or territory code.

    Returns
    -------
    None
    """

    print("\nNRN acquisition summary")
    print("-----------------------")

    for province, files in acquired_files.items():
        print(f"{province}: {len(files)} GeoPackage file(s)")
        for file in files:
            print(f"  - {file.name}")

    print(f"\nProcessed {len(acquired_files)} provincial/territorial archive(s).")


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for raw NRN acquisition.

    The command-line interface allows existing NRN downloads and extracted
    GeoPackages to be replaced and permits the default raw NRN directory to be
    overridden.

    Returns
    -------
    argparse.Namespace
        Parsed arguments containing the overwrite flag and raw NRN output
        directory.
    """

    parser = argparse.ArgumentParser(
        description="Download, extract, flatten, and validate raw NRN GeoPackages."
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload and re-extract NRN files even when outputs already exist.",
    )

    parser.add_argument(
        "--raw-nrn-dir",
        type=Path,
        default=RAW_NRN,
        help="Output directory for raw NRN files. Defaults to data_files/raw/nrn.",
    )

    return parser.parse_args()


def main() -> None:
    """Run the command-line workflow for raw NRN acquisition.

    Command-line arguments are parsed to resolve the raw NRN output directory and
    overwrite behaviour. The configured provincial and territorial National Road
    Network archives are then downloaded, extracted, flattened, validated, and
    summarized to the console.

    Returns
    -------
    None
    """

    args = parse_args()

    acquired_files = acquire_nrn(
        raw_nrn=args.raw_nrn_dir,
        overwrite=args.overwrite,
    )

    print_summary(acquired_files)


if __name__ == "__main__":
    main()
