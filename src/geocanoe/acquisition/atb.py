"""Acquire the full official ATB CSV, independently of CANOE and Atlite.

Bronze preserves raw bytes. Technology filtering, currency conversion, and
mapping to Atlite physical configurations belong downstream.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import tomllib

import requests
from tqdm.auto import tqdm

from geocanoe.acquisition._download import download_resumable
from geocanoe.acquisition.era5_renewables import acquisition_lock, manifest, sha256
from geocanoe.paths import find_project_root


def validate_csv(path):
    """Reject error pages and truncated/incorrect ATB tables without filtering rows."""
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        columns = next(reader, [])
        required = {
            "display_name",
            "core_metric_parameter",
            "core_metric_variable",
            "value",
        }
        if not required.issubset(columns):
            raise ValueError("Downloaded file is not the expected ATB summary CSV.")
        count = 0
        for row in reader:
            if len(row) != len(columns):
                raise ValueError(f"Malformed ATB CSV row {count + 2}.")
            count += 1
        if not count:
            raise ValueError("Empty ATB dataset.")
    return {"columns": columns, "rows": count}


def acquire_atb(config_path=None):
    """Prefer compatible CANOE_CLOUD Silver inputs, with a pinned official fallback."""
    root = find_project_root()
    config_path = (
        Path(config_path) if config_path else root / "config/atb/electricity_2025.toml"
    )
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    selected = None
    if "cloud" in config:
        from geocanoe.acquisition.canoe_cloud import import_atb_cache

        selected = import_atb_cache(
            root / config["cloud"]["config"], required_edition=config["edition"]
        )
    if selected:
        path, record = selected
        provider = "canoe_cloud"
    else:
        path = acquire_official_atb(config_path)
        record = path.parent / "acquisition_manifest.json"
        provider = "official_atb"
    manifest(
        root / "data_files/raw/atb/selected_source.json",
        {
            "provider": provider,
            "source": str(path.resolve()),
            "manifest": str(record.resolve()),
            "source_sha256": sha256(path),
            "edition": config["edition"],
        },
    )
    return path


def acquire_official_atb(config_path=None):
    """Download/verify the pinned full dataset; never overwrite a verified release."""
    root = find_project_root()
    config_path = (
        Path(config_path) if config_path else root / "config/atb/electricity_2025.toml"
    )
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    raw = root / config["raw_dir"]
    filename = config["filename"]
    if Path(filename).name != filename:
        raise ValueError("ATB filename must be a basename.")
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / filename
    record = raw / "acquisition_manifest.json"
    print(
        f"ATB: edition {config['edition']}, revision {config['revision']}; full source -> {path}",
        flush=True,
    )
    with acquisition_lock(raw):
        if path.exists():
            if not record.is_file():
                raise ValueError(
                    f"Unmanifested ATB source; inspect before adoption: {path}"
                )
            cached = json.loads(record.read_text(encoding="utf-8"))
            if (
                cached["url"] != config["url"]
                or cached["edition"] != config["edition"]
                or cached["revision"] != config["revision"]
                or cached["sha256"] != sha256(path)
                or cached["bytes"] != path.stat().st_size
            ):
                raise ValueError(
                    "ATB cache identity/checksum mismatch; use a separate release directory."
                )
            print(
                f"ATB: verified cache ({path.stat().st_size / 1e6:.1f} MB); no HTTP calls.",
                flush=True,
            )
            return path
        # Stage the download separately so failed content validation cannot become a cache.
        staged = raw / (filename + ".download")
        download_resumable(
            config["url"],
            staged,
            headers={"User-Agent": "GeoCANOE-ATB-acquisition"},
            timeout=config.get("timeout_seconds", 60),
            max_retries=config.get("max_attempts", 3),
            retry_delay=config.get("retry_delay_seconds", 5),
            chunk_size=1024 * 1024,
            request_get=requests.get,
            progress_factory=tqdm,
        )
        if (
            config.get("expected_bytes") is not None
            and staged.stat().st_size != config["expected_bytes"]
        ):
            raise ValueError(
                "ATB source size differs from the pinned release; inspect upstream revision."
            )
        print("ATB: validating CSV structure and calculating SHA-256...", flush=True)
        structure = validate_csv(staged)
        digest = sha256(staged)
        staged.replace(path)
        manifest(
            record,
            {
                "edition": config["edition"],
                "revision": config["revision"],
                "url": config["url"],
                "source_page": config["source_page"],
                "sha256": digest,
                "bytes": path.stat().st_size,
                "config_sha256": sha256(config_path),
                "structure": structure,
                "scope": "complete unfiltered official summary CSV",
            },
        )
    print(
        f"ATB: complete; preserved {structure['rows']:,} rows. No CANOE/Atlite imports or transformations.",
        flush=True,
    )
    return path
