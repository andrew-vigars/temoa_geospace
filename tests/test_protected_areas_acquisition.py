import json
import stat
import zipfile

import pytest

from geocanoe.acquisition import protected_areas as cpcad


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\escape"])
def test_archive_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, b"bad")
    if "\\" in name:
        # Windows' ZIP writer normalizes separators; emulate an external ZIP.
        archive.write_bytes(archive.read_bytes().replace(b"a/escape", b"a\\escape"))
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        cpcad.extract_archive(archive, tmp_path / "source")
    assert not (tmp_path / "source").exists()


def test_archive_rejects_symlink(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(info, b"target")
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        cpcad.extract_archive(archive, tmp_path / "source")


def test_capture_verifies_extracted_bytes_and_reuses_offline(tmp_path, monkeypatch):
    archive = tmp_path / "ProtectedConservedArea_2025.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("ProtectedConservedArea_2025.gdb/table", b"original")
    source = cpcad.source_record()
    (tmp_path / "catalogue.json").write_text(
        json.dumps({"success": True, "result": {"id": source["record_id"]}})
    )
    monkeypatch.setattr(cpcad, "download_file", lambda url, path: path)
    monkeypatch.setattr(
        cpcad,
        "inspect_source",
        lambda directory: (
            directory / "ProtectedConservedArea_2025.gdb",
            {"fixture": True},
        ),
    )
    cpcad.acquire_protected_areas(tmp_path, preview=False)
    record = (tmp_path / "acquisition_manifest.json").read_bytes()

    def no_network(*args):
        raise AssertionError("Cache must not access the network")

    monkeypatch.setattr(cpcad, "download_file", no_network)
    cpcad.acquire_protected_areas(tmp_path, preview=False)
    assert (tmp_path / "acquisition_manifest.json").read_bytes() == record
    (tmp_path / "source/ProtectedConservedArea_2025.gdb/table").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        cpcad.acquire_protected_areas(tmp_path, preview=False)


def test_capture_rejects_incorrect_catalogue(tmp_path, monkeypatch):
    (tmp_path / "catalogue.json").write_text(
        '{"success": true, "result": {"id": "wrong"}}'
    )
    calls = []
    monkeypatch.setattr(
        cpcad, "download_file", lambda url, path: calls.append(path.name)
    )
    with pytest.raises(ValueError, match="catalogue identity"):
        cpcad.acquire_protected_areas(tmp_path, preview=False)
    assert calls == ["catalogue.json"]
