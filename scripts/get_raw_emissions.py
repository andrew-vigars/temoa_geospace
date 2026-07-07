"""
get_raw_emissions_data.py

Batch script for Greenhouse gas emissions from large facilities (CO2 equivalents) (2024).

Downloads and organizes the raw large-facility greenhouse gas emissions data
required by later emissions preprocessing steps. This script does not clean,
spatially process, aggregate, or encode emissions data into the CANOE schema.

Outputs:
    data_files/raw/emissions/co2_large_facilities_2024/
        Greenhouse gas emissions from large facilities - 2024.csv
        AirEmissions_GHG_2024.json
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
RAW_DATA = DATA_FILES / "raw"
RAW_EMISSIONS = RAW_DATA / "emissions" / "co2_large_facilities_2024"


# =============================================================================
# Download settings
# =============================================================================

HEADERS = {
    "User-Agent": (
        "Geospatial-CANOE/0.1.0 "
        "(University of Toronto Academic Research)"
    )
}

REQUEST_TIMEOUT = 120
MAX_RETRIES = 3
DOWNLOAD_DELAY = 2
CHUNK_SIZE = 1024 * 1024


# =============================================================================
# Source definitions
# =============================================================================

EMISSIONS_SOURCE_PAGE = (
    "https://open.canada.ca/data/en/dataset/"
    "756bc907-34bb-4b33-9a87-b3c1a6c3f292"
)

EMISSIONS_RESOURCE = {
    "csv": {
        "name": "Large facility greenhouse gas emissions (CSV)",
        "url": (
            "https://indicators-map.canada.ca/CSVs/en/"
            "Greenhouse%20gas%20emissions%20from%20large%20facilities%20-%202024.csv"
        ),
        "filename": "Greenhouse gas emissions from large facilities - 2024.csv",
    },
    "geojson": {
        "name": "Large facility locations (GeoJSON)",
        "url": "https://indicators-map.canada.ca/historic/english/AirEmissions_GHG_2024.zip",
        "archive_name": "AirEmissions_GHG_2024.zip",
        "expected_file": "AirEmissions_GHG_2024.json",
    },
}


# =============================================================================
# Helpers
# =============================================================================

def download_file(
    url: str,
    destination: Path,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download a URL to disk using a temporary partial file and retry handling.

    Existing files are reused unless ``overwrite`` is true. Downloads are first
    written to a ``.part`` file, then atomically moved to ``destination`` after
    completion so incomplete downloads are not mistaken for valid raw inputs.
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

            response = requests.get(
                url,
                headers=HEADERS,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            temp_path = destination.with_suffix(destination.suffix + ".part")

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)

            temp_path.replace(destination)

            print(f"[Complete download] {destination.name}")
            time.sleep(DOWNLOAD_DELAY)
            return destination

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[Retry] {destination.name}: {exc}")
            time.sleep(DOWNLOAD_DELAY)

    raise RuntimeError(f"Failed to download {url}") from last_error


def extract_geojson_archive(
    zip_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> list[Path]:
    """Extract, validate, and clean up the emissions GeoJSON archive.

    If the expected GeoJSON already exists and ``overwrite`` is false, the
    existing file is reused. Otherwise, the ZIP archive is extracted, nested
    GeoJSON files are moved into ``output_dir``, temporary folders are removed,
    the expected final GeoJSON file is validated, and the source archive is
    deleted after successful extraction.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    expected_file = output_dir / EMISSIONS_RESOURCE["geojson"]["expected_file"]
    existing_json = sorted(output_dir.glob("*.json"))

    if expected_file.exists() and not overwrite:
        print(f"[Skip extract] {expected_file.name} already exists.")
        return [expected_file]

    if existing_json and not overwrite:
        print(f"[Skip extract] {output_dir.name} already contains GeoJSON file(s).")
        return existing_json

    print(f"[Extract] {zip_path.name} → {output_dir.name}")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(output_dir)

    json_files = sorted(output_dir.rglob("*.json"))

    if not json_files:
        raise FileNotFoundError(
            f"No GeoJSON file found after extracting {zip_path.name}"
        )

    for source_path in json_files:
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

    final_json = sorted(output_dir.glob("*.json"))
    expected_file = output_dir / EMISSIONS_RESOURCE["geojson"]["expected_file"]

    if not expected_file.exists():
        raise FileNotFoundError(f"Expected GeoJSON file not found: {expected_file}")

    if len(final_json) != 1:
        raise ValueError(
            f"Expected exactly 1 GeoJSON file in {output_dir}, "
            f"found {len(final_json)}"
        )

    if zip_path.exists():
        zip_path.unlink()
        print(f"[Delete] {zip_path.name}")

    print(f"[Complete] {output_dir.name}: {expected_file.name}")

    return [expected_file]


def validate_outputs(output_dir: Path) -> tuple[Path, Path]:
    """Validate that the expected raw emissions CSV and GeoJSON files exist."""

    csv_path = output_dir / EMISSIONS_RESOURCE["csv"]["filename"]
    json_path = output_dir / EMISSIONS_RESOURCE["geojson"]["expected_file"]

    missing = [path for path in [csv_path, json_path] if not path.exists()]

    if missing:
        raise FileNotFoundError(
            "Missing expected emissions raw data files: "
            + ", ".join(str(path) for path in missing)
        )

    return csv_path, json_path


def get_raw_emissions_data(overwrite: bool = False) -> tuple[Path, Path]:
    """Acquire raw large-facility emissions data and return validated file paths.

    Downloads the emissions CSV and GeoJSON ZIP archive, extracts and flattens
    the GeoJSON file, validates that both expected raw outputs are present, and
    prints a summary of the acquired files.
    """

    RAW_EMISSIONS.mkdir(parents=True, exist_ok=True)

    print("Getting raw large-facility emissions data...")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Raw emissions folder: {RAW_EMISSIONS}")
    print(f"Source page: {EMISSIONS_SOURCE_PAGE}\n")

    csv_path = RAW_EMISSIONS / EMISSIONS_RESOURCE["csv"]["filename"]
    downloaded_csv = download_file(
        url=EMISSIONS_RESOURCE["csv"]["url"],
        destination=csv_path,
        overwrite=overwrite,
    )

    geojson_archive_path = RAW_EMISSIONS / EMISSIONS_RESOURCE["geojson"]["archive_name"]
    downloaded_geojson_archive = download_file(
        url=EMISSIONS_RESOURCE["geojson"]["url"],
        destination=geojson_archive_path,
        overwrite=overwrite,
    )

    geojson_files = extract_geojson_archive(
        zip_path=downloaded_geojson_archive,
        output_dir=RAW_EMISSIONS,
        overwrite=overwrite,
    )

    csv_ready, json_ready = validate_outputs(RAW_EMISSIONS)

    print("\nEmissions acquisition summary")
    print("-----------------------------")

    for file in sorted(RAW_EMISSIONS.iterdir()):
        if file.is_file():
            print(f"- {file.name}")

    print(f"\nCSV ready: {downloaded_csv.name}")
    print(f"GeoJSON ready: {geojson_files[0].name}")

    return csv_ready, json_ready


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the raw emissions acquisition workflow."""
    parser = argparse.ArgumentParser(
        description="Download and organize raw emissions data for Geospatial-CANOE."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload and re-extract files even when outputs already exist.",
    )
    return parser.parse_args()

# =============================================================================
# Main workflow
# =============================================================================

def main() -> None:
    """Run the raw emissions acquisition command-line workflow."""
    args = parse_args()
    get_raw_emissions_data(overwrite=args.overwrite)


if __name__ == "__main__":
    main()
