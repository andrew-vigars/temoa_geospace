"""Select ATB technology families without assuming model financial/physical cases."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import tomllib

from tqdm.auto import tqdm

from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.paths import find_project_root

SELECTION_COLUMNS = (
    "technology",
    "display_name",
    "scenario",
    "core_metric_case",
    "tax_credit_case",
    "crpyears",
    "core_metric_parameter",
    "core_metric_variable",
    "units",
)


def build_atb_subset(config_path=None):
    """Stream the verified master; preserve original columns and exact selected rows."""
    root = find_project_root()
    config_path = (
        Path(config_path)
        if config_path
        else root / "config/atb/renewable_families_2025.toml"
    )
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if "source_selection" in config:
        selection = json.loads(
            (root / config["source_selection"]).read_text(encoding="utf-8")
        )
        source = Path(selection["source"])
        source_manifest = Path(selection["manifest"])
    else:
        source = root / config["source"]
        source_manifest = root / config["manifest"]
    record = json.loads(source_manifest.read_text(encoding="utf-8"))
    digest = sha256(source)
    if record["sha256"] != digest:
        raise ValueError("ATB Bronze checksum mismatch.")
    technologies = config["technologies"]
    if not technologies or len(set(technologies)) != len(technologies):
        raise ValueError("Technology selection must be nonempty and unique.")
    out = root / config["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    target = out / "technology_families.csv"
    temporary = target.with_suffix(".csv.part")
    counts = Counter()
    dimensions = {
        tech: {column: set() for column in SELECTION_COLUMNS[1:]}
        for tech in technologies
    }
    print(
        f"ATB subset: selecting {technologies}; retaining all original columns.",
        flush=True,
    )
    try:
        with (
            source.open(encoding="utf-8-sig", newline="") as src,
            temporary.open("w", encoding="utf-8", newline="") as dst,
        ):
            if source.suffix == ".parquet":
                import pyarrow.parquet as pq

                parquet = pq.ParquetFile(source)
                columns = parquet.schema_arrow.names
                reader = (
                    row for batch in parquet.iter_batches() for row in batch.to_pylist()
                )
            else:
                reader = csv.DictReader(src)
                columns = reader.fieldnames
            if not set(SELECTION_COLUMNS).issubset(columns or []):
                raise ValueError("ATB master is missing required selector columns.")
            writer = csv.DictWriter(dst, fieldnames=columns)
            writer.writeheader()
            for row in tqdm(
                reader,
                total=record.get("structure", {}).get("rows"),
                desc="ATB: selecting technology families",
                unit="row",
            ):
                tech = row["technology"]
                if tech in dimensions:
                    writer.writerow(row)
                    counts[tech] += 1
                    for column, values in dimensions[tech].items():
                        values.add(row[column])
        missing = set(technologies) - counts.keys()
        if missing:
            raise ValueError(
                f"Technology labels absent from ATB master: {sorted(missing)}"
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    manifest(
        out / "selection_manifest.json",
        {
            "source": str(source),
            "source_sha256": digest,
            "source_manifest_sha256": sha256(source_manifest),
            "edition": record["edition"],
            "revision": record["revision"],
            "config_sha256": sha256(config_path),
            "output_sha256": sha256(target),
            "selection_column": "technology",
            "selection": technologies,
            "rows_by_technology": dict(counts),
            "preserved_columns": columns,
            "available_dimensions": {
                tech: {k: sorted(v) for k, v in fields.items()}
                for tech, fields in dimensions.items()
            },
            "model_ready": False,
            "note": "Family subset only; no financial-case, resource-class, currency or physical-profile mapping applied.",
        },
    )
    print(f"ATB subset: saved {sum(counts.values()):,} rows to {target}", flush=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    build_atb_subset(parser.parse_args().config)
