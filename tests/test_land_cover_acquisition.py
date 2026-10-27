import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from geocanoe.acquisition import land_cover


def make_raster(path, resolution=30):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=3,
        count=1,
        dtype="uint8",
        crs="EPSG:3979",
        nodata=0,
        transform=from_origin(0, 90, resolution, resolution),
    ) as dst:
        dst.write(
            np.array([[0, 1, 1, 2], [1, 1, 2, 2], [2, 2, 1, 1]], dtype="uint8"), 1
        )
        dst.write_colormap(
            1, {0: (0, 0, 0, 0), 1: (0, 60, 0, 255), 2: (147, 155, 111, 255)}
        )


def test_capture_cache_integrity_and_preview(tmp_path, monkeypatch):
    source = land_cover.source_record()
    make_raster(tmp_path / "landcover-2020-classification.tif")
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
    monkeypatch.setattr(land_cover, "download_file", lambda url, path: path)
    land_cover.acquire_land_cover(tmp_path)
    record = (tmp_path / "acquisition_manifest.json").read_bytes()
    preview = json.loads((tmp_path / "preview/preview_manifest.json").read_text())
    assert preview["sampled_class_codes"] == [1, 2]
    assert (tmp_path / "preview/land_cover_2020.png").is_file()
    land_cover.acquire_land_cover(tmp_path, preview=False)
    assert (tmp_path / "acquisition_manifest.json").read_bytes() == record
    with (tmp_path / "catalogue.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        land_cover.acquire_land_cover(tmp_path)


def test_reject_wrong_catalogue_before_tiff_download(tmp_path, monkeypatch):
    (tmp_path / "catalogue.json").write_text(
        '{"success": true, "result": {"id": "wrong"}}'
    )
    calls = []
    monkeypatch.setattr(
        land_cover, "download_file", lambda url, path: calls.append(path.name)
    )
    with pytest.raises(ValueError, match="catalogue identity"):
        land_cover.acquire_land_cover(tmp_path)
    assert calls == ["catalogue.json"]


def test_reject_wrong_pixel_resolution(tmp_path):
    path = tmp_path / "wrong.tif"
    make_raster(path, resolution=100)
    with pytest.raises(ValueError, match="30 m"):
        land_cover.inspect_raster(path)
