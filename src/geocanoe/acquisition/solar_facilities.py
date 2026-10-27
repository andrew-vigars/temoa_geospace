"""Immutable TZ-SAM Q1 2024 capture and Canadian footprint diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio

from geocanoe.acquisition.aboriginal_lands import download_file
from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.paths import find_project_root
from geocanoe.registry.geospatial_sources import load_geospatial_source_registry

SOURCE_ID = "transitionzero_solar_2024q1"
GPKG = "2024Q1_final_analysis_polygons.gpkg"
CSV = "2024Q1_final_analysis_polygons.csv"
SOURCES = "2024Q1_final_sources.csv"
PDF = "tz-sam-scientific-methodology-Q12024.pdf"


def source_record():
    return next(
        s for s in load_geospatial_source_registry()["sources"] if s["id"] == SOURCE_ID
    )


def verify_published_files(root, metadata):
    """Check selected payloads against Zenodo's published byte sizes and MD5s."""
    if str(metadata.get("id")) != "11368204":
        raise ValueError("Unexpected TZ-SAM Zenodo record identity.")
    published = {f["key"]: f for f in metadata["files"]}
    for name in (GPKG, CSV, SOURCES):
        entry = published[name]
        path = Path(root) / name
        algorithm, expected = entry["checksum"].split(":", 1)
        if algorithm != "md5":
            raise ValueError("Review changed Zenodo checksum algorithm.")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, algorithm).hexdigest()
        if path.stat().st_size != entry["size"] or actual != expected:
            raise ValueError(f"Published TZ-SAM checksum/size mismatch: {name}")


def inspect_source(root):
    root = Path(root)
    layers = pyogrio.list_layers(root / GPKG)
    if len(layers) != 1 or layers[0][1] not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Expected one solar polygon layer.")
    layer = str(layers[0][0])
    polygons = pyogrio.read_dataframe(root / GPKG, layer=layer)
    table = pd.read_csv(root / CSV)
    sources = pd.read_csv(root / SOURCES)
    required = {"cluster_id", "capacity_mw", "constructed_before", "constructed_after"}
    if not required <= set(polygons) or not required | {
        "country",
        "latitude",
        "longitude",
    } <= set(table):
        raise ValueError("Missing solar asset fields.")
    if not {"cluster_id", "source_id", "source", "source_date"} <= set(sources):
        raise ValueError("Missing solar lineage fields.")
    for data in (polygons, table):
        if data.cluster_id.isna().any() or data.cluster_id.duplicated().any():
            raise ValueError("Solar cluster IDs must be present and unique.")
    ids = set(polygons.cluster_id)
    if not ids or set(table.cluster_id) != ids or not set(sources.cluster_id) <= ids:
        raise ValueError("Solar polygon/table/lineage IDs do not align.")
    if set(sources.cluster_id) != ids:
        raise ValueError("Solar lineage must cover every asset.")
    if (
        polygons.crs is None
        or polygons.geometry.isna().any()
        or polygons.geometry.is_empty.any()
    ):
        raise ValueError("Solar geometries require a CRS and nonempty polygons.")
    numeric = polygons.capacity_mw.to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric < 0).any():
        raise ValueError("Invalid solar estimated capacity.")
    joined = polygons[["cluster_id", "capacity_mw"]].merge(
        table[["cluster_id", "capacity_mw"]], on="cluster_id"
    )
    if not np.allclose(joined.capacity_mw_x, joined.capacity_mw_y):
        raise ValueError("Solar capacity fields disagree between files.")
    canada_ids = set(table.loc[table.country.eq("Canada"), "cluster_id"])
    canada = polygons.loc[polygons.cluster_id.isin(canada_ids)].copy()
    inventory = {
        "layer": layer,
        "crs": polygons.crs.to_string(),
        "global_features": len(polygons),
        "canada_features": len(canada),
        "canada_selection": "CSV country equals Canada, joined by cluster_id; not a spatial completeness check",
        "lineage_rows": len(sources),
        "lineage_source_counts": sources.source.value_counts().to_dict(),
        "invalid_geometry_count": int((~polygons.is_valid).sum()),
        "fields": list(polygons.columns),
        "date_warning": "CSV date values include time-like strings; retain GeoPackage timestamps for later review.",
        "coverage_warning": "Observed 63,096 features in analysis_polygons.no_ukraine; catalogue describes 63,616 assets.",
        "raw_geometry_lineage": "Sources CSV preserved; raw_polygons.gpkg not acquired in this selection.",
    }
    return canada, inventory


