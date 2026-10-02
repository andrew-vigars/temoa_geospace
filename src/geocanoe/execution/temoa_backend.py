"""Narrow integration boundary for the installed TEMOA v4 package."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from temoa import TemoaConfig, TemoaSequencer
from temoa.cli import setup_logging as setup_temoa_logging


def distribution_record() -> dict[str, Any]:
    """Return reproducible provenance for the installed TEMOA distribution."""

    try:
        installed = distribution("temoa")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "TEMOA v4 is not installed. Install GeoCANOE from pyproject.toml "
            "with `python -m pip install -e .`."
        ) from exc

    record: dict[str, Any] = {
        "distribution": "temoa",
        "version": installed.version,
        "source_url": None,
        "editable": False,
        "vcs": None,
        "requested_revision": None,
        "commit": None,
    }

    direct_url_text = installed.read_text("direct_url.json")
    if direct_url_text is None:
        return record

    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return record

    vcs_info = direct_url.get("vcs_info", {})
    dir_info = direct_url.get("dir_info", {})
    record.update(
        {
            "source_url": direct_url.get("url"),
            "editable": dir_info.get("editable", False),
            "vcs": vcs_info.get("vcs"),
            "requested_revision": vcs_info.get("requested_revision"),
            "commit": vcs_info.get("commit_id"),
        }
    )
    return record


def run_temoa(
    config_path: Path,
    output_path: Path,
    *,
    silent: bool,
) -> None:
    """Build and solve a model through TEMOA v4's importable Python API.

    TEMOA's CLI normally installs its console and file logging handlers before
    constructing the model. Imported execution must do that explicitly so each
    GeoCANOE run retains TEMOA's canonical ``temoa-run.log`` artifact.
    """

    setup_temoa_logging(output_path, silent=silent)

    config = TemoaConfig.build_config(
        config_file=config_path,
        output_path=output_path,
        silent=silent,
    )

    if not silent:
        print(config)
        confirmation = input("\nContinue with this TEMOA configuration? [y/N]: ").strip()
        if confirmation.casefold() not in {"y", "yes"}:
            raise RuntimeError("TEMOA run cancelled by user.")

    sequencer = TemoaSequencer(config=config)
    sequencer.start()
