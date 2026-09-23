"""Acquire raw Statistics Canada inputs for spatial gasoline demand.

The Bronze bundle contains the annual provincial and territorial motor-fuel
sales table and the 2021 national population-centre boundary file.  Existing
dissemination-area population inputs remain owned by the independent
``residential`` Bronze stage and are intentionally not copied here.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import pyogrio
import requests
from tqdm.auto import tqdm

from geocanoe import __version__
from geocanoe.acquisition._download import download_resumable
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
RAW_GASOLINE_DEMAND = PROJECT_ROOT / "data_files" / "raw" / "gasoline_demand"

FUEL_SALES_DIRNAME = "fuel_sales"
POPULATION_CENTRES_DIRNAME = "population_centres"

FUEL_SALES_TABLE_PAGE = (
    "https://www150.statcan.gc.ca/t1/tbl1/en/"
    "tv.action?pid=2310006601"
)
FUEL_SALES_ARCHIVE_URL = (
    "https://www150.statcan.gc.ca/n1/en/tbl/csv/23100066-eng.zip"
)
FUEL_SALES_ARCHIVE_NAME = "23100066-eng.zip"
FUEL_SALES_TABLE_NAME = "23100066.csv"
FUEL_SALES_METADATA_NAME = "23100066_MetaData.csv"

POPULATION_CENTRE_CATALOGUE_URL = (
    "https://www150.statcan.gc.ca/n1/en/catalogue/92-166-X"
)
POPULATION_CENTRE_ARCHIVE_URL = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
    "sip-pis/boundary-limites/files-fichiers/lpc_000b21a_e.zip"
)
POPULATION_CENTRE_ARCHIVE_NAME = "lpc_000b21a_e.zip"
POPULATION_CENTRE_STEM = "lpc_000b21a_e"
POPULATION_CENTRE_SHAPEFILE_NAME = f"{POPULATION_CENTRE_STEM}.shp"
POPULATION_CENTRE_METADATA_NAME = f"{POPULATION_CENTRE_STEM}.xml"

NRCAN_DISTRIBUTION_REFERENCE_URL = (
    "https://natural-resources.canada.ca/energy-sources/fossil-fuels/"
    "petroleum-products-distribution-networks"
)

MANIFEST_NAME = "gasoline_demand_acquisition_manifest.json"
EXPECTED_POPULATION_CENTRE_CRS = "EPSG:3347"
EXPECTED_POPULATION_CENTRE_RECORDS = 1_030
EXPECTED_POPULATION_CENTRES = 1_026
REQUIRED_POPULATION_CENTRE_FIELDS = {
    "PCUID",
    "PCPUID",
    "DGUID",
    "DGUIDP",
    "PCNAME",
    "PCTYPE",
    "PCCLASS",
    "PRUID",
}
REQUIRED_BOUNDARY_SIDECARS = {".dbf", ".prj", ".shx"}
REQUIRED_FUEL_SALES_FIELDS = {
    "REF_DATE",
    "GEO",
    "DGUID",
    "UOM",
    "SCALAR_FACTOR",
    "VALUE",
}
NET_GASOLINE_LABEL = "Net sales of gasoline"

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


class GasolineDemandAcquisitionResult(TypedDict):
    """Validated artifacts produced by the gasoline-demand Bronze stage."""

    fuel_sales: Path
    fuel_sales_metadata: Path
    population_centres: Path
    population_centres_metadata: Path
    manifest: Path


def download_file(
    url: str,
    destination: Path,
    *,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download one source archive atomically with resume support."""

    return download_resumable(
        url,
        destination,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        max_retries=max_retries,
        retry_delay=RETRY_DELAY,
        chunk_size=CHUNK_SIZE,
        overwrite=overwrite,
        request_get=requests.get,
        progress_factory=tqdm,
        report_completion=False,
        sleep=time.sleep,
    )


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = [member for member in archive.infolist() if not member.is_dir()]
    for member in members:
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe ZIP member path: {member.filename!r}")
    return members


def validate_fuel_sales_archive(archive_path: Path) -> Path:
    """Confirm that the bulk table archive includes data and metadata CSVs."""

    with zipfile.ZipFile(archive_path) as archive:
        names = {Path(member.filename).name for member in _safe_members(archive)}
    required = {FUEL_SALES_TABLE_NAME, FUEL_SALES_METADATA_NAME}
    missing = sorted(required - names)
    if missing:
        raise FileNotFoundError(
            f"Fuel-sales archive is missing required member(s): {missing}"
        )
    return archive_path


