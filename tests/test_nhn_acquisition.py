from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from types import ModuleType

from geocanoe.acquisition import nhn
from geocanoe.execution.bronze import (
    BRONZE_STAGE_ORDER,
    BronzeStageOptions,
    execute_bronze_stage,
)


def write_test_gpkg(path: Path) -> None:
    """Create the minimum SQLite structure required by NHN validation."""

    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA application_id = {nhn.EXPECTED_GPKG_APPLICATION_ID}")
        connection.execute(
            "CREATE TABLE gpkg_contents ("
            "table_name TEXT, data_type TEXT, srs_id INTEGER)"
        )
        for layer in sorted(nhn.EXPECTED_LAYERS):
            connection.execute(
                "INSERT INTO gpkg_contents VALUES (?, 'features', 4617)",
                (layer,),
            )
            if layer == "nhn_hhyd_Waterbody_2":
                connection.execute(
                    f'CREATE TABLE "{layer}" ('
                    "geom BLOB, nid TEXT, water_definition INTEGER, "
                    "permanency INTEGER, isolated INTEGER, code_spec INTEGER)"
                )
            else:
                connection.execute(f'CREATE TABLE "{layer}" (geom BLOB)')


def write_capabilities_xml(path: Path) -> None:
    path.write_text(
        "<?xml version='1.0'?>"
        "<WMS_Capabilities xmlns='http://www.opengis.net/wms' version='1.3.0'>"
        "<Service><Title>GOV Canada - National Hydro Network - NHN</Title>"
        "</Service></WMS_Capabilities>",
        encoding="utf-8",
    )


def test_archive_extract_and_gpkg_validation(tmp_path: Path) -> None:
    source_gpkg = tmp_path / "source.gpkg"
    write_test_gpkg(source_gpkg)
    archive = tmp_path / nhn.NHN_ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(source_gpkg, arcname=nhn.NHN_GPKG_NAME)

    output_dir = tmp_path / "raw" / "nhn"
    extracted = nhn.extract_hhyd_archive(archive, output_dir)

    assert extracted == output_dir / nhn.NHN_GPKG_NAME
    assert nhn.validate_archive(archive) == archive
    assert nhn.validate_gpkg(extracted) == extracted


def test_capabilities_xml_validation(tmp_path: Path) -> None:
    xml_path = tmp_path / nhn.NHN_XML_NAME
    write_capabilities_xml(xml_path)

    assert nhn.validate_capabilities_xml(xml_path) == xml_path


def test_download_reports_streamed_byte_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Response:
        status_code = 200
        headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == nhn.CHUNK_SIZE
            yield b"abc"
            yield b"def"

    progress_options: dict[str, object] = {}
    progress_updates: list[int] = []

    class Progress:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def update(self, size: int) -> None:
            progress_updates.append(size)

    def fake_tqdm(**kwargs: object) -> Progress:
        progress_options.update(kwargs)
        return Progress()

    monkeypatch.setattr(nhn.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(nhn, "tqdm", fake_tqdm)
    destination = tmp_path / nhn.NHN_ARCHIVE_NAME

    result = nhn.download_file("https://example.test/nhn.zip", destination)

    assert result.read_bytes() == b"abcdef"
    assert progress_options["total"] == 6
    assert progress_options["initial"] == 0
    assert progress_options["unit"] == "B"
    assert progress_updates == [3, 3]


def test_acquisition_reuses_existing_gpkg_before_archive_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "nhn"
    raw_dir.mkdir()
    gpkg_path = raw_dir / nhn.NHN_GPKG_NAME
    xml_path = raw_dir / nhn.NHN_XML_NAME
    write_test_gpkg(gpkg_path)
    write_capabilities_xml(xml_path)
    calls: list[str] = []

    def unexpected_download(
        url: str,
        destination: Path,
        *,
        overwrite: bool = False,
        max_retries: int = nhn.MAX_RETRIES,
    ) -> Path:
        calls.append(url)
        return destination

    monkeypatch.setattr(nhn, "download_file", unexpected_download)

    result = nhn.acquire_nhn(raw_nhn=raw_dir)

    assert result["gpkg"] == gpkg_path
    assert result["metadata_xml"] == xml_path
    assert result["archive"] is None
    assert result["manifest"].is_file()
    assert calls == [nhn.NHN_WMS_CAPABILITIES_URL]


def test_acquisition_deletes_archive_after_successful_validation(
    tmp_path: Path,
) -> None:
    raw_dir = tmp_path / "nhn"
    raw_dir.mkdir()
    gpkg_path = raw_dir / nhn.NHN_GPKG_NAME
    archive_path = raw_dir / nhn.NHN_ARCHIVE_NAME
    xml_path = raw_dir / nhn.NHN_XML_NAME
    write_test_gpkg(gpkg_path)
    write_capabilities_xml(xml_path)
    archive_path.write_bytes(b"redundant compressed source")

    result = nhn.acquire_nhn(raw_nhn=raw_dir)

    assert result["archive"] is None
    assert not archive_path.exists()
    assert result["manifest"].is_file()


def test_acquisition_can_keep_archive_after_validation(tmp_path: Path) -> None:
    raw_dir = tmp_path / "nhn"
    raw_dir.mkdir()
    gpkg_path = raw_dir / nhn.NHN_GPKG_NAME
    archive_path = raw_dir / nhn.NHN_ARCHIVE_NAME
    xml_path = raw_dir / nhn.NHN_XML_NAME
    write_test_gpkg(gpkg_path)
    write_capabilities_xml(xml_path)
    archive_path.write_bytes(b"retained compressed source")

    result = nhn.acquire_nhn(raw_nhn=raw_dir, keep_archive=True)

    assert result["archive"] == archive_path
    assert archive_path.exists()
    assert result["manifest"].is_file()


def test_bronze_dispatches_nhn_with_output_override(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    module = ModuleType("test_nhn_stage")
    setattr(
        module,
        "acquire_nhn",
        lambda **kwargs: calls.append(kwargs) or "complete",
    )
    options = BronzeStageOptions(
        overwrite=True,
        raw_nhn_dir=tmp_path,
        keep_nhn_archive=True,
    )

    result = execute_bronze_stage("nhn", module, options)

    assert "nhn" in BRONZE_STAGE_ORDER
    assert result == "complete"
    assert calls == [
        {
            "overwrite": True,
            "keep_archive": True,
            "raw_nhn": tmp_path,
        }
    ]
