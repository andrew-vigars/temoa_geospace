"""Explicitly refresh reviewable economic registry tables from verified Bronze."""

import argparse
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

import pandas as pd

from geocanoe.acquisition.economics import load_settings, validate_source
from geocanoe.acquisition.era5_renewables import manifest, sha256


def prepare_economics(config_path=None):
    """Filter GDP observations and publish checksummed tables; never download."""
    root, config_path, settings = load_settings(config_path)
    policy_path = root / settings["economic_config"]
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    sources = {source["id"]: source for source in settings["sources"]}
    verified = {}
    records = {}
    for key in ("canada_gdp", "exchange"):
        source = sources[key]
        path = root / settings["raw_dir"] / source["filename"]
        record_path = path.parent / f"{key}_manifest.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["source"] != source or record["sha256"] != sha256(path):
            raise ValueError("Economic Bronze manifest mismatch")
        validate_source(path, source)
        verified[key] = path
        records[key] = {"manifest": str(record_path.relative_to(root)),
                        "manifest_sha256": sha256(record_path), "source_sha256": sha256(path)}
    source = sources["canada_gdp"]
    with zipfile.ZipFile(verified["canada_gdp"]) as archive:
        with archive.open(source["member"]) as stream:
            frame = pd.read_csv(stream, low_memory=False)
    for column, value in source["selectors"].items():
        frame = frame.loc[frame[column] == value]
    frame = frame.loc[frame.REF_DATE.between(policy["first_year"], policy["last_year"])]
    expected = set(range(policy["first_year"], policy["last_year"] + 1))
    if len(frame) != len(expected) or set(frame.REF_DATE) != expected:
        raise ValueError("GDP selection must contain exactly one observation per year")
    if frame.VALUE.isna().any() or not (frame.VALUE > 0).all():
        raise ValueError("Missing or invalid GDP observations")
    exchange = pd.read_csv(verified["exchange"], encoding="utf-8-sig")
    if len(exchange) != len(expected) or set(exchange.Year) != expected:
        raise ValueError("Exchange years do not match economic policy")
    rates = exchange.drop(columns="Year")
    if rates.isna().any().any() or not ((rates > 0) & (rates < float("inf"))).all().all():
        raise ValueError("Invalid exchange observations")
    if not (exchange.CAD == 1).all():
        raise ValueError("Exchange rates must be denominated in CAD")
    index_path = root / policy["index_path"]
    index_path.parent.mkdir(parents=True, exist_ok=True)
    frame.sort_values("REF_DATE")[["REF_DATE", "VALUE", "VECTOR", "UOM", "STATUS"]].rename(
        columns={"REF_DATE": "year", "VALUE": "index"}
    ).to_csv(index_path, index=False)
    exchange_path = root / policy["exchange_path"]
    exchange_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(verified["exchange"], exchange_path)
    manifest(root / policy["index_provenance"], {
        "source": source.get("url", source.get("local_path")), "table": source["table"],
        "archive_sha256": sha256(verified["canada_gdp"]),
        "subset_sha256": sha256(index_path), "selectors": source["selectors"],
        "years": [policy["first_year"], policy["last_year"]],
        "bronze": records["canada_gdp"], "config_sha256": sha256(config_path),
    })
    manifest(root / policy["exchange_provenance"], {
        "source": sources["exchange"].get("url", sources["exchange"].get("local_path")),
        "sha256": sha256(exchange_path), "bronze": records["exchange"],
        "notes": sources["exchange"].get("notes", ""),
        "convention": "CAD per source currency, indexed by source price year",
    })
    return index_path, exchange_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    print(prepare_economics(args.config))


if __name__ == "__main__":
    main()