def validate_population_centres_archive(archive_path: Path) -> Path:
    """Confirm that the population-centre archive is a complete shapefile."""

    with zipfile.ZipFile(archive_path) as archive:
        names = {Path(member.filename).name for member in _safe_members(archive)}
    required = {
        POPULATION_CENTRE_SHAPEFILE_NAME,
        POPULATION_CENTRE_METADATA_NAME,
    } | {
        f"{POPULATION_CENTRE_STEM}{suffix}"
        for suffix in REQUIRED_BOUNDARY_SIDECARS
    }
    missing = sorted(required - names)
    if missing:
        raise FileNotFoundError(
            "Population-centre archive is missing required member(s): "
            f"{missing}"
        )
    return archive_path


def _extract_flattened(archive_path: Path, output_dir: Path) -> list[Path]:
    """Extract archive members atomically and flatten source directories."""

    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix=".gasoline_demand_", dir=output_dir
    ) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging, _safe_members(archive))
        for source in sorted(staging.rglob("*")):
            if not source.is_file():
                continue
            destination = output_dir / source.name
            destination.unlink(missing_ok=True)
            shutil.move(str(source), destination)
            extracted.append(destination)
    return extracted


def validate_fuel_sales_table(table_path: Path) -> Path:
    """Validate the table schema and presence of net gasoline observations."""

    if not table_path.is_file() or table_path.stat().st_size == 0:
        raise FileNotFoundError(f"Fuel-sales table not found: {table_path}")
    with table_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_FUEL_SALES_FIELDS - fields)
        if missing:
            raise ValueError(f"Fuel-sales table fields are missing: {missing}")
        found_net_gasoline = any(
            NET_GASOLINE_LABEL in {str(value).strip() for value in row.values()}
            for row in reader
        )
    if not found_net_gasoline:
        raise ValueError(
            f"Fuel-sales table contains no {NET_GASOLINE_LABEL!r} observations."
        )
    return table_path


def validate_csv_metadata(metadata_path: Path) -> Path:
    """Require a non-empty Statistics Canada metadata CSV."""

    if not metadata_path.is_file() or metadata_path.stat().st_size == 0:
        raise FileNotFoundError(f"Fuel-sales metadata not found: {metadata_path}")
    return metadata_path


def validate_population_centres(shapefile_path: Path) -> Path:
    """Validate identity, schema, CRS, and national record/centre counts."""

    if not shapefile_path.is_file() or shapefile_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Population-centre shapefile not found: {shapefile_path}"
        )
    missing_sidecars = sorted(
        suffix
        for suffix in REQUIRED_BOUNDARY_SIDECARS
        if not shapefile_path.with_suffix(suffix).is_file()
    )
    if missing_sidecars:
        raise FileNotFoundError(
            "Population-centre shapefile is missing sidecar(s): "
            f"{missing_sidecars}"
        )
    info = pyogrio.read_info(shapefile_path)
    if info["crs"] != EXPECTED_POPULATION_CENTRE_CRS:
        raise ValueError(
            f"Expected {EXPECTED_POPULATION_CENTRE_CRS}, found "
            f"{info['crs']!r} in {shapefile_path.name}."
        )
    if info["features"] != EXPECTED_POPULATION_CENTRE_RECORDS:
        raise ValueError(
            f"Expected {EXPECTED_POPULATION_CENTRE_RECORDS:,} population-"
            f"centre parts, found {info['features']:,}."
        )
    if info["geometry_type"] not in {"Polygon", "MultiPolygon"}:
        raise ValueError(
            f"Unexpected population-centre geometry: {info['geometry_type']!r}."
        )
    missing_fields = sorted(
        REQUIRED_POPULATION_CENTRE_FIELDS - set(info["fields"])
    )
    if missing_fields:
        raise ValueError(
            f"Population-centre fields are missing: {missing_fields}"
        )
    identifiers = pyogrio.read_dataframe(
        shapefile_path,
        columns=["PCUID"],
        read_geometry=False,
    )
    centre_count = identifiers["PCUID"].nunique(dropna=True)
    if centre_count != EXPECTED_POPULATION_CENTRES:
        raise ValueError(
            f"Expected {EXPECTED_POPULATION_CENTRES:,} unique population "
            f"centres, found {centre_count:,}."
        )
    return shapefile_path


def validate_population_centres_metadata(metadata_path: Path) -> Path:
    """Require the source XML metadata distributed with the boundary file."""

    if not metadata_path.is_file() or metadata_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Population-centre metadata not found: {metadata_path}"
        )
    return metadata_path


