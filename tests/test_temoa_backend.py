"""Contracts for the installed TEMOA v4 integration boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from geocanoe.execution import temoa_backend


def test_distribution_record_captures_vcs_provenance(monkeypatch) -> None:
    direct_url = {
        "url": "https://github.com/andrew-vigars/temoa.git",
        "vcs_info": {
            "vcs": "git",
            "requested_revision": "29f98f9f",
            "commit_id": "29f98f9f67f4645ff35f6a176933d86b2963e485",
        },
    }
    fake_distribution = SimpleNamespace(
        version="4.0.0a2",
        read_text=lambda name: json.dumps(direct_url)
        if name == "direct_url.json"
        else None,
    )
    monkeypatch.setattr(
        temoa_backend,
        "distribution",
        lambda name: fake_distribution,
    )

    assert temoa_backend.distribution_record() == {
        "distribution": "temoa",
        "version": "4.0.0a2",
        "source_url": "https://github.com/andrew-vigars/temoa.git",
        "editable": False,
        "vcs": "git",
        "requested_revision": "29f98f9f",
        "commit": "29f98f9f67f4645ff35f6a176933d86b2963e485",
    }


def test_distribution_record_captures_local_editable_source(monkeypatch) -> None:
    direct_url = {
        "url": "file:///C:/repos/temoa-v4",
        "dir_info": {"editable": True},
    }
    fake_distribution = SimpleNamespace(
        version="4.0.0a2",
        read_text=lambda name: json.dumps(direct_url)
        if name == "direct_url.json"
        else None,
    )
    monkeypatch.setattr(temoa_backend, "distribution", lambda name: fake_distribution)

    record = temoa_backend.distribution_record()

    assert record["source_url"] == "file:///C:/repos/temoa-v4"
    assert record["editable"] is True


def test_run_temoa_builds_config_and_starts_sequencer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    config = object()

    def setup_logging(output_path, *, silent):
        calls["logging"] = {
            "output_path": output_path,
            "silent": silent,
        }

    def build_config(**kwargs):
        calls["build"] = kwargs
        return config

    class FakeSequencer:
        def __init__(self, *, config):
            calls["sequencer_config"] = config

        def start(self) -> None:
            calls["started"] = True

    monkeypatch.setattr(temoa_backend.TemoaConfig, "build_config", build_config)
    monkeypatch.setattr(temoa_backend, "TemoaSequencer", FakeSequencer)
    monkeypatch.setattr(temoa_backend, "setup_temoa_logging", setup_logging)

    config_path = tmp_path / "scenario.toml"
    output_path = tmp_path / "outputs"
    temoa_backend.run_temoa(config_path, output_path, silent=True)

    assert calls == {
        "logging": {
            "output_path": output_path,
            "silent": True,
        },
        "build": {
            "config_file": config_path,
            "output_path": output_path,
            "silent": True,
        },
        "sequencer_config": config,
        "started": True,
    }
