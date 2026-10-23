"""Bronze immutability and offline economic table preparation."""

import hashlib
import io
import zipfile

import pandas as pd
import shutil

import pytest

from geocanoe.acquisition import economics as bronze
from geocanoe.preprocessing.economics import prepare_economics


@pytest.fixture
def economic_project(tmp_path, monkeypatch):
    root = bronze.find_project_root()
    for relative in ("registry/economics.toml", "config/economics/sources.toml"):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, dest)
    shutil.copytree(root / "registry/economics", tmp_path / "registry/economics")
    # A synthetic full-table ZIP keeps the tests independent of local raw caches.
    frame = pd.read_csv(root / "registry/economics/canada_gdp_implicit_price_index.csv")
    frame = frame.rename(columns={"year": "REF_DATE", "index": "VALUE"})
    frame["GEO"] = "Canada"
    frame["Index"] = "Implicit price indexes"
    frame["Estimates"] = "Gross domestic product at market prices"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("36100223.csv", frame.to_csv(index=False))
    payload = buffer.getvalue()
    config = tmp_path / "config/economics/sources.toml"
    config.write_text(config.read_text().replace(
        "c0df3ea6f48cfd9b6f5bca9034076ae1d45300ebcc0e3ee19959b15d7e640ec2",
        hashlib.sha256(payload).hexdigest(),
    ))
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield payload

    monkeypatch.setattr(bronze, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(bronze.requests, "get", lambda *a, **k: Response())
    return tmp_path


def test_bronze_cache_is_offline_and_preparation_preserves_index_values(economic_project, monkeypatch):
    root = economic_project
    raw = bronze.acquire_economics()
    before = (root / "registry/economics/canada_gdp_implicit_price_index.csv").read_bytes()
    def no_network(*args, **kwargs):
        pytest.fail("Verified economic cache must not access network")
    monkeypatch.setattr(bronze.requests, "get", no_network)
    assert bronze.acquire_economics() == raw
    index, exchange = prepare_economics()
    assert index.read_bytes() == before
    assert exchange.read_bytes() == (raw / "currency_exchange.csv").read_bytes()
    bronze.validate_source(raw / "36100223-eng.zip", bronze.load_settings()[2]["sources"][0])


def test_bronze_rejects_tampering(economic_project):
    raw = bronze.acquire_economics()
    (raw / "currency_exchange.csv").write_text("corrupted")
    with pytest.raises(ValueError, match="mismatch"):
        bronze.acquire_economics()
    with pytest.raises(ValueError, match="mismatch"):
        prepare_economics()


def test_economics_registered_in_bronze_workflow():
    from geocanoe.execution.bronze import BRONZE_STAGE_ORDER, STAGE_MODULE_NAMES
    assert "economics" in BRONZE_STAGE_ORDER
    assert STAGE_MODULE_NAMES["economics"] == "geocanoe.acquisition.economics"
