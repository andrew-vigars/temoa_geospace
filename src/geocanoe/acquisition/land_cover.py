"""Preserve NRCan's national 2020 land cover and preview source classes only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling

from geocanoe.acquisition.aboriginal_lands import download_file
from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import load_geospatial_source_registry

SOURCE_ID = "nrcan_land_cover_2020"


def source_record():
    """Use the shared geographic registry as the acquisition authority."""
    registry = load_geospatial_source_registry()
    return next(s for s in registry["sources"] if s["id"] == SOURCE_ID)


def inspect_raster(path):
    """Read structural metadata without loading the national pixel array."""
    with rasterio.open(path) as raster:
        if raster.driver != "GTiff" or raster.count != 1:
            raise ValueError("Land cover requires a single-band GeoTIFF.")
        if raster.crs is None or not np.issubdtype(
            np.dtype(raster.dtypes[0]), np.integer
        ):
            raise ValueError("Land cover requires a CRS and integer class codes.")
        if raster.res != (30.0, 30.0) or not raster.crs.is_projected:
            raise ValueError("Expected projected 30 m land-cover pixels.")
        try:
            colors = raster.colormap(1)
        except ValueError:
            colors = {}
        return {
            "driver": raster.driver,
            "width": raster.width,
            "height": raster.height,
            "count": raster.count,
            "dtype": raster.dtypes[0],
            "crs_wkt": raster.crs.to_wkt(),
            "crs": raster.crs.to_string(),
            "resolution": list(raster.res),
            "bounds": list(raster.bounds),
            "transform": list(raster.transform),
            "nodata": raster.nodata,
            "overviews": raster.overviews(1),
            "tags": raster.tags(),
            "band_tags": raster.tags(1),
            "colormap": colors,
        }


def preview_raster(path, destination, max_dimension=1800):
    """Bounded nearest-neighbour quicklook; sampled classes are not area totals."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    info = inspect_raster(path)
    with rasterio.open(path) as raster:
        scale = min(1.0, max_dimension / max(raster.width, raster.height))
        shape = (
            max(1, round(raster.height * scale)),
            max(1, round(raster.width * scale)),
        )
        values = raster.read(
            1, out_shape=shape, masked=True, resampling=Resampling.nearest
        )
        codes = np.unique(values.compressed()).astype(int)
        if not len(codes):
            raise ValueError("Land-cover preview has no valid pixels.")
        palette = raster.colormap(1) if info["colormap"] else {}
        colors = [
            np.array(palette[c]) / 255 if c in palette else plt.cm.tab20(i % 20)
            for i, c in enumerate(codes)
        ]
        indexed = np.ma.array(np.searchsorted(codes, values.data), mask=values.mask)
        fig, ax = plt.subplots(figsize=(13, 10))
        ax.imshow(
            indexed,
            cmap=ListedColormap(colors),
            norm=BoundaryNorm(np.arange(len(codes) + 1) - 0.5, len(codes)),
            extent=(
                raster.bounds.left,
                raster.bounds.right,
                raster.bounds.bottom,
                raster.bounds.top,
            ),
            interpolation="nearest",
        )
        ax.set_title(
            "Canada 2020 land cover — source classes\n"
            "Nearest-neighbour quicklook; no eligibility rules applied"
        )
        ax.set_xlabel("Source projection easting (m)")
        ax.set_ylabel("Source projection northing (m)")
        ax.legend(
            handles=[
                Patch(color=color, label=f"Class {code}")
                for code, color in zip(codes, colors)
            ],
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            title="Source codes",
        )
        fig.tight_layout()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(destination, dpi=150)
        plt.close(fig)
    return {
        "path": str(destination),
        "sample_shape": list(shape),
        "sampled_class_codes": codes.tolist(),
        "resampling": "nearest",
        "class_labels": "numeric source codes; semantic legend not yet verified",
    }


def acquire_land_cover(output_dir=None, *, preview=True):
    """Acquire once; verify cached byte identity and preserve metadata snapshots.

    Refreshes use a new output directory. Existing captured sources are never
    overwritten, including when the general Bronze CLI requests overwrite.
    """
    source = source_record()
    root = (
        Path(output_dir)
        if output_dir
        else find_project_root() / source["bronze"]["root"]
    )
    root.mkdir(parents=True, exist_ok=True)
    raster_path = root / "landcover-2020-classification.tif"
    metadata_path = root / "catalogue.json"
    record_path = root / "acquisition_manifest.json"
    previous = json.loads(record_path.read_text()) if record_path.exists() else None
    if previous:
        for path in (raster_path, metadata_path):
            if (
                not path.exists()
                or sha256(path) != previous["files"][path.name]["sha256"]
            ):
                raise ValueError(f"Captured land-cover checksum mismatch: {path.name}")
    else:
        download_file(source["metadata_url"], metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        result = metadata.get("result", {})
        if not metadata.get("success") or result.get("id") != source["record_id"]:
            raise ValueError("Unexpected land-cover catalogue identity.")
        if source["distribution_url"] not in {r["url"] for r in result["resources"]}:
            raise ValueError("Configured TIFF is absent from the catalogue resources.")
        download_file(source["distribution_url"], raster_path)
    info = inspect_raster(raster_path)
    if previous is None:
        manifest(
            record_path,
            {
                "source_id": SOURCE_ID,
                "source": source,
                "files": {
                    p.name: {"sha256": sha256(p), "size_bytes": p.stat().st_size}
                    for p in (raster_path, metadata_path)
                },
                "raster": info,
                "scope": "Bronze source acquisition; no exclusions or MW potential",
            },
        )
    if preview:
        report = preview_raster(raster_path, root / "preview" / "land_cover_2020.png")
        report["raster_sha256"] = (previous or json.loads(record_path.read_text()))[
            "files"
        ][raster_path.name]["sha256"]
        manifest(root / "preview" / "preview_manifest.json", report)
    print(f"Land-cover Bronze verified: {root}", flush=True)
    return {"raster": raster_path, "manifest": record_path}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="New directory for an explicit fresh snapshot."
    )
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    acquire_land_cover(args.output_dir, preview=not args.no_preview)


if __name__ == "__main__":
    main()
