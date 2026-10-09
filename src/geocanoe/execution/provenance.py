"""Archive model configuration sources with their capture time and hashes."""

import hashlib
import json
from pathlib import Path


def configuration_snapshot(path: Path) -> dict:
    """Capture the exact bytes of a UTF-8 TOML source."""
    data = path.read_bytes()
    return {"source_path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "text": data.decode("utf-8")}


def archive_run_configs(database: Path, solver: Path, destination: Path,
                        batch: Path | None = None) -> dict:
    """Prefer build-time snapshots; identify older sources copied at run time."""
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = database.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    sources = manifest.get("configuration_sources", {})
    resolved = manifest.get("resolved_gold_configuration", {})
    paths = {"build": manifest.get("silver_build_profile", {}).get("source_path"),
             "model": resolved.get("model_config_path"),
             "schema": resolved.get("scenario_config_path"),
             "temoav4": solver, "batch": batch}
    records = {}
    for role, source in paths.items():
        snapshot = sources.get(role)
        origin = "schema-build snapshot" if snapshot else "run-time source copy"
        if snapshot is None and source and Path(source).exists():
            snapshot = configuration_snapshot(Path(source))
        if snapshot is None:
            records[role] = {"status": "unavailable", "source_path": str(source) if source else None}
            continue
        data = snapshot["text"].encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        if digest != snapshot["sha256"]:
            raise ValueError(f"Configuration snapshot hash mismatch: {role}")
        target = destination / f"{role}.toml"
        target.write_bytes(data)
        records[role] = {"status": "archived", "origin": origin,
                         "source_path": snapshot["source_path"], "sha256": digest,
                         "archive": target.name}
    (destination / "index.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records
