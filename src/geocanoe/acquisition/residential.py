"""Acquire the 2021 Census inputs for residential pipeline impedance.

The reference residential layer is a Silver product: Statistics Canada's
national dissemination-area cartographic boundary file joined to population
density from table 98-10-0015-01.  This module acquires the two unmodified
Bronze inputs.  It intentionally does not filter Ontario, join records, or
derive penalty classes.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TypedDict

import pyogrio
import requests
from tqdm.auto import tqdm

from geocanoe import __version__
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
RAW_RESIDENTIAL = PROJECT_ROOT / "data_files" / "raw" / "residential"

BOUNDARY_CATALOGUE_URL = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
    "sip-pis/boundary-limites/index-eng.cfm"
)
BOUNDARY_ARCHIVE_NAME = "lda_000b21a_e.zip"
BOUNDARY_ARCHIVE_URL = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
    "sip-pis/boundary-limites/files-fichiers/lda_000b21a_e.zip"
)
BOUNDARY_STEM = "lda_000b21a_e"
BOUNDARY_SHAPEFILE_NAME = f"{BOUNDARY_STEM}.shp"

POPULATION_TABLE_URL = (
    "https://www150.statcan.gc.ca/n1/tbl/csv/98100015-eng.zip"
)
POPULATION_ARCHIVE_NAME = "98100015-eng.zip"
POPULATION_TABLE_NAME = "98100015.csv"

REQUIRED_BOUNDARY_SIDECARS = {".dbf", ".prj", ".shx"}
REQUIRED_BOUNDARY_FIELDS = {"DAUID", "DGUID", "LANDAREA", "PRUID"}
EXPECTED_BOUNDARY_CRS = "EPSG:3347"
EXPECTED_BOUNDARY_FEATURES = 57_932
REQUIRED_TABLE_FIELDS = {"REF_DATE", "GEO", "DGUID", "Coordinate"}
POPULATION_DENSITY_FIELD = (
    "Population and dwelling counts (5): Population density per square "
    "kilometre, 2021 [5]"
)

HEADERS = {
    "User-Agent": (
        f"Geospatial-CANOE/{__version__} "
        "(University of Toronto Academic Research)"
    )
}
REQUEST_TIMEOUT = 120
MAX_RETRIES = 3
RETRY_DELAY = 5
CHUNK_SIZE = 1024 * 1024


class ResidentialAcquisitionResult(TypedDict):
    """Validated paths produced by the residential Bronze acquisition."""

    boundaries: Path
    population_table: Path


def _content_length(response: requests.Response, offset: int) -> int | None:
    """Return the expected final byte count for a streamed response."""

    header = response.headers.get("Content-Length")
    if header is None:
        return None
    try:
        length = int(header)
    except ValueError:
        return None
    return offset + length if response.status_code == 206 else length


def download_file(
    url: str,
    destination: Path,
    *,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download a Census archive atomically with resume and ``tqdm`` progress."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    if destination.exists() and not overwrite:
        print(f"[Skip download] {destination.name} already exists.")
        return destination
    if overwrite:
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        request_headers = dict(HEADERS)
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        try:
            print(
                f"[Download] {destination.name} "
                f"(attempt {attempt}/{max_retries}, offset {offset:,})"
            )
            response = requests.get(
                url,
                headers=request_headers,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            )
            if offset and response.status_code != 206:
                response.close()
                partial.unlink(missing_ok=True)
                offset = 0
                response = requests.get(
                    url,
                    headers=HEADERS,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                )

            with response:
                response.raise_for_status()
                expected_size = _content_length(response, offset)
                mode = "ab" if offset and response.status_code == 206 else "wb"
                with (
                    partial.open(mode) as file,
                    tqdm(
                        total=expected_size,
                        initial=offset,
                        desc=destination.name,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        dynamic_ncols=True,
                    ) as progress,
                ):
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if chunk:
                            file.write(chunk)
                            progress.update(len(chunk))

            actual_size = partial.stat().st_size
            if expected_size is not None and actual_size != expected_size:
                raise OSError(
                    f"Incomplete download for {destination.name}: expected "
                    f"{expected_size:,} bytes, found {actual_size:,}."
                )
            partial.replace(destination)
            print(f"[Complete download] {destination.name} ({actual_size:,} bytes)")
            return destination
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                print(f"[Retry] {destination.name}: {exc}")
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"Failed to download {url}") from last_error


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Return file members after rejecting absolute and parent-relative paths."""

    members = [member for member in archive.infolist() if not member.is_dir()]
    for member in members:
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe ZIP member path: {member.filename!r}")
    return members


