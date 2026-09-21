"""Acquire the national Aboriginal Lands of Canada boundary shapefile.

The official NRCan distribution uses a stable archive URL while versioning the
shapefile name inside the ZIP.  This module preserves that source filename,
validates the complete shapefile set, snapshots the WMS capabilities document,
and records the acquired snapshot in a JSON manifest.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from xml.etree import ElementTree

import pyogrio
import requests
from tqdm.auto import tqdm

from geocanoe import __version__
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
RAW_ABORIGINAL_LANDS = PROJECT_ROOT / "data_files" / "raw" / "aboriginal_lands"

DATASET_PAGE = (
    "https://open.canada.ca/data/en/dataset/"
    "522b07b9-78e2-4819-b736-ad9208eb1067"
)
DOWNLOAD_DIRECTORY = (
    "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/"
    "geobase_al_ta/shp_eng"
)
ARCHIVE_NAME = "AL_TA_CA_SHP_eng.zip"
ARCHIVE_URL = f"{DOWNLOAD_DIRECTORY}/{ARCHIVE_NAME}"
WMS_CAPABILITIES_URL = (
    "https://proxyinternet.nrcan-rncan.gc.ca/arcgis/services/CLSS-SATC/"
    "CLSS_Administrative_Boundaries/MapServer/WMSServer"
    "?request=GetCapabilities&service=WMS&version=1.3.0&layers=1"
    "&legend_format=image%2Fpng&feature_info_type=text%2Fhtml"
)
XML_NAME = "aboriginal_lands_wms_capabilities.xml"
MANIFEST_NAME = "aboriginal_lands_acquisition_manifest.json"
SHAPEFILE_PATTERN = "AL_TA_CA_*_eng.shp"
REQUIRED_SIDECARS = {".dbf", ".prj", ".shx"}
REQUIRED_FIELDS = {
    "ACQTECH",
    "METACOVER",
    "CREDATE",
    "REVDATE",
    "ACCURACY",
    "PROVIDER",
    "DATASETNAM",
    "SPECVERS",
    "NID",
    "ALCODE",
    "NAME1",
    "JUR1",
    "ALTYPE",
    "WEBREF",
}
EXPECTED_CRS = "EPSG:4617"

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


class AboriginalLandsAcquisitionResult(TypedDict):
    """Validated paths produced by the Aboriginal Lands Bronze acquisition."""

    shapefile: Path
    metadata_xml: Path
    archive: Path | None
    manifest: Path


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
    """Download a source file atomically, resuming partial transfers if possible."""

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


def _archive_shapefile(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """Find the single national English shapefile member in an open archive."""

    matches = [
        info
        for info in archive.infolist()
        if not info.is_dir() and Path(info.filename).match(SHAPEFILE_PATTERN)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {SHAPEFILE_PATTERN!r} member; found "
            f"{len(matches)}."
        )
    return matches[0]


def validate_archive(archive_path: Path) -> Path:
    """Validate the archive's shapefile identity, members, and non-empty data."""

    if not archive_path.is_file():
        raise FileNotFoundError(f"Aboriginal Lands archive not found: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        shapefile = _archive_shapefile(archive)
        by_name = {Path(info.filename).name: info for info in archive.infolist()}
        missing = sorted(
            suffix
            for suffix in REQUIRED_SIDECARS
            if shapefile.filename.rsplit(".", 1)[0].split("/")[-1] + suffix
            not in by_name
        )
        if missing:
            raise FileNotFoundError(
                f"Archive is missing required shapefile sidecar(s): {missing}"
            )
        if shapefile.file_size <= 0:
            raise ValueError(f"Shapefile member is empty in {archive_path.name}.")
    return archive_path


def find_shapefile(output_dir: Path) -> Path:
    """Find the single extracted national English Aboriginal Lands shapefile."""

    matches = sorted(output_dir.glob(SHAPEFILE_PATTERN))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {SHAPEFILE_PATTERN!r} in {output_dir}; "
            f"found {len(matches)}: {[path.name for path in matches]}"
        )
    return matches[0]


