"""Capture the CPCAD 2025 source archive, metadata and diagnostic preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
import zipfile

import pandas as pd
import pyogrio

from geocanoe.acquisition.aboriginal_lands import download_file
from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import load_geospatial_source_registry

SOURCE_ID = "eccc_cpcad_2025"
MAIN_LAYER = "ProtectedConservedArea_2025"


def source_record():
    return next(
        s for s in load_geospatial_source_registry()["sources"] if s["id"] == SOURCE_ID
    )


def extract_archive(archive_path, destination):
    """Validate all archive paths and CRCs before extracting source bytes."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"Extraction destination already exists: {destination}")
    with zipfile.ZipFile(archive_path) as archive:
        names = set()
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.orig_filename
                or ":" in member.filename
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError(f"Unsafe ZIP member: {member.filename}")
            folded = member.filename.casefold()
            if folded in names:
                raise ValueError("Duplicate ZIP member.")
            names.add(folded)
        if archive.testzip() is not None:
            raise ValueError("CPCAD ZIP CRC validation failed.")
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            staging = Path(temporary) / "source"
            archive.extractall(staging)
            staging.rename(destination)
    return destination


def inspect_source(source_dir):
    """Inventory all layers and read classification labels from the supplied dictionary."""
    source_dir = Path(source_dir)
    databases = list(source_dir.glob("*.gdb"))
    dictionaries = list(source_dir.glob("*.xlsx"))
    if (
        len(databases) != 1
        or len(dictionaries) != 1
        or not list(source_dir.glob("*.xml"))
    ):
        raise ValueError("Expected one geodatabase, one dictionary and XML metadata.")
    database = databases[0]
    layers = {}
    for name, _ in pyogrio.list_layers(database):
        info = pyogrio.read_info(database, layer=name)
        layers[name] = {k: info[k] for k in ("features", "crs", "geometry_type")}
        layers[name]["fields"] = info["fields"].tolist()
    if MAIN_LAYER not in layers or layers[MAIN_LAYER]["geometry_type"] not in {
        "Polygon",
        "MultiPolygon",
    }:
        raise ValueError("Missing CPCAD main polygon layer.")
    if not layers[MAIN_LAYER]["crs"] or not layers[MAIN_LAYER]["features"]:
        raise ValueError("CPCAD requires nonempty georeferenced polygons.")
    labels = {}
    frame = pd.read_excel(
        dictionaries[0], sheet_name="PA_OECM_DF_E | PA_OECM_DF_F", header=None
    )
    for code, label in frame.iloc[:, :2].itertuples(index=False, name=None):
        if isinstance(code, (int, float)) and pd.notna(code) and isinstance(label, str):
            labels[int(code)] = label.strip()
    if set(labels) != {1, 2, 3, 4, 5}:
        raise ValueError("Unexpected PA/OECM dictionary domain.")
    attributes = pyogrio.read_dataframe(
        database, layer=MAIN_LAYER, columns=["PA_OECM_DF", "BIOME"], read_geometry=False
    )
    if attributes.PA_OECM_DF.isna().any() or not set(attributes.PA_OECM_DF) <= set(
        labels
    ):
        raise ValueError("Unknown or missing PA/OECM classification.")
    if attributes.BIOME.isna().any() or not set(attributes.BIOME) <= {"T", "M"}:
        raise ValueError("Unknown or missing biome classification.")
    counts = attributes.groupby(["PA_OECM_DF", "BIOME"]).size()
    return database, {
        "layers": layers,
        "classification_labels": labels,
        "classification_counts": [
            {"code": int(code), "biome": biome, "features": int(count)}
            for (code, biome), count in counts.items()
        ],
    }


