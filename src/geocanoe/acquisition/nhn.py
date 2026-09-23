"""Acquire the national National Hydro Network hydrographic features.

The Natural Resources Canada national GeoPackage distribution contains several
large archives.  GeoCANOE currently requires only the hydrographic-feature
archive, ``rhn_nhn_hhyd.gpkg.zip``.  This module downloads that archive,
extracts its GeoPackage without modifying it, and snapshots the official NHN
WMS capabilities XML as source metadata.

Existing validated GeoPackages are reused before considering the approximately
15 GB archive download. Downloads use ``.part`` files, HTTP range requests, and
a byte-based progress bar so interrupted transfers are visible, are not mistaken
for complete Bronze inputs, and may be resumed when the server supports ranges.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from xml.etree import ElementTree

import requests
from tqdm.auto import tqdm

from geocanoe import __version__
from geocanoe.acquisition._download import download_resumable
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
RAW_NHN = PROJECT_ROOT / "data_files" / "raw" / "nhn"

NHN_DATASET_PAGE = (
    "https://open.canada.ca/data/en/dataset/"
    "a4b190fe-e090-4e6d-881e-b87956c07977"
)
NHN_GPKG_RESOURCE_PAGE = (
    f"{NHN_DATASET_PAGE}/resource/c993b360-f86e-4c1d-92c2-e0b4c9268671"
)
NHN_XML_RESOURCE_PAGE = (
    f"{NHN_DATASET_PAGE}/resource/f6ca81dd-a1ee-4002-ac94-bfaa1118e8ea"
)
NHN_DOWNLOAD_DIRECTORY = (
    "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/"
    "geobase_nhn_rhn/gpkg_en/CA"
)
NHN_ARCHIVE_NAME = "rhn_nhn_hhyd.gpkg.zip"
NHN_GPKG_NAME = "rhn_nhn_hhyd.gpkg"
NHN_ARCHIVE_URL = f"{NHN_DOWNLOAD_DIRECTORY}/{NHN_ARCHIVE_NAME}"
NHN_WMS_CAPABILITIES_URL = (
    "https://maps.geogratis.gc.ca/wms/hydro_network_en"
    "?request=GetCapabilities&service=WMS&version=1.3.0"
    "&layers=hydro_network&legend_format=image%2Fpng"
    "&feature_info_type=text%2Fhtml"
)
NHN_XML_NAME = "nhn_wms_capabilities.xml"
NHN_MANIFEST_NAME = "nhn_acquisition_manifest.json"

EXPECTED_GPKG_APPLICATION_ID = 1_196_444_487
EXPECTED_LAYERS = {
    "nhn_hhyd_Island_2",
    "nhn_hhyd_Manmade_0",
    "nhn_hhyd_Manmade_1",
    "nhn_hhyd_Manmade_2",
    "nhn_hhyd_Obstacle_0",
    "nhn_hhyd_Obstacle_1",
    "nhn_hhyd_Obstacle_2",
    "nhn_hhyd_S_L_Watercourse_1",
    "nhn_hhyd_Waterbody_2",
}
EXPECTED_WATERBODY_FIELDS = {
    "geom",
    "nid",
    "water_definition",
    "permanency",
    "isolated",
    "code_spec",
}

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


class NHNAcquisitionResult(TypedDict):
    """Validated paths produced by the NHN Bronze acquisition."""

    gpkg: Path
    metadata_xml: Path
    archive: Path | None
    manifest: Path


def write_acquisition_manifest(
    raw_nhn: Path,
    gpkg_path: Path,
    xml_path: Path,
    archive_path: Path | None,
) -> Path:
    """Record the validated local snapshot and its authoritative source URLs."""

    manifest_path = raw_nhn / NHN_MANIFEST_NAME
    manifest = {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_page": NHN_DATASET_PAGE,
        "gpkg_resource_page": NHN_GPKG_RESOURCE_PAGE,
        "metadata_resource_page": NHN_XML_RESOURCE_PAGE,
        "distribution_url": NHN_ARCHIVE_URL,
        "metadata_url": NHN_WMS_CAPABILITIES_URL,
        "gpkg": {
            "path": str(gpkg_path),
            "size_bytes": gpkg_path.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                gpkg_path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat(),
        },
        "metadata_xml": {
            "path": str(xml_path),
            "size_bytes": xml_path.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                xml_path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat(),
        },
        "archive": (
            {
                "path": str(archive_path),
                "size_bytes": archive_path.stat().st_size,
            }
            if archive_path is not None
            else None
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def download_file(
    url: str,
    destination: Path,
    *,
    overwrite: bool = False,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Download a source file atomically, resuming partial transfers when possible."""

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
        sleep=time.sleep,
    )