def validate_shapefile(shapefile_path: Path) -> Path:
    """Validate the shapefile components, CRS, geometry type, and source fields."""

    if not shapefile_path.is_file() or shapefile_path.stat().st_size == 0:
        raise FileNotFoundError(f"Aboriginal Lands shapefile not found: {shapefile_path}")
    missing = sorted(
        suffix
        for suffix in REQUIRED_SIDECARS
        if not shapefile_path.with_suffix(suffix).is_file()
    )
    if missing:
        raise FileNotFoundError(
            f"Missing required shapefile sidecar(s) for {shapefile_path.name}: {missing}"
        )

    info = pyogrio.read_info(shapefile_path)
    if info["crs"] != EXPECTED_CRS:
        raise ValueError(
            f"Expected {EXPECTED_CRS}, found {info['crs']!r} in {shapefile_path.name}."
        )
    if info["geometry_type"] not in {"Polygon", "MultiPolygon"}:
        raise ValueError(
            f"Expected polygon geometry, found {info['geometry_type']!r} in "
            f"{shapefile_path.name}."
        )
    fields = set(info["fields"])
    missing_fields = sorted(REQUIRED_FIELDS - fields)
    if missing_fields:
        raise ValueError(
            "Aboriginal Lands shapefile is missing expected field(s): "
            + ", ".join(missing_fields)
        )
    return shapefile_path


def validate_capabilities_xml(xml_path: Path) -> Path:
    """Validate the supplied WMS 1.3.0 capabilities snapshot."""

    if not xml_path.is_file():
        raise FileNotFoundError(f"WMS capabilities XML not found: {xml_path}")
    try:
        root = ElementTree.parse(xml_path).getroot()
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid WMS capabilities XML: {xml_path}") from exc
    local_name = root.tag.rsplit("}", maxsplit=1)[-1]
    if local_name != "WMS_Capabilities" or root.attrib.get("version") != "1.3.0":
        raise ValueError(f"Expected a WMS 1.3.0 capabilities document: {xml_path}")
    values = {
        (element.text or "").strip()
        for element in root.iter()
        if element.tag.rsplit("}", maxsplit=1)[-1] in {"Name", "Title"}
    }
    if "Aboriginal_Lands_of_Canada_Legislative_Boundaries" not in values:
        raise ValueError(
            f"Capabilities XML does not identify Aboriginal Lands of Canada: {xml_path}"
        )
    return xml_path


