"""Audited Atlite land eligibility shared by renewable capacity and CF weights."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy import sparse

from geocanoe.acquisition.era5_renewables import identity, manifest, sha256
from geocanoe.registry.geospatial_sources import GeospatialSourceRegistry

SOURCE_IDS = {
    "land_cover": "nrcan_land_cover_2020",
    "protected_areas": "eccc_cpcad_2025",
    "wind_turbines": "nrcan_wind_turbines_snapshot_20261023",
    "solar_facilities": "transitionzero_solar_2024q1",
}
TECHS = {"wind": "WIND_GEN", "solar": "SOLAR_GEN"}


def export_settings(settings, output):
    """Require intact exclusion products before encoding their generated MW limits."""
    if not policy(settings)["enabled"]:
        return settings
    directory = Path(output) / "land_exclusion"
    record = json.loads((directory / "manifest.json").read_text())
    for name, digest in record["sha256"].items():
        if sha256(directory / name) != digest:
            raise ValueError(f"Land export checksum mismatch: {name}")
    return {
        **settings,
        "capacity_limits": dict(
            enabled=True, input_csv=str(directory / "regional_capacity_assumptions.csv")
        ),
    }


def policy(settings):
    """Validate scenario choices; these are not a regulatory compliance model."""
    result = dict(settings.get("land_exclusion", {}))
    if not isinstance(result.get("enabled", False), bool):
        raise ValueError("land_exclusion.enabled must be boolean.")
    if not result.get("enabled", False):
        return {"enabled": False}
    defaults = dict(
        resolution_m=100,
        wind_buffer_m=100.0,
        protected_status_codes=[1],
        wind_excluded_codes=[14, 17, 18],
        solar_excluded_codes=[14, 15, 17, 18],
        wind_land_km2_per_gw=368.3,
        solar_land_km2_per_gw=34.4,
        batch_size=8,
    )
    result = {**defaults, **result}
    for field in (
        "resolution_m",
        "wind_land_km2_per_gw",
        "solar_land_km2_per_gw",
        "batch_size",
    ):
        if not np.isfinite(result[field]) or result[field] <= 0:
            raise ValueError(f"Invalid land exclusion {field}.")
    if not np.isfinite(result["wind_buffer_m"]) or result["wind_buffer_m"] <= 0:
        raise ValueError(
            "Wind hull buffer must be positive, including for single turbines."
        )
    if int(result["batch_size"]) != result["batch_size"]:
        raise ValueError("Land exclusion batch_size must be an integer.")
    for key, valid in [
        ("protected_status_codes", {1, 2, 3, 4}),
        ("wind_excluded_codes", set(range(1, 20))),
        ("solar_excluded_codes", set(range(1, 20))),
    ]:
        if not result[key] or not set(result[key]) <= valid:
            raise ValueError(f"Invalid land exclusion {key}.")
    if settings["technology"].get("pv_tracking") != "horizontal":
        raise ValueError("Land baseline requires single-axis horizontal tracking PV.")
    if settings.get("capacity_limits", {}).get("enabled", False):
        raise ValueError(
            "Choose generated land bounds or external capacity_limits, not both."
        )
    return result


def source_identity(settings, root):
    """Validate all Bronze bytes before use; hash inputs and implementation into runs."""
    selected = policy(settings)
    if not selected["enabled"]:
        return {"policy": selected}
    registry = GeospatialSourceRegistry(repo_root=root)
    records = {}
    for role, source_id in SOURCE_IDS.items():
        directory = registry.resolve_bronze_root(source_id)
        record = directory / "acquisition_manifest.json"
        captured = json.loads(record.read_text())
        for relative, entry in captured["files"].items():
            path = directory / relative
            if not path.is_file() or sha256(path) != entry["sha256"]:
                raise ValueError(f"Land source checksum mismatch: {role}/{relative}")
        records[role] = {"source_id": source_id, "manifest_sha256": sha256(record)}
    return {
        "policy": selected,
        "sources": records,
        "implementation_sha256": sha256(__file__),
    }


def wind_footprints(points, buffer_m):
    """Project-level hull plus explicit scenario buffer; no property-boundary claim."""
    points = points.to_crs(3347)
    keys = ["Province_Territory", "Project_Name"]
    if (
        points[keys].isna().any().any()
        or points[keys].astype(str).apply(lambda x: x.str.strip().eq("")).any().any()
    ):
        raise ValueError("Wind footprint grouping requires province and project names.")
    footprints = points.dissolve(by=keys).convex_hull.buffer(buffer_m)
    return gpd.GeoDataFrame(geometry=footprints, crs=3347).reset_index()


def exclusion_containers(settings, root, output, regions):
    from atlite.gis import ExclusionContainer

    selected = policy(settings)
    registry = GeospatialSourceRegistry(repo_root=root)
    protected_path = registry.resolve_bronze_path(SOURCE_IDS["protected_areas"])
    protected = gpd.read_file(
        protected_path,
        layer="ProtectedConservedArea_2025",
        where="BIOME = 'T'",
        bbox=tuple(regions.to_crs("ESRI:102001").total_bounds),
    )
    protected = protected.loc[
        protected.PA_OECM_DF.isin(selected["protected_status_codes"])
    ].to_crs(3347)
    protected.geometry = protected.geometry.make_valid()
    turbines = gpd.read_file(registry.resolve_bronze_path(SOURCE_IDS["wind_turbines"]))
    wind = wind_footprints(turbines, selected["wind_buffer_m"])
    solar_path = registry.resolve_bronze_path(SOURCE_IDS["solar_facilities"])
    table = pd.read_csv(
        registry.resolve_artifact_paths(SOURCE_IDS["solar_facilities"], "assets")[0]
    )
    solar = gpd.read_file(solar_path)
    solar = solar.loc[
        solar.cluster_id.isin(table.loc[table.country.eq("Canada"), "cluster_id"])
    ].to_crs(3347)
    solar.geometry = solar.geometry.make_valid()
    for name, frame in [
        ("protected_areas", protected),
        ("existing_wind", wind),
        ("existing_solar", solar),
    ]:
        frame.to_file(output / "exclusion_sources.gpkg", layer=name, driver="GPKG")
    containers = {}
    for technology in TECHS:
        excluder = ExclusionContainer(crs=3347, res=selected["resolution_m"])
        # Explicit whitelist excludes unknown codes and NoData as well as scenario exclusions.
        allowed = sorted(
            set(range(1, 20)) - set(selected[f"{technology}_excluded_codes"])
        )
        excluder.add_raster(
            str(registry.resolve_bronze_path(SOURCE_IDS["land_cover"])),
            # Atlite interprets an empty codes list as a truthiness mask.
            # A negative sentinel instead matches no uint8 land-cover class.
            codes=allowed or [-1],
            invert=True,
            nodata=0,
            allow_no_overlap=False,
        )
        for frame in (protected, wind, solar):
            excluder.add_geometry(frame)
        containers[technology] = excluder
    common = ExclusionContainer(crs=3347, res=selected["resolution_m"])
    for frame in (protected, wind, solar):
        common.add_geometry(frame)
    containers["common"] = common
    containers["land_cover"] = registry.resolve_bronze_path(SOURCE_IDS["land_cover"])
    return containers


def eligible_weights(cutout, regions, excluder, batch_size, checkpoint=None):
    """Integrate native mask area; allocate pixels to weather cells by their centres.

    Unlike GDAL average fractions times coarse cell area, this preserves native
    eligible area. Exact polygon intersections account for production boundaries;
    weather-cell assignment has the configured fine-raster spatial precision.
    """
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    import shapely

    projected = regions.to_crs(excluder.crs)
    xs, ys = np.asarray(cutout.data.x), np.asarray(cutout.data.y)
    grid = cutout.grid
    to_weather = Transformer.from_crs(excluder.crs, 4326, always_xy=True)
    cell_lookup = {(float(row.x), float(row.y)): i for i, row in grid.iterrows()}
    blocks = []
    if checkpoint is not None:
        checkpoint = Path(checkpoint)
        checkpoint.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(regions), batch_size):
        batch = projected.iloc[start : start + batch_size]
        cached = checkpoint / f"{start:06d}.npz" if checkpoint else None
        digest = cached.with_suffix(".json") if cached else None
        if cached and cached.exists() and digest.exists():
            if sha256(cached) != json.loads(digest.read_text())["sha256"]:
                raise ValueError(f"Land batch checksum mismatch: {cached}")
            block = sparse.load_npz(cached)
        else:
            rows = []
            for region, geometry in zip(batch.region, batch.geometry):
                # Expand only the mask envelope so partial boundary pixels whose
                # centres lie outside the production polygon are retained.
                envelope = gpd.GeoSeries(
                    [geometry.buffer(2 * excluder.res)], crs=excluder.crs
                )
                mask, transform = excluder.compute_shape_availability(envelope)
                rr, cc = np.nonzero(mask)
                px, py = transform * (cc + 0.5, rr + 0.5)
                interior = geometry.buffer(-np.sqrt(2) * excluder.res)
                inside = (
                    geometry_mask([interior], mask.shape, transform, invert=True)
                    if not interior.is_empty
                    else np.zeros(mask.shape, dtype=bool)
                )
                areas = np.full(len(rr), abs(transform.a * transform.e))
                edge = ~inside[rr, cc]
                pixels = shapely.box(
                    px[edge] - abs(transform.a) / 2,
                    py[edge] - abs(transform.e) / 2,
                    px[edge] + abs(transform.a) / 2,
                    py[edge] + abs(transform.e) / 2,
                )
                areas[edge] = shapely.area(shapely.intersection(pixels, geometry))
                keep = areas > 0
                px, py, areas = px[keep], py[keep], areas[keep] / 1e6
                lon, lat = to_weather.transform(px, py)
                # Grid coordinates are regular ERA5 centres, in cutout.grid order.
                xorder, yorder = np.argsort(xs), np.argsort(ys)
                ix = xorder[
                    np.searchsorted((xs[xorder][1:] + xs[xorder][:-1]) / 2, lon)
                ]
                iy = yorder[
                    np.searchsorted((ys[yorder][1:] + ys[yorder][:-1]) / 2, lat)
                ]
                dx = abs(xs[1] - xs[0]) if len(xs) > 1 else float(cutout.dx)
                dy = abs(ys[1] - ys[0]) if len(ys) > 1 else float(cutout.dy)
                if (np.abs(xs[ix] - lon) > dx / 2 + 1e-8).any() or (
                    np.abs(ys[iy] - lat) > dy / 2 + 1e-8
                ).any():
                    raise ValueError(
                        f"Weather grid does not cover eligible pixels: region {region}"
                    )
                columns = [
                    cell_lookup[(float(xs[x]), float(ys[y]))] for x, y in zip(ix, iy)
                ]
                row = sparse.csr_matrix(
                    (areas, (np.zeros(len(areas), dtype=int), columns)),
                    shape=(1, len(grid)),
                )
                if (
                    not np.isfinite(row.data).all()
                    or (row.data < 0).any()
                    or row.sum() > geometry.area / 1e6 + 1e-6
                ):
                    raise ValueError(
                        f"Invalid eligible area for region {region}: {row.sum()} km2; production {geometry.area / 1e6} km2"
                    )
                rows.append(row)
            block = sparse.vstack(rows, format="csr")
            if cached:
                temporary = cached.with_name(cached.stem + ".partial.npz")
                sparse.save_npz(temporary, block)
                temporary.replace(cached)
                manifest(digest, {"sha256": sha256(cached)})
        if block.shape != (len(batch), len(grid)):
            raise ValueError("Land checkpoint shape mismatch.")
        blocks.append(block)
        print(
            f"Land eligibility: {min(start + batch_size, len(regions))}/{len(regions)} regions (checkpointed={checkpoint is not None})",
            flush=True,
        )
    return sparse.vstack(blocks, format="csr")


def shared_eligible_weights(
    cutout, regions, excluder, land_cover, selected, checkpoint
):
    """Read/classify land cover and map native pixels once for both technologies."""
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from atlite.gis import projected_mask
    import rasterio
    import shapely
    from time import perf_counter

    projected = regions.to_crs(excluder.crs)
    xs, ys = np.asarray(cutout.data.x), np.asarray(cutout.data.y)
    grid = cutout.grid
    to_weather = Transformer.from_crs(excluder.crs, 4326, always_xy=True)
    cell_lookup = {(float(row.x), float(row.y)): i for i, row in grid.iterrows()}
    blocks = {tech: [] for tech in TECHS}
    checkpoint = Path(checkpoint)
    checkpoint.mkdir(parents=True, exist_ok=True)
    batch_size = int(selected["batch_size"])
    allowed = {
        tech: sorted(set(range(1, 20)) - set(selected[f"{tech}_excluded_codes"]))
        for tech in TECHS
    }
    with rasterio.open(land_cover) as raster:
        for start in range(0, len(regions), batch_size):
            started = perf_counter()
            batch = projected.iloc[start : start + batch_size]
            paths = {tech: checkpoint / f"{start:06d}_{tech}.npz" for tech in TECHS}
            record = checkpoint / f"{start:06d}.json"
            if record.exists():
                captured = json.loads(record.read_text())
                for tech, path in paths.items():
                    if not path.exists() or sha256(path) != captured["sha256"].get(
                        tech
                    ):
                        raise ValueError(f"Shared land batch checksum mismatch: {path}")
                    block = sparse.load_npz(path)
                    if block.shape != (len(batch), len(grid)):
                        raise ValueError("Shared land checkpoint shape mismatch.")
                    blocks[tech].append(block)
            else:
                rows = {tech: [] for tech in TECHS}
                for region, geometry in zip(batch.region, batch.geometry):
                    envelope = gpd.GeoSeries(
                        [geometry.buffer(2 * excluder.res)], crs=excluder.crs
                    )
                    common, transform = excluder.compute_shape_availability(envelope)
                    codes, code_transform = projected_mask(
                        raster,
                        envelope,
                        transform=transform,
                        shape=common.shape,
                        crs=excluder.crs,
                        nodata=0,
                        allow_no_overlap=False,
                    )
                    if code_transform != transform or codes.shape != common.shape:
                        raise ValueError(
                            f"Shared land raster alignment mismatch: region {region}"
                        )
                    masks = {
                        tech: common & np.isin(codes, values)
                        for tech, values in allowed.items()
                    }
                    mask = np.logical_or.reduce(list(masks.values()))
                    rr, cc = np.nonzero(mask)
                    px, py = transform * (cc + 0.5, rr + 0.5)
                    interior = geometry.buffer(-np.sqrt(2) * excluder.res)
                    inside = (
                        geometry_mask([interior], mask.shape, transform, invert=True)
                        if not interior.is_empty
                        else np.zeros(mask.shape, dtype=bool)
                    )
                    areas = np.full(len(rr), abs(transform.a * transform.e))
                    edge = ~inside[rr, cc]
                    pixels = shapely.box(
                        px[edge] - abs(transform.a) / 2,
                        py[edge] - abs(transform.e) / 2,
                        px[edge] + abs(transform.a) / 2,
                        py[edge] + abs(transform.e) / 2,
                    )
                    areas[edge] = shapely.area(shapely.intersection(pixels, geometry))
                    keep = areas > 0
                    selectors = {
                        tech: values[rr, cc][keep] for tech, values in masks.items()
                    }
                    px, py, areas = px[keep], py[keep], areas[keep] / 1e6
                    lon, lat = to_weather.transform(px, py)
                    # Grid coordinates are regular ERA5 centres, in cutout.grid order.
                    xorder, yorder = np.argsort(xs), np.argsort(ys)
                    ix = xorder[
                        np.searchsorted((xs[xorder][1:] + xs[xorder][:-1]) / 2, lon)
                    ]
                    iy = yorder[
                        np.searchsorted((ys[yorder][1:] + ys[yorder][:-1]) / 2, lat)
                    ]
                    dx = abs(xs[1] - xs[0]) if len(xs) > 1 else float(cutout.dx)
                    dy = abs(ys[1] - ys[0]) if len(ys) > 1 else float(cutout.dy)
                    if (np.abs(xs[ix] - lon) > dx / 2 + 1e-8).any() or (
                        np.abs(ys[iy] - lat) > dy / 2 + 1e-8
                    ).any():
                        raise ValueError(
                            f"Weather grid does not cover eligible pixels: region {region}"
                        )
                    columns = [
                        cell_lookup[(float(xs[x]), float(ys[y]))]
                        for x, y in zip(ix, iy)
                    ]
                    for tech in TECHS:
                        chosen = selectors[tech]
                        data = areas[chosen]
                        indices = np.asarray(columns, dtype=int)[chosen]
                        row = sparse.csr_matrix(
                            (data, (np.zeros(len(data), dtype=int), indices)),
                            shape=(1, len(grid)),
                        )
                        if (
                            not np.isfinite(row.data).all()
                            or (row.data < 0).any()
                            or row.sum() > geometry.area / 1e6 + 1e-6
                        ):
                            raise ValueError(
                                f"Invalid {tech} eligible area for region {region}: {row.sum()} km2; production {geometry.area / 1e6} km2"
                            )
                        rows[tech].append(row)
                for tech, path in paths.items():
                    block = sparse.vstack(rows[tech], format="csr")
                    temporary = path.with_name(path.stem + ".partial.npz")
                    sparse.save_npz(temporary, block)
                    temporary.replace(path)
                    blocks[tech].append(block)
                # Commit the pair only after both files are complete.
                manifest(
                    record,
                    {"sha256": {tech: sha256(path) for tech, path in paths.items()}},
                )
            print(
                f"Land eligibility: wind + solar, {min(start + batch_size, len(regions))}/{len(regions)} regions ({perf_counter() - started:.1f}s; checkpointed=True)",
                flush=True,
            )
    return {tech: sparse.vstack(parts, format="csr") for tech, parts in blocks.items()}


def build_land_weights(cutout, regions, settings, root, output, source_record):
    """Write masks/previews before physical conversion; capacity derives from same weights."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    output = Path(output) / "land_exclusion"
    output.mkdir(parents=True, exist_ok=True)
    selected = policy(settings)
    signature = identity(
        {
            "sources": source_record,
            "regions": [
                (str(r), g.wkb_hex) for r, g in zip(regions.region, regions.geometry)
            ],
            "grid": [g.wkb_hex for g in cutout.grid.geometry],
        }
    )
    record_path = output / "manifest.json"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if record["identity"] != signature:
            raise ValueError("Land cache identity mismatch.")
        for name, digest in record["sha256"].items():
            if sha256(output / name) != digest:
                raise ValueError(f"Land cache checksum mismatch: {name}")
        return {
            tech: sparse.load_npz(output / f"{tech}_area_weights.npz") for tech in TECHS
        }
    containers = exclusion_containers(settings, root, output, regions)
    print(
        f"Land eligibility: shared wind + solar pass, {len(regions)} regions",
        flush=True,
    )
    weights = shared_eligible_weights(
        cutout,
        regions,
        containers["common"],
        containers["land_cover"],
        selected,
        output / "batches" / signature / "shared",
    )
    rows = []
    audit = regions[["region", "geometry"]].copy().to_crs(3347)
    total = audit.area.to_numpy() / 1e6
    for technology, tech in TECHS.items():
        area = np.asarray(weights[technology].sum(axis=1)).ravel()
        if (area > total + 1e-6).any():
            raise ValueError(
                "Eligible area exceeds production area beyond raster tolerance."
            )
        audit[f"{technology}_eligible_km2"] = area
        audit[f"{technology}_eligible_fraction"] = np.minimum(area / total, 1)
        for region, value in zip(regions.region, area):
            rows.append(
                dict(
                    region=region,
                    tech=tech,
                    eligible_area_km2=value,
                    density_mw_per_km2=1000 / selected[f"{technology}_land_km2_per_gw"],
                    basis="Palmer-Wilson 2019 median footprint plus spacing; scenario eligibility",
                    source=f"land_exclusion/{signature}",
                )
            )
        sparse.save_npz(output / f"{technology}_area_weights.npz", weights[technology])
        fig, axes = plt.subplots(1, 2, figsize=(13, 6))
        audit.plot(
            ax=axes[0],
            column=f"{technology}_eligible_fraction",
            cmap="YlGn",
            vmin=0,
            vmax=1,
            legend=True,
        )
        axes[0].set_title(f"{technology.title()}: eligible fraction per model region")
        containers[technology].plot_shape_availability(
            regions.iloc[[0]].to_crs(3347).geometry, ax=axes[1]
        )
        axes[1].set_title(f"Applied exclusion mask — region {regions.region.iloc[0]}")
        axes[1].legend(
            handles=[
                Patch(color="green", label="Eligible"),
                Patch(
                    facecolor="white",
                    edgecolor="gray",
                    label="Excluded / outside region",
                ),
            ]
        )
        fig.suptitle("Applied land-exclusion scenario")
        fig.tight_layout()
        fig.savefig(output / f"{technology}_exclusions.png", dpi=150)
        plt.close(fig)
    pd.DataFrame(rows).to_csv(output / "regional_capacity_assumptions.csv", index=False)
    audit.to_file(
        output / "eligibility.gpkg", layer="eligibility", driver="GPKG", index=False
    )
    paths = [p for p in output.iterdir() if p.is_file() and p.name != "manifest.json"]
    manifest(
        record_path,
        {
            "identity": signature,
            "inputs": source_record,
            "region_order": regions.region.astype(str).tolist(),
            "weather_cell_order": "cutout.grid (y,x)",
            "zero_area_policy": "zero MW and zero CF",
            "mask_evaluation": "shared geometry exclusions, one land-cover read and one pixel mapping per region; separate technology code filters",
            "area_method": "native mask; exact production-boundary pixel intersections; weather cell by pixel centre",
            "sha256": {p.name: sha256(p) for p in paths},
        },
    )
    return weights