def validate_archive(archive_path: Path) -> Path:
    """Validate that the NHN ZIP contains one non-empty expected GeoPackage."""

    if not archive_path.is_file():
        raise FileNotFoundError(f"NHN archive not found: {archive_path}")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if Path(info.filename).name == NHN_GPKG_NAME and not info.is_dir()
            ]
    except zipfile.BadZipFile:
        raise

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {NHN_GPKG_NAME} member in "
            f"{archive_path.name}; found {len(matches)}."
        )
    if matches[0].file_size <= 0:
        raise ValueError(f"NHN GeoPackage member is empty in {archive_path.name}.")
    return archive_path


def validate_gpkg(gpkg_path: Path) -> Path:
    """Validate the GeoPackage identity, layer family, CRS, and lake fields."""

    if not gpkg_path.is_file():
        raise FileNotFoundError(f"NHN GeoPackage not found: {gpkg_path}")
    if gpkg_path.stat().st_size == 0:
        raise ValueError(f"NHN GeoPackage is empty: {gpkg_path}")

    with gpkg_path.open("rb") as file:
        if file.read(16) != b"SQLite format 3\x00":
            raise ValueError(f"NHN output is not a SQLite GeoPackage: {gpkg_path}")

    connection = sqlite3.connect(gpkg_path)
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        if application_id != EXPECTED_GPKG_APPLICATION_ID:
            raise ValueError(
                f"Unexpected GeoPackage application_id {application_id} in "
                f"{gpkg_path.name}."
            )

        contents = connection.execute(
            "SELECT table_name, data_type, srs_id FROM gpkg_contents"
        ).fetchall()
        layer_rows = {name: (data_type, srs_id) for name, data_type, srs_id in contents}
        missing_layers = sorted(EXPECTED_LAYERS - set(layer_rows))
        if missing_layers:
            raise ValueError(
                "NHN GeoPackage is missing expected layer(s): "
                + ", ".join(missing_layers)
            )

        invalid_layers = sorted(
            name
            for name in EXPECTED_LAYERS
            if layer_rows[name] != ("features", 4617)
        )
        if invalid_layers:
            raise ValueError(
                "Expected NHN feature layers in EPSG:4617: "
                + ", ".join(invalid_layers)
            )

        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("nhn_hhyd_Waterbody_2")'
            ).fetchall()
        }
        missing_fields = sorted(EXPECTED_WATERBODY_FIELDS - columns)
        if missing_fields:
            raise ValueError(
                "NHN waterbody layer is missing expected field(s): "
                + ", ".join(missing_fields)
            )
    finally:
        connection.close()

    return gpkg_path


def validate_capabilities_xml(xml_path: Path) -> Path:
    """Validate the downloaded NHN WMS 1.3.0 capabilities document."""

    if not xml_path.is_file():
        raise FileNotFoundError(f"NHN capabilities XML not found: {xml_path}")
    try:
        root = ElementTree.parse(xml_path).getroot()
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid NHN capabilities XML: {xml_path}") from exc

    local_name = root.tag.rsplit("}", maxsplit=1)[-1]
    if local_name != "WMS_Capabilities" or root.attrib.get("version") != "1.3.0":
        raise ValueError(
            f"Expected a WMS 1.3.0 capabilities document: {xml_path}"
        )

    titles = [
        (element.text or "").strip()
        for element in root.iter()
        if element.tag.rsplit("}", maxsplit=1)[-1] == "Title"
    ]
    if not any("National Hydro Network" in title for title in titles):
        raise ValueError(f"Capabilities XML does not identify the NHN: {xml_path}")
    return xml_path


