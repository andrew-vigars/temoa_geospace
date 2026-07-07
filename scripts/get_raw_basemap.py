"""
get_raw_basemap.py

Batch script for Cartographic Canadian basemap (2021 Census).

Download, extract, flatten, and validate the raw Statistics Canada provincial
and territorial boundary shapefile used by the geospatial basemap workflow.

Source:
    https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/index-eng.cfm

Inputs:
    Public Statistics Canada ZIP archive from:
        https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/files-fichiers/lpr_000b21a_e.zip

Outputs:
    data_files/raw/basemaps/
        lpr_000b21a_e.dbf
        lpr_000b21a_e.prj
        lpr_000b21a_e.shp
        lpr_000b21a_e.shx
        lpr_000b21a_e.xml
Notes:
    - Downloads are sequential and polite by default.
    - The archive is deleted after successful extraction and validation.
    - Nested archive folders are flattened into the raw basemap folder.
"""

from __future__ import annotations

import argparse
import shutil
import time
import zipfile
from pathlib import Path

import requests


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = PROJECT_ROOT / "data_files"
RAW_BASEMAPS = DATA_FILES / "raw" / "basemaps"


# =============================================================================
# Download settings
# =============================================================================

HEADERS = {
    "User-Agent": (
        "Geospatial-CANOE/0.1.0 "
        "(University of Toronto Academic Research)"
    )
}

REQUEST_TIMEOUT = 120          # seconds
MAX_RETRIES = 3
DOWNLOAD_DELAY = 2             # seconds between successful downloads
RETRY_DELAY = 5                # seconds between failed attempts
CHUNK_SIZE = 1024 * 1024       # 1 MB streaming chunks


# =============================================================================
# Basemap source definition
# =============================================================================

BASEMAP_SOURCE_PAGE = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
    "sip-pis/boundary-limites/index-eng.cfm"
)

BASEMAP_URL = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
    "sip-pis/boundary-limites/files-fichiers/lpr_000b21a_e.zip"
)

BASEMAP_RESOURCE = {
    "name": "Statistics Canada 2021 provincial and territorial boundary file",
    "url": BASEMAP_URL,
    "archive_name": "lpr_000b21a_e.zip",
    "expected_shapefile": "lpr_000b21a_e.shp",
}


# =============================================================================
# Helpers
# =============================================================================


def ensure_directory(raw_basemaps: Path) -> None:
    """Ensure the raw basemap output directory exists."""

    raw_basemaps.mkdir(parents=True, exist_ok=True)


def download_file(
    url: str,
    destination: Path,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download a file to ``destination`` unless an existing file is reused.

    Existing files are skipped by default. When ``overwrite`` is true, the
    existing destination is deleted before downloading. Failed partial downloads
    are removed before retrying, and a ``RuntimeError`` is raised if all retry
    attempts fail.
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

        except Exception as exc:  # noqa: BLE001 - show original error after retries
            last_error = exc

            if destination.exists():
                destination.unlink()

            if attempt < max_retries:
                print(f"[Retry] {destination.name}: {exc}")
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"Failed to download {url}") from last_error


