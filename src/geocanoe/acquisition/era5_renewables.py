"""Immutable ERA5 Bronze acquisition; no Atlite conversion or CDS calls on import.

Payload IDs match Notebook 22. Cached files are verified before CDS is contacted.
Credentials are read only by cdsapi through its normal user configuration.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tomllib
import time
import shutil
from contextlib import contextmanager

from tqdm.auto import tqdm

from geocanoe.paths import find_project_root

FEATURE_VARIABLES = {
    "wind": [
        "10m_u_component_of_wind",
        "10m_v_component_of_wind",
        "100m_u_component_of_wind",
        "100m_v_component_of_wind",
        "forecast_surface_roughness",
    ],
    "influx": [
        "surface_net_solar_radiation",
        "surface_solar_radiation_downwards",
        "toa_incident_solar_radiation",
        "total_sky_direct_solar_radiation_at_surface",
    ],
    "temperature": [
        "2m_temperature",
        "soil_temperature_level_4",
        "2m_dewpoint_temperature",
    ],
}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def manifest(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"written_utc": datetime.now(timezone.utc).isoformat(), **value}
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def build_request_plan(area, weather_year, months=range(1, 13), dx=0.25):
    """Plan monthly hourly ERA5 requests for [north, west, south, east]."""
    if dx != 0.25:
        raise ValueError("ERA5 acquisition pins the standard 0.25-degree grid.")
    if (
        isinstance(weather_year, bool)
        or not isinstance(weather_year, int)
        or not 1940 <= weather_year <= datetime.now(timezone.utc).year
    ):
        raise ValueError("Invalid ERA5 weather year.")
    if len(area) != 4 or not all(math.isfinite(v) for v in area):
        raise ValueError("Area must contain four finite coordinates.")
    north, west, south, east = map(float, area)
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("Invalid north/west/south/east bounds.")
    months = list(months)
    if (
        not months
        or len(set(months)) != len(months)
        or any(
            isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 12
            for m in months
        )
    ):
        raise ValueError("Months must be unique integers from 1 to 12.")
    rows = []
    for month in months:
        days = calendar.monthrange(weather_year, month)[1]
        for feature, variables in FEATURE_VARIABLES.items():
            request = {
                "product_type": ["reanalysis"],
                "variable": variables,
                "year": [str(weather_year)],
                "month": [f"{month:02d}"],
                "day": [f"{d:02d}" for d in range(1, days + 1)],
                "time": [f"{h:02d}:00" for h in range(24)],
                "area": [north, west, south, east],
                "grid": [dx, dx],
                "data_format": "netcdf",
                "download_format": "unarchived",
            }
            cells = (round((east - west) / dx) + 1) * (round((north - south) / dx) + 1)
            rows.append(
                {
                    "month": month,
                    "feature": feature,
                    "request_id": identity(request),
                    "hours": days * 24,
                    "weather_cells_estimate": cells,
                    "float32_bytes_estimate": cells * days * 24 * len(variables) * 4,
                    "request": request,
                }
            )
    return rows


def cache_verified(item, bronze_root):
    """Return False for missing payloads; reject corrupt/unmanifested caches."""
    dest = Path(bronze_root) / f"{item['request_id']}.payload"
    record = dest.with_suffix(".json")
    if not dest.is_file():
        return False
    if not record.is_file():
        raise ValueError(f"Unmanifested payload: {dest}")
    cached = json.loads(record.read_text(encoding="utf-8"))
    if (
        not dest.stat().st_size
        or cached.get("request") != item["request"]
        or cached.get("sha256") != sha256(dest)
    ):
        raise ValueError(f"Invalid Bronze cache: {dest}")
    return True


@contextmanager
def acquisition_lock(bronze_root):
    """Reject overlapping writers; a killed process leaves a lock for inspection."""
    lock = Path(bronze_root) / ".acquisition.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"ERA5 acquisition lock exists: {lock}. Check for an active downloader; "
            "remove the lock only if that process has stopped."
        ) from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(
                f"pid={os.getpid()} started_utc={datetime.now(timezone.utc).isoformat()}"
            )
        yield
    finally:
        lock.unlink(missing_ok=True)


def canoe_reference(config, root):
    """Validate weather alignment and retain ATB edition/row years separately."""
    if "canoe" not in config:
        return None
    import csv

    spec = config["canoe"]
    params_path = (root / spec["params"]).resolve()
    generators_path = (root / spec["generators"]).resolve()
    params = tomllib.loads(params_path.read_text(encoding="utf-8"))
    if config["weather_year"] != params["weather_year"]:
        raise ValueError(
            "ERA5 weather_year must match CANOE params.weather_year; "
            "ATB edition and atb_min_year are separate technology/economic years."
        )
    if params["atb"]["year"] != spec["atb_edition"]:
        raise ValueError("CANOE ATB edition differs from pinned Bronze configuration.")
    with generators_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = {r["code"]: r for r in csv.DictReader(stream)}
    selected = [rows[code] for code in ("solar", "wind_onshore")]
    if any(not row["atb_display_name"] for row in selected):
        raise ValueError("CANOE solar/wind rows need ATB display names.")
    return {
        "weather_year": params["weather_year"],
        "atb_edition": params["atb"]["year"],
        "atb_url": params["atb"]["url"],
        "selected_rows": selected,
        "year_basis": "CANOE params.weather_year; ATB edition/minimum cost year are independent",
        "source_files": {
            "params.toml": str(params_path),
            "generator_technologies.csv": str(generators_path),
        },
        "source_sha256": {
            "params.toml": sha256(params_path),
            "generator_technologies.csv": sha256(generators_path),
        },
    }


def acquire(plan, bronze_root, max_new_requests=None, *, request_interval_seconds=1):
    """Sequential resumable acquisition with bounded per-operation HTTP retries."""

    bronze_root = Path(bronze_root)
    bronze_root.mkdir(parents=True, exist_ok=True)
    if max_new_requests is not None and (
        isinstance(max_new_requests, bool)
        or not isinstance(max_new_requests, int)
        or max_new_requests < 0
    ):
        raise ValueError(
            "max_new_requests must be None (all missing) or a nonnegative integer."
        )
    # Validate the entire plan before any network calls.
    pending = []
    for item in tqdm(plan, desc="ERA5: verifying local cache", unit="request"):
        if not cache_verified(item, bronze_root):
            pending.append(item)
    limit = len(pending) if max_new_requests is None else max_new_requests
    print(
        f"ERA5: {len(plan) - len(pending)}/{len(plan)} cached; {len(pending)} missing; "
        f"this run will acquire up to {min(limit, len(pending))}.",
        flush=True,
    )
    if not math.isfinite(request_interval_seconds) or request_interval_seconds < 0:
        raise ValueError("Request interval must be finite and nonnegative.")
    downloaded = 0
    if not pending or limit == 0:
        print("ERA5: no CDS calls required.", flush=True)
        return 0
    with acquisition_lock(bronze_root):
        for item in tqdm(
            pending[:limit], desc="ERA5: acquiring missing weather", unit="request"
        ):
            dest = bronze_root / f"{item['request_id']}.payload"
            record = dest.with_suffix(".json")
            if cache_verified(item, bronze_root):
                continue
            if downloaded:
                time.sleep(request_interval_seconds)
            print(
                f"ERA5: requesting {item['request']['year'][0]}-{item['month']:02d} "
                f"{item['feature']} ({item['request_id']}); CDS queue/status follows.",
                flush=True,
            )
            import cdsapi

            temporary = dest.with_suffix(".part")
            try:
                cdsapi.Client(retry_max=3, sleep_max=30, timeout=60).retrieve(
                    "reanalysis-era5-single-levels", item["request"]
                ).download(str(temporary))
                if not temporary.stat().st_size:
                    raise ValueError("Empty CDS payload.")
                os.replace(temporary, dest)
                manifest(
                    record,
                    {
                        "dataset": "reanalysis-era5-single-levels",
                        "request": item["request"],
                        "feature": item["feature"],
                        "month": item["month"],
                        "sha256": sha256(dest),
                        "bytes": dest.stat().st_size,
                    },
                )
                downloaded += 1
                print(
                    f"ERA5: saved and verified {dest.name} "
                    f"({dest.stat().st_size / 1e6:.1f} MB).",
                    flush=True,
                )
            finally:
                temporary.unlink(missing_ok=True)
    print(
        f"ERA5: downloaded {downloaded}; remaining {len(pending) - downloaded}.",
        flush=True,
    )
    return downloaded


def boundary_extent(config, root):
    """Use the complete Census boundary, snapped outward with one grid-cell buffer."""
    boundary = config.get("boundary")
    if boundary:
        if "boundary_file" in config or "area" in config:
            raise ValueError(
                "Choose [boundary], boundary_file, or area, not multiple extents."
            )
        kind = boundary["kind"]
        if kind not in {"digital", "cartographic"}:
            raise ValueError("boundary.kind must be digital or cartographic.")
        config = {**config, "boundary_file": boundary[f"{kind}_file"]}
    if "boundary_file" not in config:
        return config["area"], None
    if "area" in config:
        raise ValueError("Choose boundary_file or area, not both.")
    import geopandas as gpd

    path = (root / config["boundary_file"]).resolve()
    print(
        f"ERA5: reading full Census boundary {path.name} (all provinces/territories).",
        flush=True,
    )
    boundaries = gpd.read_file(path)
    if (
        boundaries.empty
        or boundaries.crs is None
        or boundaries.geometry.is_empty.any()
        or boundaries.geometry.isna().any()
    ):
        raise ValueError(
            "Census boundary must contain nonempty georeferenced geometries."
        )
    west, south, east, north = boundaries.to_crs(4326).total_bounds
    dx = config.get("grid_degrees", 0.25)
    if dx != 0.25:
        raise ValueError("ERA5 acquisition pins the standard 0.25-degree grid.")
    buffer_cells = config.get("buffer_cells", 1)
    if (
        isinstance(buffer_cells, bool)
        or not isinstance(buffer_cells, int)
        or buffer_cells < 1
    ):
        raise ValueError("buffer_cells must be an integer of at least one.")
    buffer = buffer_cells * dx
    area = [
        min(90.0, math.ceil(north / dx) * dx + buffer),
        max(-180.0, math.floor(west / dx) * dx - buffer),
        max(-90.0, math.floor(south / dx) * dx - buffer),
        min(180.0, math.ceil(east / dx) * dx + buffer),
    ]
    # A shapefile's CRS and feature attributes are part of its source identity.
    sources = [path]
    if path.suffix.lower() == ".shp":
        sources = [
            p
            for p in path.parent.glob(path.stem + ".*")
            if p.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"}
        ]
    return area, {
        "boundary_file": str(path),
        "feature_count": len(boundaries),
        "source_crs": str(boundaries.crs),
        "sha256": {p.name: sha256(p) for p in sources},
        "area": area,
        "buffer_degrees": buffer,
        "boundary_kind": boundary["kind"] if boundary else "explicit_file",
        "census_year": boundary.get("census_year") if boundary else None,
        "selection": "full boundary extent; outward grid snapping; polygon clipping deferred to Silver",
    }


def acquire_era5_renewables(
    config_path=None, *, allow_download=False, max_new_requests=None
):
    """Write a reviewed request plan and optionally acquire missing raw payloads.

    Default operation is offline. No overwrite flag can bypass cache validation.
    """
    root = find_project_root()
    config_path = Path(config_path) if config_path else root / "config/era5/canada.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    reference = canoe_reference(config, root)
    area, spatial_reference = boundary_extent(config, root)
    print(
        f"ERA5: planning weather year {config['weather_year']} for {area}.",
        flush=True,
    )
    plan = build_request_plan(
        area, config["weather_year"], config["months"], config.get("grid_degrees", 0.25)
    )
    print(
        f"ERA5: {len(plan)} sequential requests; estimated uncompressed float32 variables "
        f"{sum(item['float32_bytes_estimate'] for item in plan) / 1e9:.1f} GB "
        "(actual download and disk usage differ).",
        flush=True,
    )
    bronze = Path(config["raw_dir"])
    if not bronze.is_absolute():
        bronze = root / bronze
    verified = sum(
        cache_verified(item, bronze)
        for item in tqdm(plan, desc="ERA5: checking stage cache", unit="request")
    )
    if reference:
        bundle = bronze / "technology_references" / identity(reference["source_sha256"])
        bundle.mkdir(parents=True, exist_ok=True)
        for name, source in reference["source_files"].items():
            target = bundle / name
            if not target.exists():
                shutil.copyfile(source, target)
            if sha256(target) != reference["source_sha256"][name]:
                raise ValueError(f"Invalid CANOE reference snapshot: {target}")
        manifest(bundle / "manifest.json", reference)
        print(
            f"ERA5: aligned with CANOE weather_year={reference['weather_year']}; "
            f"ATB edition={reference['atb_edition']} (separate year).",
            flush=True,
        )
    # A plan is local provenance, not a claim that all downloads are complete.
    manifest(
        bronze / "request_plan.json",
        {
            "dataset": "reanalysis-era5-single-levels",
            "plan": plan,
            "canoe_reference": reference,
            "spatial_reference": spatial_reference,
            "acquisition_config": {
                "path": str(config_path.resolve()),
                "sha256": sha256(config_path),
                "settings": config,
            },
        },
    )
    budget = (
        config.get("max_new_requests_per_run")
        if max_new_requests is None
        else max_new_requests
    )
    if not allow_download:
        print(
            "ERA5: offline verification only; use --era5-download to acquire missing requests.",
            flush=True,
        )
    downloaded = (
        acquire(
            plan,
            bronze,
            budget,
            request_interval_seconds=config.get("request_interval_seconds", 1),
        )
        if allow_download
        else 0
    )
    result = {
        "planned": len(plan),
        "cached": verified,
        "downloaded": downloaded,
        "remaining": len(plan) - verified - downloaded,
        "raw_dir": str(bronze),
    }
    print(result)
    return result
