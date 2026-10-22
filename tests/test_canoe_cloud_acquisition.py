import json

import pandas as pd
import pytest

from geocanoe.acquisition import canoe_cloud
from geocanoe.preprocessing.atb import SELECTION_COLUMNS
from geocanoe.preprocessing.atb import build_atb_subset


def test_import_pins_actual_edition_and_original_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr(canoe_cloud, "find_project_root", lambda: tmp_path)
    config = tmp_path / "cloud.toml"
    config.write_text(
        'cache_dir="cache"\nraw_dir="raw"\nconnector_reference_commit="fixture"\ntarget_date="2026-10-01"\n'
    )
    assert canoe_cloud.import_atb_cache(config) is None
    folder = tmp_path / "cache/silver/2026-10-01"
    folder.mkdir(parents=True)
    source = folder / "atb_2026-10-01.parquet"
    row = dict.fromkeys(SELECTION_COLUMNS, "*")
    row.update(atb_year="2024", value="1")
    pd.DataFrame([row]).to_parquet(source)
    assert canoe_cloud.import_atb_cache(config) is None
    row["atb_year"] = "2025"
    pd.DataFrame([row]).to_parquet(source)
    imported, record = canoe_cloud.import_atb_cache(config)
    assert imported.read_bytes() == source.read_bytes()
    metadata = json.loads(record.read_text())
    assert metadata["edition"] == 2025
    assert metadata["snapshot_date"] == "2026-10-01"
    assert canoe_cloud.import_atb_cache(config) == (imported, record)
    selection = tmp_path / "selected.json"
    selection.write_text(json.dumps({"source": str(imported), "manifest": str(record)}))
    subset_config = tmp_path / "subset.toml"
    subset_config.write_text(
        f'source_selection="{selection.as_posix()}"\noutput_dir="{tmp_path.as_posix()}/subset"\ntechnologies=["*"]\n'
    )
    subset = build_atb_subset(subset_config)
    assert len(pd.read_csv(subset)) == 1
    imported.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        canoe_cloud.import_atb_cache(config)