def extract_basemap_archive(
    zip_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> list[Path]:
    """Extract, flatten, validate, and clean up the raw basemap archive.

    If a shapefile already exists in ``output_dir`` and ``overwrite`` is false,
    the existing basemap outputs are validated and reused. Otherwise, the ZIP
    archive is extracted, nested files are moved into ``output_dir``, temporary
    folders are removed, required shapefile sidecars are validated, and the
    source archive is deleted after successful extraction.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    existing_shapefiles = sorted(output_dir.glob("*.shp"))

    if existing_shapefiles and not overwrite:
        print(f"[Skip extract] {output_dir.name} already contains shapefile(s).")
        return validate_basemap_outputs(output_dir)

    if existing_shapefiles and overwrite:
        for path in output_dir.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

    print(f"[Extract] {zip_path.name} → {output_dir.name}")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(output_dir)

    archive_files = sorted(path for path in output_dir.rglob("*") if path.is_file())

    if not archive_files:
        raise FileNotFoundError(f"No files found after extracting {zip_path.name}")

    for source_path in archive_files:
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

    final_shapefiles = validate_basemap_outputs(output_dir)

    if zip_path.exists():
        zip_path.unlink()
        print(f"[Delete] {zip_path.name}")

    print(f"[Complete] {output_dir.name}: {final_shapefiles[0].name}")

    return final_shapefiles


def validate_basemap_outputs(output_dir: Path) -> list[Path]:
    """Validate that the raw basemap folder contains one complete shapefile."""

    final_shapefiles = sorted(output_dir.glob("*.shp"))

    if len(final_shapefiles) != 1:
        raise ValueError(
            f"Expected exactly 1 shapefile in {output_dir}, "
            f"found {len(final_shapefiles)}"
        )

    expected_path = output_dir / BASEMAP_RESOURCE["expected_shapefile"]

    if not expected_path.exists():
        raise FileNotFoundError(
            f"Expected shapefile not found: {expected_path.name}. "
            f"Found: {[path.name for path in final_shapefiles]}"
        )

    required_sidecars = [".shx", ".dbf", ".prj"]
    missing_sidecars = [
        suffix for suffix in required_sidecars
        if not expected_path.with_suffix(suffix).exists()
    ]

    if missing_sidecars:
        raise FileNotFoundError(
            f"Missing required shapefile sidecar(s) for {expected_path.name}: "
            f"{missing_sidecars}"
        )

    return final_shapefiles


def acquire_basemap(
    raw_basemaps: Path = RAW_BASEMAPS,
    overwrite: bool = False,
) -> list[Path]:
    """Acquire the raw Statistics Canada basemap and return validated shapefiles.

    The workflow ensures the output directory exists, downloads the source ZIP
    archive when needed, extracts and flattens the archive contents, validates
    the final shapefile outputs, and returns the validated shapefile path list.
    """

    ensure_directory(raw_basemaps)

    print("Acquiring Statistics Canada basemap shapefile...\n")
    print(f"Source page: {BASEMAP_SOURCE_PAGE}")
    print(f"Raw basemap folder: {raw_basemaps}\n")

    archive_path = raw_basemaps / BASEMAP_RESOURCE["archive_name"]

    downloaded_archive = download_file(
        url=BASEMAP_RESOURCE["url"],
        destination=archive_path,
        overwrite=overwrite,
    )

    basemap_files = extract_basemap_archive(
        zip_path=downloaded_archive,
        output_dir=raw_basemaps,
        overwrite=overwrite,
    )

    return basemap_files


def print_summary(raw_basemaps: Path, basemap_files: list[Path]) -> None:
    """Print the final raw basemap files and validated shapefile path."""

    print("\nBasemap acquisition summary")
    print("---------------------------")

    for file in sorted(raw_basemaps.iterdir()):
        if file.is_file():
            print(f"- {file.name}")

    print(f"\nShapefile ready: {basemap_files[0]}")


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the raw basemap acquisition script."""

    parser = argparse.ArgumentParser(
        description="Download, extract, flatten, and validate raw basemap shapefile."
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload and re-extract basemap files even when outputs already exist.",
    )

    parser.add_argument(
        "--raw-basemap-dir",
        type=Path,
        default=RAW_BASEMAPS,
        help="Output directory for raw basemap files. Defaults to data_files/raw/basemaps.",
    )

    return parser.parse_args()


def main() -> None:
    """Run the raw basemap acquisition command-line workflow."""
    args = parse_args()

    basemap_files = acquire_basemap(
        raw_basemaps=args.raw_basemap_dir,
        overwrite=args.overwrite,
    )

    print_summary(args.raw_basemap_dir, basemap_files)


if __name__ == "__main__":
    main()
