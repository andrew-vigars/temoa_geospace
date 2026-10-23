"""Shared source-year FX, then Canadian GDP implicit-price-index conversion.

Source price years describe purchasing power, not technology projection years.
All paths are relative to the project root. No network access occurs at build time.
"""

import csv
import hashlib
import json
import math
from pathlib import Path
import tomllib

from geocanoe.paths import find_project_root


def conversion_basis(source_currency, source_year, source_scale=1.0, *, config=None):
    """Return a checked multiplier and auditable economic inputs for one cost."""
    root = find_project_root()
    path = Path(config) if config else root / "registry/economics.toml"
    if not path.is_absolute():
        path = root / path
    policy = tomllib.loads(path.read_text(encoding="utf-8"))
    if policy["method"] != "source_year_fx_then_canadian_gdp_index":
        raise ValueError("Unsupported economic conversion method")
    if policy["target_currency"] != "CAD":
        raise ValueError("Canadian GDP conversion requires a CAD target")
    index_path = root / policy["index_path"]
    provenance_path = root / policy["index_provenance"]
    digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if digest != provenance["subset_sha256"]:
        raise ValueError("GDP index checksum mismatch")
    with index_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    indexes = {int(row["year"]): float(row["index"]) for row in rows}
    expected = set(range(policy["first_year"], policy["last_year"] + 1))
    if len(rows) != len(indexes) or set(indexes) != expected:
        raise ValueError("GDP index must have exactly one observation per configured year")
    target_year = policy["target_year"]
    if source_year not in indexes or target_year not in indexes:
        raise ValueError("Missing source or target price year in GDP index")
    exchange_path = root / policy["exchange_path"]
    exchange_digest = hashlib.sha256(exchange_path.read_bytes()).hexdigest()
    exchange_provenance = json.loads(
        (root / policy["exchange_provenance"]).read_text(encoding="utf-8")
    )
    if exchange_digest != exchange_provenance["sha256"]:
        raise ValueError("Exchange table checksum mismatch")
    with exchange_path.open(encoding="utf-8-sig", newline="") as stream:
        exchange_rows = list(csv.DictReader(stream))
    exchange = {int(row["Year"]): row for row in exchange_rows}
    if len(exchange_rows) != len(exchange) or set(exchange) != expected:
        raise ValueError("Exchange table must have one observation per configured year")
    if source_currency not in exchange[source_year]:
        raise ValueError(f"No configured FX rate for {source_currency}")
    fx = float(exchange[source_year][source_currency])
    for row in exchange_rows:
        rates = [float(value) for key, value in row.items() if key != "Year"]
        if not all(math.isfinite(rate) and rate > 0 for rate in rates):
            raise ValueError("Exchange rates must be finite and positive")
        if float(row["CAD"]) != 1.0:
            raise ValueError("Exchange table must be expressed in CAD per source currency")
    factors = [fx, source_scale, policy["target_scale"], *indexes.values()]
    if not all(math.isfinite(v) and v > 0 for v in factors):
        raise ValueError("Economic indexes, rates and scales must be finite and positive")
    basis = {
        "source_currency": source_currency, "source_year": source_year,
        "source_scale": source_scale, "target_currency": f"CAD{target_year}",
        "target_scale": policy["target_scale"], "cad_per_source_currency": fx,
        "source_price_index": indexes[source_year],
        "target_price_index": indexes[target_year], "policy": policy,
        "index_sha256": digest, "index_provenance": provenance,
        "exchange_sha256": exchange_digest,
        "exchange_provenance": exchange_provenance,
    }
    return {
        "target_currency": basis["target_currency"],
        "multiplier": fx * indexes[target_year] / indexes[source_year]
        * source_scale / policy["target_scale"],
        "basis": basis,
        "basis_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def validate_preconverted_source(source, currency, year, *, config=None):
    """Reject relabelling a source whose upstream conversion is already embedded."""
    root = find_project_root()
    path = Path(config) if config else root / "registry/economics.toml"
    if not path.is_absolute():
        path = root / path
    policy = tomllib.loads(path.read_text(encoding="utf-8"))
    basis = policy["sources"][source]
    if not basis["already_converted"] or (currency, year) != (
        basis["currency"], basis["price_year"]
    ):
        raise ValueError("Preconverted source cannot be relabelled; upstream costs are already CAD2020")
    return basis
