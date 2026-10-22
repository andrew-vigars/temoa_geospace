"""Import compatible CANOE_CLOUD ATB tables without running upstream models."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import tomllib
import zipfile

from tqdm.auto import tqdm

from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.paths import find_project_root


def load_config(path=None):
    root = find_project_root()
    path = Path(path) if path else root / "config/canoe_cloud/sources.toml"
    return root, path, tomllib.loads(path.read_text(encoding="utf-8"))


def import_atb_cache(config_path=None, *, required_edition=2025):
    """Return an imported source and manifest, or None with an explicit reason."""
    root, config_path, config = load_config(config_path)
    cache = root / config["cache_dir"] / "silver"
    if not cache.is_dir():
        print(
            f"CANOE_CLOUD: no Silver cache at {cache}; use official ATB fallback.",
            flush=True,
        )
        return None
    date = config.get("target_date")
    dated = sorted(
        p
        for p in cache.iterdir()
        if p.is_dir()
        and len(p.name) == 10
        and p.name[4:5] == "-"
        and p.name[7:8] == "-"
    )
    selected = cache / date if date else (dated[-1] if dated else cache)
    candidates = sorted(selected.rglob("atb_*.parquet"))
    if len(candidates) != 1:
        print(
            f"CANOE_CLOUD: expected one ATB table in {selected}, found {len(candidates)}; official fallback.",
            flush=True,
        )
        return None
    import pyarrow.parquet as pq

    source = candidates[0]
    parquet = pq.ParquetFile(source)
    required = {
        "atb_year",
        "technology",
        "display_name",
        "scenario",
        "core_metric_case",
        "crpyears",
        "core_metric_parameter",
        "core_metric_variable",
        "units",
        "value",
    }
    if not required.issubset(parquet.schema_arrow.names):
        raise ValueError(
            "CANOE_CLOUD ATB schema is missing required selection/provenance columns."
        )
    years = set()
    for batch in parquet.iter_batches(columns=["atb_year"]):
        years.update(int(value) for value in batch.column(0).to_pylist())
    if years != {required_edition}:
        print(
            f"CANOE_CLOUD: published ATB editions {sorted(years)} do not match {required_edition}; official fallback.",
            flush=True,
        )
        return None
    digest = sha256(source)
    dest_dir = root / config["raw_dir"] / "atb" / digest
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "atb.parquet"
    if not dest.exists():
        print(
            f"CANOE_CLOUD: importing {source.name} ({source.stat().st_size / 1e6:.1f} MB).",
            flush=True,
        )
        temporary = dest.with_suffix(".part")
        shutil.copyfile(source, temporary)
        if sha256(temporary) != digest:
            raise ValueError("CANOE_CLOUD source changed during import.")
        temporary.replace(dest)
    if sha256(dest) != digest:
        raise ValueError("CANOE_CLOUD imported cache checksum mismatch.")
    record = dest_dir / "acquisition_manifest.json"
    if not record.exists():
        manifest(
            record,
            {
                "edition": required_edition,
                "revision": "external-cache-" + digest[:16],
                "sha256": digest,
                "bytes": dest.stat().st_size,
                "format": "parquet",
                "source": str(source.resolve()),
                "snapshot_date": selected.name,
                "connector_reference_commit": config["connector_reference_commit"],
                "config_sha256": sha256(config_path),
                "structure": {"rows": parquet.metadata.num_rows},
                "scope": "external processed source; not independently reproduced from raw ATB",
            },
        )
    print(
        f"CANOE_CLOUD: verified ATB {required_edition}, snapshot {selected.name}.",
        flush=True,
    )
    return dest, record


def sync_archive(config_path=None, *, renewables=False):
    """Download the published archive to GeoCANOE Bronze, extracting files safely."""
    root, _, config = load_config(config_path)
    try:
        import gdown
    except ImportError as exc:
        raise RuntimeError(
            'Install the cloud extra: pip install -e ".[cloud]"'
        ) from exc
    raw = root / config["raw_dir"] / "archives"
    raw.mkdir(parents=True, exist_ok=True)
    name = "renewables_cache" if renewables else "silver"
    file_id = config["renewables_archive_id" if renewables else "silver_archive_id"]
    archive = raw / f"{name}.zip"
    if archive.exists():
        raise ValueError(
            "Archive already exists; published IDs can change. Reuse the extracted snapshot or explicitly choose a new archive directory."
        )
    temporary = archive.with_suffix(".zip.part")
    print(
        f"CANOE_CLOUD: downloading published {name} archive; upstream size is not pinned.",
        flush=True,
    )
    if not gdown.download(
        id=file_id, output=str(temporary), quiet=False, use_cookies=False
    ):
        raise RuntimeError("CANOE_CLOUD archive download failed.")
    extracted = root / config["raw_dir"] / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    print("CANOE_CLOUD: validating archive paths and extracting files...", flush=True)
    with zipfile.ZipFile(temporary) as zipped:
        for member in zipped.infolist():
            target = (extracted / member.filename).resolve()
            if not target.is_relative_to(extracted.resolve()):
                raise ValueError("Unsafe archive member path.")
        for member in tqdm(
            zipped.infolist(), desc="CANOE_CLOUD: extracting", unit="file"
        ):
            zipped.extract(member, extracted)
    temporary.replace(archive)
    manifest(
        archive.with_suffix(".json"),
        {
            "file_id": file_id,
            "sha256": sha256(archive),
            "bytes": archive.stat().st_size,
            "extracted_dir": str(extracted),
            "connector_reference_commit": config["connector_reference_commit"],
        },
    )
    print(
        f"CANOE_CLOUD: archive preserved; extracted to {extracted}. Set cache_dir to this directory for import.",
        flush=True,
    )
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--renewables", action="store_true")
    args = parser.parse_args()
    if args.sync:
        sync_archive(args.config, renewables=args.renewables)
    else:
        import_atb_cache(args.config)
