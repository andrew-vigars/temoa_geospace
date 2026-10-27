import hashlib
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geocanoe.acquisition import solar_facilities as solar


def fixture_source(root):
    data = gpd.GeoDataFrame(
        {
            "cluster_id": [1, 2],
            "capacity_mw": [10.0, 20.0],
            "constructed_before": ["2020-01-01", "2021-01-01"],
            "constructed_after": [None, None],
        },
        geometry=[box(-80, 43, -79.99, 43.01), box(10, 45, 10.01, 45.01)],
        crs=4326,
    )
    data.to_file(root / solar.GPKG, layer="assets", driver="GPKG")
    table = pd.DataFrame(data.drop(columns="geometry"))
    table["country"] = ["Canada", "Italy"]
    table["latitude"] = [43.005, 45.005]
    table["longitude"] = [-79.995, 10.005]
    table.to_csv(root / solar.CSV, index=False)
    pd.DataFrame(
        {
            "cluster_id": [1, 2],
            "source_id": [10, 20],
            "source": ["osm", "solar_asset_mapper"],
            "source_date": ["00:00.0", "00:00.0"],
        }
    ).to_csv(root / solar.SOURCES, index=False)
    (root / solar.PDF).write_bytes(b"%PDF-fixture")
    files = []
    for name in (solar.GPKG, solar.CSV, solar.SOURCES):
        payload = (root / name).read_bytes()
        files.append(
            {
                "key": name,
                "size": len(payload),
                "checksum": "md5:" + hashlib.md5(payload).hexdigest(),
            }
        )
    metadata = {"id": 11368204, "files": files}
    (root / "zenodo_record.json").write_text(json.dumps(metadata))
    return metadata


def test_capture_join_preview_and_cache(tmp_path, monkeypatch):
    fixture_source(tmp_path)
    monkeypatch.setattr(solar, "download_file", lambda url, path: path)
    solar.acquire_solar_facilities(tmp_path)
    report = json.loads((tmp_path / "acquisition_manifest.json").read_text())
    assert report["inventory"]["global_features"] == 2
    assert report["inventory"]["canada_features"] == 1
    assert (tmp_path / "preview/solar_canada.png").is_file()
    solar.acquire_solar_facilities(tmp_path, preview=False)
    with (tmp_path / solar.SOURCES).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        solar.acquire_solar_facilities(tmp_path, preview=False)


def test_published_checksum_rejects_modified_payload(tmp_path):
    metadata = fixture_source(tmp_path)
    with (tmp_path / solar.CSV).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum/size mismatch"):
        solar.verify_published_files(tmp_path, metadata)


def test_reject_unmatched_lineage(tmp_path):
    fixture_source(tmp_path)
    data = pd.read_csv(tmp_path / solar.SOURCES)
    data.loc[0, "cluster_id"] = 999
    data.to_csv(tmp_path / solar.SOURCES, index=False)
    with pytest.raises(ValueError, match="IDs do not align"):
        solar.inspect_source(tmp_path)
