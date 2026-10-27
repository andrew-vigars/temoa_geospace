import json
import zipfile

import pytest

from geocanoe.acquisition import wind_turbines as wind


def test_capture_and_offline_cache_integrity(tmp_path, monkeypatch):
    source = wind.source_record()
    with zipfile.ZipFile(tmp_path / "wind_turbine_database_en.gdb.zip", "w") as stream:
        stream.writestr("wind_turbine_database_en.gdb/table", b"points")
    (tmp_path / "catalogue.json").write_text(
        json.dumps(
            {
                "success": True,
                "result": {
                    "id": source["record_id"],
                    "resources": [{"url": source["distribution_url"]}],
                },
            }
        )
    )
    monkeypatch.setattr(wind, "download_file", lambda url, path: path)
    monkeypatch.setattr(
        wind,
        "inspect_source",
        lambda directory: (
            directory / "wind_turbine_database_en.gdb",
            {"fixture": True},
        ),
    )
    wind.acquire_wind_turbines(tmp_path, preview=False)
    record = (tmp_path / "acquisition_manifest.json").read_bytes()

    def no_network(*args):
        raise AssertionError("Cache must be offline")

    monkeypatch.setattr(wind, "download_file", no_network)
    wind.acquire_wind_turbines(tmp_path, preview=False)
    assert (tmp_path / "acquisition_manifest.json").read_bytes() == record
    (tmp_path / "source/wind_turbine_database_en.gdb/table").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        wind.acquire_wind_turbines(tmp_path, preview=False)


def test_reject_wrong_distribution_before_zip_download(tmp_path, monkeypatch):
    source = wind.source_record()
    (tmp_path / "catalogue.json").write_text(
        json.dumps(
            {
                "success": True,
                "result": {
                    "id": source["record_id"],
                    "resources": [{"url": "https://example.invalid/wrong"}],
                },
            }
        )
    )
    calls = []
    monkeypatch.setattr(
        wind, "download_file", lambda url, path: calls.append(path.name)
    )
    with pytest.raises(ValueError, match="absent from catalogue"):
        wind.acquire_wind_turbines(tmp_path, preview=False)
    assert calls == ["catalogue.json"]