def extract_hhyd_archive(
    archive_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Extract the hydrographic GeoPackage atomically from its validated ZIP."""

    validate_archive(archive_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / NHN_GPKG_NAME

    if destination.exists() and not overwrite:
        print(f"[Skip extract] {destination.name} already exists.")
        return validate_gpkg(destination)

    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            member = next(
                info
                for info in archive.infolist()
                if Path(info.filename).name == NHN_GPKG_NAME and not info.is_dir()
            )
            print(f"[Extract] {archive_path.name} -> {destination.name}")
            with archive.open(member) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_SIZE)

        validate_gpkg(partial)
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    print(f"[Complete extract] {destination.name}")
    return destination


def acquire_nhn(
    raw_nhn: Path = RAW_NHN,
    *,
    overwrite: bool = False,
    keep_archive: bool = False,
) -> NHNAcquisitionResult:
    """Acquire and validate the national NHN hydrographic-feature Bronze inputs.

    The source ZIP is deleted by default only after the extracted GeoPackage and
    capabilities XML have both passed validation. Set ``keep_archive`` to retain
    the compressed source alongside the extracted Bronze artifact.
    """

    raw_nhn = raw_nhn.expanduser().resolve()
    raw_nhn.mkdir(parents=True, exist_ok=True)
    archive_path = raw_nhn / NHN_ARCHIVE_NAME
    gpkg_path = raw_nhn / NHN_GPKG_NAME
    xml_path = raw_nhn / NHN_XML_NAME

    print("Acquiring National Hydro Network hydrographic features...")
    print(f"Dataset page: {NHN_DATASET_PAGE}")
    print(f"GeoPackage resource: {NHN_GPKG_RESOURCE_PAGE}")
    print(f"WMS XML resource: {NHN_XML_RESOURCE_PAGE}")
    print(f"Raw NHN folder: {raw_nhn}\n")

    if gpkg_path.exists() and not overwrite:
        print(f"[Reuse] Validating existing {gpkg_path.name} before any archive download.")
        validate_gpkg(gpkg_path)
    else:
        download_file(
            NHN_ARCHIVE_URL,
            archive_path,
            overwrite=overwrite,
        )
        validate_archive(archive_path)
        extract_hhyd_archive(
            archive_path,
            raw_nhn,
            overwrite=overwrite,
        )

    download_file(
        NHN_WMS_CAPABILITIES_URL,
        xml_path,
        overwrite=overwrite,
    )
    validate_capabilities_xml(xml_path)
    validate_gpkg(gpkg_path)

    if archive_path.exists() and not keep_archive:
        archive_path.unlink()
        print(f"[Delete archive] {archive_path.name}")

    archive = archive_path if archive_path.exists() else None
    manifest_path = write_acquisition_manifest(
        raw_nhn,
        gpkg_path,
        xml_path,
        archive,
    )
    return NHNAcquisitionResult(
        gpkg=gpkg_path,
        metadata_xml=xml_path,
        archive=archive,
        manifest=manifest_path,
    )


def print_summary(result: NHNAcquisitionResult) -> None:
    """Print a compact NHN acquisition summary."""

    print("\nNHN acquisition summary")
    print("-----------------------")
    print(f"GeoPackage:   {result['gpkg']}")
    print(f"Metadata XML: {result['metadata_xml']}")
    print(f"Manifest:     {result['manifest']}")
    if result["archive"] is not None:
        print(f"Archive:      {result['archive']}")


def parse_args() -> argparse.Namespace:
    """Parse command-line options for national NHN acquisition."""

    parser = argparse.ArgumentParser(
        description=(
            "Download, extract, and validate national NHN hydrographic features."
        )
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload and re-extract NHN files even when outputs already exist.",
    )
    parser.add_argument(
        "--raw-nhn-dir",
        type=Path,
        default=RAW_NHN,
        help="Output directory for NHN files. Defaults to data_files/raw/nhn/.",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Retain the approximately 15 GB source ZIP after validation.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the national NHN acquisition workflow."""

    args = parse_args()
    result = acquire_nhn(
        raw_nhn=args.raw_nhn_dir,
        overwrite=args.overwrite,
        keep_archive=args.keep_archive,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