def write_acquisition_manifest(
    raw_dir: Path,
    fuel_sales: Path,
    fuel_sales_metadata: Path,
    population_centres: Path,
    population_centres_metadata: Path,
) -> Path:
    """Record authoritative sources and validated local Bronze artifacts."""

    manifest_path = raw_dir / MANIFEST_NAME
    manifest = {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "stage": "gasoline_demand",
        "sources": {
            "fuel_sales": {
                "publisher": "Statistics Canada",
                "table": "23-10-0066-01",
                "catalogue_url": FUEL_SALES_TABLE_PAGE,
                "distribution_url": FUEL_SALES_ARCHIVE_URL,
                "data": str(fuel_sales.relative_to(raw_dir)),
                "metadata": str(fuel_sales_metadata.relative_to(raw_dir)),
            },
            "population_centres": {
                "publisher": "Statistics Canada",
                "census_year": 2021,
                "catalogue_url": POPULATION_CENTRE_CATALOGUE_URL,
                "distribution_url": POPULATION_CENTRE_ARCHIVE_URL,
                "source_crs": EXPECTED_POPULATION_CENTRE_CRS,
                "record_count": EXPECTED_POPULATION_CENTRE_RECORDS,
                "unique_centre_count": EXPECTED_POPULATION_CENTRES,
                "record_granularity": (
                    "Provincial population-centre parts; four centres cross "
                    "a provincial boundary and each has two records."
                ),
                "boundaries": str(population_centres.relative_to(raw_dir)),
                "metadata": str(
                    population_centres_metadata.relative_to(raw_dir)
                ),
            },
        },
        "methodology_reference": {
            "publisher": "Natural Resources Canada",
            "url": NRCAN_DISTRIBUTION_REFERENCE_URL,
            "role": (
                "Conceptual support for treating population-centre hubs as "
                "regional bulk-terminal proxies; not a terminal inventory."
            ),
        },
        "reused_population_source": {
            "stage": "residential",
            "registry_id": "statcan_2021_da_population_density_inputs",
            "role": "Dissemination-area population weights for demand allocation.",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def acquire_gasoline_demand(
    raw_gasoline_demand: Path = RAW_GASOLINE_DEMAND,
    *,
    overwrite: bool = False,
) -> GasolineDemandAcquisitionResult:
    """Acquire and validate the gasoline-demand Bronze source bundle."""

    raw_dir = raw_gasoline_demand.expanduser().resolve()
    fuel_dir = raw_dir / FUEL_SALES_DIRNAME
    centres_dir = raw_dir / POPULATION_CENTRES_DIRNAME
    fuel_dir.mkdir(parents=True, exist_ok=True)
    centres_dir.mkdir(parents=True, exist_ok=True)

    fuel_sales = fuel_dir / FUEL_SALES_TABLE_NAME
    fuel_metadata = fuel_dir / FUEL_SALES_METADATA_NAME
    centres = centres_dir / POPULATION_CENTRE_SHAPEFILE_NAME
    centres_metadata = centres_dir / POPULATION_CENTRE_METADATA_NAME
    fuel_archive = fuel_dir / FUEL_SALES_ARCHIVE_NAME
    centres_archive = centres_dir / POPULATION_CENTRE_ARCHIVE_NAME

    if not overwrite and all(
        path.exists()
        for path in (fuel_sales, fuel_metadata, centres, centres_metadata)
    ):
        validate_fuel_sales_table(fuel_sales)
        validate_csv_metadata(fuel_metadata)
        validate_population_centres(centres)
        validate_population_centres_metadata(centres_metadata)
    else:
        download_file(
            FUEL_SALES_ARCHIVE_URL,
            fuel_archive,
            overwrite=overwrite,
        )
        validate_fuel_sales_archive(fuel_archive)
        _extract_flattened(fuel_archive, fuel_dir)
        validate_fuel_sales_table(fuel_sales)
        validate_csv_metadata(fuel_metadata)

        download_file(
            POPULATION_CENTRE_ARCHIVE_URL,
            centres_archive,
            overwrite=overwrite,
        )
        validate_population_centres_archive(centres_archive)
        _extract_flattened(centres_archive, centres_dir)
        validate_population_centres(centres)
        validate_population_centres_metadata(centres_metadata)


    # Archives are transport artifacts, not part of the validated Bronze layer.
    fuel_archive.unlink(missing_ok=True)
    centres_archive.unlink(missing_ok=True)

    manifest = write_acquisition_manifest(
        raw_dir,
        fuel_sales,
        fuel_metadata,
        centres,
        centres_metadata,
    )
    return GasolineDemandAcquisitionResult(
        fuel_sales=fuel_sales,
        fuel_sales_metadata=fuel_metadata,
        population_centres=centres,
        population_centres_metadata=centres_metadata,
        manifest=manifest,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire Statistics Canada gasoline-demand Bronze inputs."
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--raw-gasoline-demand-dir",
        type=Path,
        default=RAW_GASOLINE_DEMAND,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = acquire_gasoline_demand(
        args.raw_gasoline_demand_dir,
        overwrite=args.overwrite,
    )
    print(f"Fuel sales:             {result['fuel_sales']}")
    print(f"Fuel-sales metadata:    {result['fuel_sales_metadata']}")
    print(f"Population centres:     {result['population_centres']}")
    print(f"Population metadata:    {result['population_centres_metadata']}")
    print(f"Acquisition manifest:   {result['manifest']}")


if __name__ == "__main__":
    main()
