import pytest

from geocanoe.acquisition import atb
from geocanoe.execution.bronze import BronzeStageOptions, execute_bronze_stage


def test_full_download_cache_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(atb, "find_project_root", lambda: tmp_path)
    config = tmp_path / "atb.toml"
    config.write_text(
        f'edition=2025\nrevision="v1.0.0"\nraw_dir="{tmp_path.as_posix()}/raw"\nfilename="ATBe.csv"\nurl="https://example.test/ATBe.csv"\nsource_page="https://example.test"\n'
    )
    calls = []

    def download(url, destination, **kwargs):
        calls.append(url)
        assert kwargs["max_retries"] == 3
        destination.write_text(
            "display_name,core_metric_parameter,core_metric_variable,value\nSolar,OCC,2030,1000\nWind,OCC,2030,1500\nCoal,OCC,2030,2000\n"
        )

    monkeypatch.setattr(atb, "download_resumable", download)
    path = execute_bronze_stage("atb", atb, BronzeStageOptions(atb_config=config))
    assert "Coal" in path.read_text()  # Bronze preserves all technologies.
    assert atb.acquire_atb(config) == path
    assert len(calls) == 1
    path.write_text("corrupt")
    with pytest.raises(ValueError, match="mismatch"):
        atb.acquire_atb(config)
    assert len(calls) == 1


def test_invalid_content_never_promoted(tmp_path, monkeypatch):
    monkeypatch.setattr(atb, "find_project_root", lambda: tmp_path)
    config = tmp_path / "atb.toml"
    config.write_text(
        f'edition=2025\nrevision="v1.0.0"\nraw_dir="{tmp_path.as_posix()}/raw"\nfilename="ATBe.csv"\nurl="https://example.test"\nsource_page="https://example.test"\n'
    )
    monkeypatch.setattr(
        atb,
        "download_resumable",
        lambda url, destination, **kwargs: destination.write_text("<html>error</html>"),
    )
    with pytest.raises(ValueError, match="not the expected"):
        atb.acquire_atb(config)
    assert not (tmp_path / "raw/ATBe.csv").exists()
    assert not (tmp_path / "raw/.acquisition.lock").exists()
