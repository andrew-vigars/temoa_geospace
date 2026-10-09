"""Build a directory of Gold scenario overlays with individual logs and an index."""

import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from geocanoe.config import load_geospatial_build_config
from geocanoe.schema.build import resolve_schema_configuration, validate_required_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--exclude-snap-outliers", action="store_true",
                        help="Accept the audited graph-snap exclusions for each build")
    args = parser.parse_args()
    scenarios = sorted(args.scenario_dir.glob("*.toml"))
    if not scenarios:
        parser.error("The scenario directory contains no TOML files")
    profile = load_geospatial_build_config(args.config)
    for scenario in scenarios:
        validate_required_paths(resolve_schema_configuration(profile, scenario))
    args.log_dir.mkdir(parents=True, exist_ok=False)
    index = args.log_dir / "schema_set.csv"
    fields = ["scenario", "status", "started_utc", "elapsed_seconds", "database", "manifest", "log"]
    with index.open("w", newline="", encoding="utf-8") as inventory:
        writer = csv.DictWriter(inventory, fieldnames=fields)
        writer.writeheader()
        inventory.flush()
        for number, scenario in enumerate(scenarios, 1):
            log = (args.log_dir / f"{scenario.stem}.log").resolve()
            started = datetime.now(timezone.utc).isoformat()
            clock = monotonic()
            print(f"[{number}/{len(scenarios)}] Building {scenario.stem}", flush=True)
            with log.open("w", encoding="utf-8") as output:
                output.write(f"Started UTC: {started}\nScenario: {scenario.resolve()}\n")
                output.flush()
                result = subprocess.run(
                    [sys.executable, "-u", str(Path(__file__).with_name("build_schema.py")),
                     "--config", str(args.config), "--scenario", str(scenario)],
                    stdout=output, stderr=subprocess.STDOUT, check=False,
                    input="y\ny\n" if args.exclude_snap_outliers else "",
                    text=True, encoding="utf-8",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            database = ""
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.startswith("Output database: "):
                    database = str(Path(line.removeprefix("Output database: ")).resolve())
            elapsed = round(monotonic() - clock, 2)
            writer.writerow(dict(scenario=str(scenario.resolve()),
                                 status="success" if result.returncode == 0 else "failed",
                                 started_utc=started, elapsed_seconds=elapsed,
                                 database=database,
                                 manifest=str(Path(database).with_suffix(".manifest.json")) if database else "",
                                 log=str(log)))
            inventory.flush()
            print(f"  {'Complete' if result.returncode == 0 else 'Failed'} ({elapsed}s): {log}", flush=True)
            if result.returncode:
                raise SystemExit(f"Build failed; inspect {log}. Inventory: {index}")
    print(f"Built {len(scenarios)} schemas. Inventory: {index.resolve()}", flush=True)


if __name__ == "__main__":
    main()