def extract_archive(
    archive_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Extract and validate the source archive without partially replacing output."""

    validate_archive(archive_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_dir.glob(SHAPEFILE_PATTERN))
    if existing and not overwrite:
        print(f"[Skip extract] {existing[0].name} already exists.")
        return validate_shapefile(find_shapefile(output_dir))

    with tempfile.TemporaryDirectory(prefix=".aboriginal_lands_", dir=output_dir) as tmp:
        staging = Path(tmp)
        with zipfile.ZipFile(archive_path) as archive:
            print(f"[Extract] {archive_path.name}")
            archive.extractall(staging)
        staged_shapefile = find_shapefile(staging)
        validate_shapefile(staged_shapefile)

        source_stem = staged_shapefile.stem
        if overwrite:
            for old_shapefile in existing:
                for old_component in output_dir.glob(f"{old_shapefile.stem}.*"):
                    old_component.unlink()
        for source in sorted(staging.rglob("*")):
            if source.is_file():
                destination = output_dir / source.name
                destination.unlink(missing_ok=True)
                shutil.move(str(source), destination)

    destination = output_dir / f"{source_stem}.shp"
    validate_shapefile(destination)
    print(f"[Complete extract] {destination.name}")
    return destination


def write_acquisition_manifest(
    raw_dir: Path,
    shapefile_path: Path,
    xml_path: Path,
    archive_path: Path | None,
) -> Path:
    """Record the validated local snapshot and its authoritative sources."""

    manifest_path = raw_dir / MANIFEST_NAME
    component_paths = sorted(shapefile_path.parent.glob(f"{shapefile_path.stem}.*"))
    manifest = {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_page": DATASET_PAGE,
        "distribution_url": ARCHIVE_URL,
        "metadata_url": WMS_CAPABILITIES_URL,
        "source_crs": EXPECTED_CRS,
        "shapefile": {
            "path": str(shapefile_path),
            "size_bytes": shapefile_path.stat().st_size,
            "components": [path.name for path in component_paths],
        },
        "metadata_xml": {
            "path": str(xml_path),
            "size_bytes": xml_path.stat().st_size,
        },
        "archive": (
            {"path": str(archive_path), "size_bytes": archive_path.stat().st_size}
            if archive_path is not None
            else None
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def acquire_aboriginal_lands(
    raw_aboriginal_lands: Path = RAW_ABORIGINAL_LANDS,
    *,
    overwrite: bool = False,
    keep_archive: bool = False,
) -> AboriginalLandsAcquisitionResult:
    """Acquire and validate the national Aboriginal Lands Bronze inputs."""

    raw_dir = raw_aboriginal_lands.expanduser().resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_path = raw_dir / ARCHIVE_NAME
    xml_path = raw_dir / XML_NAME

    print("Acquiring Aboriginal Lands of Canada legislative boundaries...")
    print(f"Dataset page: {DATASET_PAGE}")
    print(f"Shapefile archive: {ARCHIVE_URL}")
    print(f"Raw Aboriginal Lands folder: {raw_dir}\n")

    existing = sorted(raw_dir.glob(SHAPEFILE_PATTERN))
    if existing and not overwrite:
        print(f"[Reuse] Validating existing {existing[0].name} before download.")
        shapefile_path = validate_shapefile(find_shapefile(raw_dir))
    else:
        download_file(ARCHIVE_URL, archive_path, overwrite=overwrite)
        validate_archive(archive_path)
        shapefile_path = extract_archive(
            archive_path,
            raw_dir,
            overwrite=overwrite,
        )

    download_file(WMS_CAPABILITIES_URL, xml_path, overwrite=overwrite)
    validate_capabilities_xml(xml_path)
    validate_shapefile(shapefile_path)

    if archive_path.exists() and not keep_archive:
        archive_path.unlink()
        print(f"[Delete archive] {archive_path.name}")
    archive = archive_path if archive_path.exists() else None
    manifest_path = write_acquisition_manifest(
        raw_dir,
        shapefile_path,
        xml_path,
        archive,
    )
    return AboriginalLandsAcquisitionResult(
        shapefile=shapefile_path,
        metadata_xml=xml_path,
        archive=archive,
        manifest=manifest_path,
    )


def print_summary(result: AboriginalLandsAcquisitionResult) -> None:
    """Print a compact acquisition summary."""

    print("\nAboriginal Lands acquisition summary")
    print("------------------------------------")
    print(f"Shapefile:    {result['shapefile']}")
    print(f"Metadata XML: {result['metadata_xml']}")
    print(f"Manifest:     {result['manifest']}")
    if result["archive"] is not None:
        print(f"Archive:      {result['archive']}")


def parse_args() -> argparse.Namespace:
    """Parse command-line options for Aboriginal Lands acquisition."""

    parser = argparse.ArgumentParser(
        description="Download, extract, and validate Aboriginal Lands boundaries."
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--raw-aboriginal-lands-dir",
        type=Path,
        default=RAW_ABORIGINAL_LANDS,
        help="Output directory. Defaults to data_files/raw/aboriginal_lands/.",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Retain the source ZIP after successful validation.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the Aboriginal Lands acquisition workflow."""

    args = parse_args()
    result = acquire_aboriginal_lands(
        raw_aboriginal_lands=args.raw_aboriginal_lands_dir,
        overwrite=args.overwrite,
        keep_archive=args.keep_archive,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