def validate_boundary_archive(archive_path: Path) -> Path:
    """Validate that the Census ZIP contains one complete DA shapefile."""

    if not archive_path.is_file():
        raise FileNotFoundError(f"DA boundary archive not found: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        names = {Path(member.filename).name for member in _safe_members(archive)}
        required = {BOUNDARY_SHAPEFILE_NAME} | {
            f"{BOUNDARY_STEM}{suffix}" for suffix in REQUIRED_BOUNDARY_SIDECARS
        }
        missing = sorted(required - names)
        if missing:
            raise FileNotFoundError(
                f"DA boundary archive is missing required members: {missing}"
            )
    return archive_path


def validate_population_archive(archive_path: Path) -> Path:
    """Validate that the table archive contains the expected full-table CSV."""

    if not archive_path.is_file():
        raise FileNotFoundError(f"Population table archive not found: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        names = {Path(member.filename).name for member in _safe_members(archive)}
        if POPULATION_TABLE_NAME not in names:
            raise FileNotFoundError(
                f"Population archive does not contain {POPULATION_TABLE_NAME!r}."
            )
    return archive_path


def _extract_flattened(archive_path: Path, output_dir: Path) -> list[Path]:
    """Extract all archive files to the output root using source basenames."""

    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix="geocanoe-census-", dir=output_dir.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            archive.extractall(temporary_dir, members)
        for source in sorted(temporary_dir.rglob("*")):
            if not source.is_file():
                continue
            destination = output_dir / source.name
            if destination.exists():
                destination.unlink()
            shutil.move(str(source), destination)
            extracted.append(destination)
    return extracted


def validate_boundary_shapefile(shapefile_path: Path) -> Path:
    """Validate identity, sidecars, CRS, schema, geometry, and record count."""

    if not shapefile_path.is_file() or shapefile_path.stat().st_size == 0:
        raise FileNotFoundError(f"DA boundary shapefile not found: {shapefile_path}")
    missing = sorted(
        suffix
        for suffix in REQUIRED_BOUNDARY_SIDECARS
        if not shapefile_path.with_suffix(suffix).is_file()
    )
    if missing:
        raise FileNotFoundError(
            f"Missing DA boundary shapefile sidecar(s): {missing}"
        )

    info = pyogrio.read_info(shapefile_path)
    if info["crs"] != EXPECTED_BOUNDARY_CRS:
        raise ValueError(
            f"Expected {EXPECTED_BOUNDARY_CRS}, found {info['crs']!r} in "
            f"{shapefile_path.name}."
        )
    fields = set(info["fields"])
    missing_fields = sorted(REQUIRED_BOUNDARY_FIELDS - fields)
    if missing_fields:
        raise ValueError(f"DA boundary fields are missing: {missing_fields}")
    if info["features"] != EXPECTED_BOUNDARY_FEATURES:
        raise ValueError(
            f"Expected {EXPECTED_BOUNDARY_FEATURES:,} cartographic DAs, found "
            f"{info['features']:,}."
        )
    if info["geometry_type"] not in {"Polygon", "MultiPolygon"}:
        raise ValueError(
            f"Unexpected DA geometry type: {info['geometry_type']!r}."
        )
    return shapefile_path


def validate_population_table(table_path: Path) -> Path:
    """Validate the identity and minimum join schema of the Census table."""

    if not table_path.is_file() or table_path.stat().st_size == 0:
        raise FileNotFoundError(f"Population table not found: {table_path}")
    with table_path.open("r", encoding="utf-8-sig", newline="") as file:
        header = next(csv.reader(file), None)
    fields = set(header or [])
    missing = sorted(REQUIRED_TABLE_FIELDS - fields)
    if missing:
        raise ValueError(f"Population table fields are missing: {missing}")
    if POPULATION_DENSITY_FIELD not in fields:
        raise ValueError(
            "Population table is missing the 2021 population-density column: "
            f"{POPULATION_DENSITY_FIELD!r}"
        )
    return table_path


def acquire_residential(
    raw_residential: Path = RAW_RESIDENTIAL,
    overwrite: bool = False,
) -> ResidentialAcquisitionResult:
    """Acquire and validate DA boundaries and the population-density table."""

    raw_residential.mkdir(parents=True, exist_ok=True)
    boundary_path = raw_residential / BOUNDARY_SHAPEFILE_NAME
    population_path = raw_residential / POPULATION_TABLE_NAME

    if not overwrite and boundary_path.exists() and population_path.exists():
        return {
            "boundaries": validate_boundary_shapefile(boundary_path),
            "population_table": validate_population_table(population_path),
        }

    resources = (
        (
            BOUNDARY_ARCHIVE_URL,
            BOUNDARY_ARCHIVE_NAME,
            validate_boundary_archive,
        ),
        (
            POPULATION_TABLE_URL,
            POPULATION_ARCHIVE_NAME,
            validate_population_archive,
        ),
    )
    for url, archive_name, archive_validator in resources:
        archive_path = download_file(
            url,
            raw_residential / archive_name,
            overwrite=overwrite,
        )
        archive_validator(archive_path)
        _extract_flattened(archive_path, raw_residential)
        archive_path.unlink(missing_ok=True)

    return {
        "boundaries": validate_boundary_shapefile(boundary_path),
        "population_table": validate_population_table(population_path),
    }


def parse_args() -> argparse.Namespace:
    """Parse standalone acquisition arguments."""

    parser = argparse.ArgumentParser(
        description="Acquire 2021 Census residential-impedance inputs."
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--raw-residential-dir", type=Path, default=RAW_RESIDENTIAL
    )
    return parser.parse_args()


def main() -> None:
    """Run the standalone residential Bronze acquisition."""

    args = parse_args()
    result = acquire_residential(args.raw_residential_dir, args.overwrite)
    print(f"DA boundaries ready: {result['boundaries']}")
    print(f"Population table ready: {result['population_table']}")


if __name__ == "__main__":
    main()
