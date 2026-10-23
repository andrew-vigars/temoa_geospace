"""Preserve pinned raw economic sources without filtering or cost conversion."""

import argparse
import csv
import io
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

import requests

from geocanoe.acquisition.era5_renewables import acquisition_lock, manifest, sha256
from geocanoe.paths import find_project_root


def load_settings(config_path=None):
    root = find_project_root()
    path = Path(config_path) if config_path else root / "config/economics/sources.toml"
    if not path.is_absolute():
        path = root / path
    return root, path, tomllib.loads(path.read_text(encoding="utf-8"))


def validate_source(path, source):
    """Check pinned bytes and basic structure, preserving the entire payload."""
    if sha256(path) != source["sha256"]:
        raise ValueError(f"Economic source checksum mismatch: {source['id']}")
    if source["kind"] == "statcan_zip":
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Corrupt economic ZIP")
            with archive.open(source["member"]) as stream:
                columns = next(csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig")))
        required = {"REF_DATE", "VALUE", *source["selectors"]}
    elif source["kind"] == "exchange_csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            columns = next(csv.reader(stream))
        required = {"Year", "CAD", "USD"}
    else:
        raise ValueError("Unsupported economic source kind")
    if not required.issubset(columns):
        raise ValueError("Economic source columns do not match configuration")


def acquire_economics(config_path=None):
    """Download or copy configured sources; verified caches require no network."""
    root, config_path, settings = load_settings(config_path)
    raw = root / settings["raw_dir"]
    raw.mkdir(parents=True, exist_ok=True)
    ids = [source["id"] for source in settings["sources"]]
    names = [source["filename"] for source in settings["sources"]]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise ValueError("Economic source IDs and filenames must be unique")
    with acquisition_lock(raw):
        for source in settings["sources"]:
            name = source["filename"]
            if Path(name).name != name:
                raise ValueError("Economic source filename must be a basename")
            if ("url" in source) == ("local_path" in source):
                raise ValueError("Choose exactly one economic URL or local source")
            path = raw / name
            record = raw / f"{source['id']}_manifest.json"
            if path.exists():
                if not record.exists():
                    raise ValueError("Unmanifested economic cache; inspect before adoption")
                cached = json.loads(record.read_text(encoding="utf-8"))
                if cached["source"] != source or cached["sha256"] != sha256(path):
                    raise ValueError("Economic cache identity mismatch; use a new release directory")
                validate_source(path, source)
                continue
            staged = path.with_suffix(path.suffix + ".download")
            if "local_path" in source:
                shutil.copyfile(root / source["local_path"], staged)
            else:
                with requests.get(source["url"], stream=True, timeout=60) as response:
                    response.raise_for_status()
                    with staged.open("wb") as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk)
            validate_source(staged, source)
            staged.replace(path)
            manifest(record, {
                "source": source, "sha256": sha256(path),
                "bytes": path.stat().st_size, "config_sha256": sha256(config_path),
                "scope": "unmodified complete source; no cost conversion",
            })
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    print(acquire_economics(args.config))


if __name__ == "__main__":
    main()