def preview_source(database, inventory, destination):
    """Plot source statuses in separate biome panels; never edit source geometry."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    data = pyogrio.read_dataframe(
        database, layer=MAIN_LAYER, columns=["PA_OECM_DF", "BIOME"]
    )
    if data.geometry.isna().any() or data.geometry.is_empty.any():
        raise ValueError("CPCAD contains missing or empty main-layer geometry.")
    display = data.to_crs(3347)
    display.geometry = display.geometry.simplify(1000, preserve_topology=True)
    colors = {1: "#218c55", 2: "#db8c20", 3: "#8bcaa8", 4: "#efc786", 5: "#777777"}
    fig, axes = plt.subplots(1, 2, figsize=(16, 9))
    for ax, biome, title in zip(
        axes, ["T", "M"], ["Terrestrial / freshwater", "Marine"]
    ):
        subset = display.loc[display.BIOME == biome]
        for code in colors:
            group = subset.loc[subset.PA_OECM_DF == code]
            if not group.empty:
                group.plot(ax=ax, color=colors[code], linewidth=0)
        ax.set_title(f"{title} ({len(subset):,} source features)")
        ax.set_aspect("equal")
        ax.set_axis_off()
    labels = inventory["classification_labels"]
    fig.legend(
        handles=[Patch(color=colors[c], label=labels[c]) for c in colors],
        loc="lower center",
        ncol=2,
        fontsize=9,
    )
    fig.suptitle(
        "CPCAD 2025 — source status categories\n"
        "Display simplified to 1 km in EPSG:3347; no exclusions applied"
    )
    fig.tight_layout(rect=(0, 0.14, 1, 0.94))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return {
        "path": str(destination),
        "display_crs": "EPSG:3347",
        "display_simplification_m": 1000,
        "source_layer": MAIN_LAYER,
        "scope": "Feature counts; overlapping areas are not dissolved or summed.",
    }


def acquire_protected_areas(output_dir=None, *, preview=True):
    """Capture once; subsequent execution verifies all captured file hashes offline."""
    source = source_record()
    root = (
        Path(output_dir)
        if output_dir
        else find_project_root() / source["bronze"]["root"]
    )
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "ProtectedConservedArea_2025.zip"
    catalogue = root / "catalogue.json"
    record = root / "acquisition_manifest.json"
    previous = json.loads(record.read_text()) if record.exists() else None
    if previous:
        for relative, expected in previous["files"].items():
            path = root / relative
            if not path.is_file() or sha256(path) != expected["sha256"]:
                raise ValueError(f"Captured CPCAD checksum mismatch: {relative}")
    else:
        download_file(source["metadata_url"], catalogue)
        metadata = json.loads(catalogue.read_text(encoding="utf-8"))
        if (
            not metadata.get("success")
            or metadata.get("result", {}).get("id") != source["record_id"]
        ):
            raise ValueError("Unexpected CPCAD catalogue identity.")
        download_file(source["distribution_url"], archive)
        if (root / "source").exists():
            # Never adopt an unverified extraction left by an interrupted capture.
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                staged = extract_archive(archive, Path(temporary) / "source")
                expected = {
                    p.relative_to(staged).as_posix(): sha256(p)
                    for p in staged.rglob("*")
                    if p.is_file()
                }
                actual = {
                    p.relative_to(root / "source").as_posix(): sha256(p)
                    for p in (root / "source").rglob("*")
                    if p.is_file()
                }
                if actual != expected:
                    raise ValueError(
                        "Existing CPCAD extraction differs from ZIP; use a new output directory."
                    )
        else:
            extract_archive(archive, root / "source")
    database, inventory = inspect_source(root / "source")
    if previous is None:
        files = [archive, catalogue] + sorted(
            p for p in (root / "source").rglob("*") if p.is_file()
        )
        manifest(
            record,
            {
                "source_id": SOURCE_ID,
                "source": source,
                "inventory": inventory,
                "files": {
                    p.relative_to(root).as_posix(): {
                        "sha256": sha256(p),
                        "size_bytes": p.stat().st_size,
                    }
                    for p in files
                },
                "scope": "Bronze; no siting exclusions or Silver integration",
            },
        )
    if preview:
        report = preview_source(
            database, inventory, root / "preview" / "cpcad_2025.png"
        )
        report["archive_sha256"] = sha256(archive)
        manifest(root / "preview" / "preview_manifest.json", report)
    print(f"CPCAD Bronze verified: {root}", flush=True)
    return {"geodatabase": database, "manifest": record}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="New directory for a fresh snapshot."
    )
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    acquire_protected_areas(args.output_dir, preview=not args.no_preview)


if __name__ == "__main__":
    main()