def preview_source(canada, inventory, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if canada.empty:
        raise ValueError("No Canada-labelled solar footprints to preview.")
    display = canada.to_crs(3347)
    boundary_path = find_project_root() / "data_files/raw/basemaps/lpr_000b21a_e.shp"
    context = {}
    province = None
    largest = display.loc[[display.area.idxmax()]]
    if boundary_path.is_file():
        import geopandas as gpd

        boundaries = gpd.read_file(boundary_path).to_crs(3347)
        province = boundaries.loc[boundaries.intersects(largest.geometry.iloc[0])]
        context = {
            "boundary_path": str(boundary_path),
            "province_names": province.PRENAME.tolist(),
            "boundary_sidecar_sha256": {
                suffix: sha256(boundary_path.with_suffix(suffix))
                for suffix in (".shp", ".shx", ".dbf", ".prj")
            },
        }
    has_context = province is not None and not province.empty
    fig, axes = plt.subplots(
        1, 3 if has_context else 2, figsize=(19 if has_context else 15, 8)
    )
    if boundary_path.is_file():
        boundaries.geometry.simplify(3000, preserve_topology=False).plot(
            ax=axes[0], color="#f0f1f2", edgecolor="#aeb4b9", linewidth=0.4
        )
    # Centroids make small footprints visible nationally; the second panel shows actual polygons.
    display.centroid.plot(ax=axes[0], color="#c17c16", markersize=8, alpha=0.7)
    axes[0].set_title(f"{len(display):,} Canada-labelled assets (centroid overview)")
    detail_ax = axes[-1]
    if has_context:
        province.geometry.simplify(1000, preserve_topology=False).plot(
            ax=axes[1], color="#f0f1f2", edgecolor="#59636b", linewidth=0.8
        )
        point = largest.geometry.centroid.iloc[0]
        axes[1].scatter(point.x, point.y, marker="*", s=160, color="#b94b14", zorder=3)
        axes[1].annotate(
            f"Cluster {int(largest.cluster_id.iloc[0])}",
            (point.x, point.y),
            xytext=(12, 14),
            textcoords="offset points",
            fontsize=9,
            arrowprops={"arrowstyle": "-", "color": "#59636b"},
        )
        axes[1].set_title("Location within " + ", ".join(province.PRENAME))
    largest.plot(ax=detail_ax, color="#e9b35b", edgecolor="#69400b", linewidth=0.7)
    location = largest.geometry.centroid.to_crs(4326).iloc[0]
    detail_ax.set_title(
        f"Largest mapped footprint — cluster {int(largest.cluster_id.iloc[0])}"
        f"\n{location.y:.4f}° N, {abs(location.x):.4f}° {'W' if location.x < 0 else 'E'}"
    )
    for ax in axes:
        ax.set_xlabel("EPSG:3347 easting (m)")
        ax.set_ylabel("EPSG:3347 northing (m)")
        ax.set_aspect("equal")
    fig.suptitle(
        "TransitionZero Solar Asset Mapper — Q1 2024\n"
        "CC BY-NC 4.0 | Display projection only; no clipping or exclusions"
    )
    fig.tight_layout()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return {
        "path": str(destination),
        "display_crs": "EPSG:3347",
        "canada_features": len(canada),
        "source_layer": inventory["layer"],
        "overview": "centroids",
        "detail_cluster_id": int(largest.cluster_id.iloc[0]),
        "geographic_context": context,
    }


def acquire_solar_facilities(output_dir=None, *, preview=True):
    source = source_record()
    root = (
        Path(output_dir)
        if output_dir
        else find_project_root() / source["bronze"]["root"]
    )
    root.mkdir(parents=True, exist_ok=True)
    record = root / "acquisition_manifest.json"
    previous = json.loads(record.read_text()) if record.exists() else None
    names = (GPKG, CSV, SOURCES, PDF, "zenodo_record.json")
    if previous:
        for name in names:
            if (
                not (root / name).is_file()
                or sha256(root / name) != previous["files"][name]["sha256"]
            ):
                raise ValueError(f"Captured solar checksum mismatch: {name}")
    else:
        download_file(source["metadata_url"], root / "zenodo_record.json")
        metadata = json.loads((root / "zenodo_record.json").read_text(encoding="utf-8"))
        if str(metadata.get("id")) != "11368204":
            raise ValueError("Unexpected TZ-SAM Zenodo record identity.")
        for name in (GPKG, CSV, SOURCES):
            download_file(
                source["distribution_base"] + name + "?download=1", root / name
            )
        verify_published_files(root, metadata)
        download_file(source["methodology_url"], root / PDF)
    with (root / PDF).open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("Methodology response is not a PDF.")
    canada, inventory = inspect_source(root)
    if previous is None:
        manifest(
            record,
            {
                "source_id": SOURCE_ID,
                "source": source,
                "inventory": inventory,
                "files": {
                    name: {
                        "sha256": sha256(root / name),
                        "size_bytes": (root / name).stat().st_size,
                    }
                    for name in names
                },
                "scope": "Global source bytes preserved; Canada selection for preview only",
            },
        )
    if preview:
        report = preview_source(
            canada, inventory, root / "preview" / "solar_canada.png"
        )
        report["gpkg_sha256"] = sha256(root / GPKG)
        manifest(root / "preview" / "preview_manifest.json", report)
    print(f"Solar-facility Bronze verified: {root}", flush=True)
    return {"geopackage": root / GPKG, "manifest": record}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    acquire_solar_facilities(args.output_dir, preview=not args.no_preview)


if __name__ == "__main__":
    main()
