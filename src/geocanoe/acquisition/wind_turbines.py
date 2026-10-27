"""Capture NRCan turbine points unchanged; polygon footprints belong in Silver."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import pyogrio

from geocanoe.acquisition.aboriginal_lands import download_file
from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.acquisition.protected_areas import extract_archive
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import load_geospatial_source_registry

SOURCE_ID = "nrcan_wind_turbines_snapshot_20261023"


def source_record():
    return next(
        s for s in load_geospatial_source_registry()["sources"] if s["id"] == SOURCE_ID
    )


def inspect_source(source_dir):
    databases = list(Path(source_dir).rglob("*.gdb"))
    if len(databases) != 1:
        raise ValueError("Expected one turbine geodatabase.")
    database = databases[0]
    layers = pyogrio.list_layers(database)
    points = [str(name) for name, geometry in layers if geometry == "Point"]
    if len(points) != 1:
        raise ValueError("Expected one turbine point layer.")
    layer = points[0]
    info = pyogrio.read_info(database, layer=layer)
    required = {
        "Province_Territory",
        "Project_Name",
        "Turbine_Identifier",
        "Latitude",
        "Longitude",
    }
    if not info["crs"] or not info["features"] or not required <= set(info["fields"]):
        raise ValueError("Missing turbine CRS, features or expected source fields.")
    data = pyogrio.read_dataframe(database, layer=layer)
    if data.geometry.isna().any() or data.geometry.is_empty.any():
        raise ValueError("Turbine point geometry is missing or empty.")
    project_keys = (
        data[["Province_Territory", "Project_Name"]].dropna().drop_duplicates()
    )
    return database, {
        "layer": layer,
        "crs": info["crs"],
        "features": int(info["features"]),
        "fields": info["fields"].tolist(),
        "bounds": list(info["total_bounds"]),
        "layers": [[str(name), geometry] for name, geometry in layers],
        "province_feature_counts": data.Province_Territory.value_counts(
            dropna=False
        ).to_dict(),
        "named_project_groups": len(project_keys),
        "missing_project_names": int(data.Project_Name.isna().sum()),
        "duplicate_turbine_identifiers": int(
            data.Turbine_Identifier.duplicated().sum()
        ),
        "note": "Project names and identifiers are preserved; no grouping, hulls or exclusions applied.",
    }


def preview_source(database, inventory, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = pyogrio.read_dataframe(database, layer=inventory["layer"])
    display = data.to_crs(3347)
    fig, ax = plt.subplots(figsize=(13, 9))
    display.plot(
        ax=ax,
        column="Province_Territory",
        categorical=True,
        legend=True,
        markersize=3,
        alpha=0.65,
        cmap="tab20",
        legend_kwds={
            "loc": "upper left",
            "bbox_to_anchor": (1.01, 1),
            "title": "Source province / territory",
        },
    )
    ax.set_title(
        f"Canadian Wind Turbine Database — {len(data):,} source points\n"
        f"Layer: {inventory['layer']} | No footprint polygons or buffers"
    )
    ax.set_xlabel("EPSG:3347 easting (m)")
    ax.set_ylabel("EPSG:3347 northing (m)")
    fig.tight_layout()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return {
        "path": str(destination),
        "display_crs": "EPSG:3347",
        "source_layer": inventory["layer"],
        "features": len(data),
        "scope": "Source point locations only; no boundaries inferred.",
    }


def file_hashes(root):
    return {
        p.relative_to(root).as_posix(): {
            "sha256": sha256(p),
            "size_bytes": p.stat().st_size,
        }
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def acquire_wind_turbines(output_dir=None, *, preview=True):
    source = source_record()
    root = (
        Path(output_dir)
        if output_dir
        else find_project_root() / source["bronze"]["root"]
    )
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "wind_turbine_database_en.gdb.zip"
    catalogue = root / "catalogue.json"
    record = root / "acquisition_manifest.json"
    previous = json.loads(record.read_text()) if record.exists() else None
    if previous:
        for relative, expected in previous["files"].items():
            path = root / relative
            if not path.is_file() or sha256(path) != expected["sha256"]:
                raise ValueError(f"Captured turbine checksum mismatch: {relative}")
    else:
        download_file(source["metadata_url"], catalogue)
        metadata = json.loads(catalogue.read_text(encoding="utf-8"))
        result = metadata.get("result", {})
        if not metadata.get("success") or result.get("id") != source["record_id"]:
            raise ValueError("Unexpected turbine catalogue identity.")
        if source["distribution_url"] not in {r["url"] for r in result["resources"]}:
            raise ValueError("Configured turbine ZIP absent from catalogue.")
        download_file(source["distribution_url"], archive)
        if (root / "source").exists():
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                staged = extract_archive(archive, Path(temporary) / "source")
                if file_hashes(staged) != file_hashes(root / "source"):
                    raise ValueError(
                        "Turbine extraction differs from ZIP; use a new directory."
                    )
        else:
            extract_archive(archive, root / "source")
    database, inventory = inspect_source(root / "source")
    if previous is None:
        files = file_hashes(root / "source")
        files = {f"source/{name}": value for name, value in files.items()}
        files.update(
            {
                p.name: {"sha256": sha256(p), "size_bytes": p.stat().st_size}
                for p in (archive, catalogue)
            }
        )
        manifest(
            record,
            {
                "source_id": SOURCE_ID,
                "source": source,
                "inventory": inventory,
                "files": files,
                "scope": "Bronze source capture; no Silver polygons or exclusions",
            },
        )
    if preview:
        report = preview_source(
            database, inventory, root / "preview" / "wind_turbines.png"
        )
        report["archive_sha256"] = sha256(archive)
        manifest(root / "preview" / "preview_manifest.json", report)
    print(f"Wind-turbine Bronze verified: {root}", flush=True)
    return {"geodatabase": database, "manifest": record}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="New directory for a fresh snapshot."
    )
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    acquire_wind_turbines(args.output_dir, preview=not args.no_preview)


if __name__ == "__main__":
    main()
