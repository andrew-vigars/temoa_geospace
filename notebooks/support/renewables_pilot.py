"""Notebook 22 pilot helpers; Bronze acquisition delegates to geocanoe.acquisition.

Raw CDS payloads are immutable Bronze inputs. Weather preparation and Atlite
conversion are Silver. A strict insert-only SQL candidate is a separate adapter.
No function here merges a production model or calls the sibling package setup.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone
import calendar
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
import shapely

from geocanoe.acquisition.era5_renewables import (
    FEATURE_VARIABLES,
    acquire as acquire,
    build_request_plan,
)


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


def select_ontario(basemap, province_boundaries):
    """Centroid-select parent cells, clip their production footprints to ON."""
    cells = gpd.read_file(basemap, layer="regions").to_crs(3347)
    # Filter at the GDAL reader rather than loading Canada's detailed northern
    # boundaries just to discard them. PRUID is a documented Census string code.
    provinces = gpd.read_file(province_boundaries, where="PRUID = '35'").to_crs(3347)
    on = provinces.loc[provinces.PRUID.astype(str) == "35"].copy()
    if len(on) != 1 or not cells.region.is_unique:
        raise ValueError("Expected one Ontario boundary and unique parent region IDs.")
    boundary = on.geometry.iloc[0]
    shapely.prepare(boundary)
    cells = cells.loc[cells.centroid.map(boundary.covers)].copy()
    cells["parent_cell_area_m2"] = cells.area
    interior = cells.geometry.map(boundary.covers)
    cells.loc[~interior, "geometry"] = cells.loc[~interior].geometry.intersection(
        boundary
    )
    cells["on_fraction"] = cells.area / cells.parent_cell_area_m2
    cells["production_area_km2"] = cells.area / 1e6
    cells = cells.loc[cells.production_area_km2 > 0].set_index("region", drop=False)
    if cells.empty or not cells.geometry.is_valid.all():
        raise ValueError("Empty or invalid Ontario production footprints.")
    return cells, on


def request_plan(regions, weather_year, months=range(1, 13), dx=0.25):
    """One buffered ON bounding box, monthly features: 36 CDS requests/year."""
    if dx != 0.25:
        raise ValueError("Pilot pins the standard ERA5 0.25-degree grid.")
    xmin, ymin, xmax, ymax = regions.to_crs(4326).total_bounds
    west, south = np.floor(np.array([xmin, ymin]) / dx) * dx - dx
    east, north = np.ceil(np.array([xmax, ymax]) / dx) * dx + dx
    return build_request_plan(
        [float(north), float(west), float(south), float(east)],
        weather_year,
        months,
        dx,
    )


def snapshot_sources(root, sister, config):
    """Copy only named non-secret source artifacts, preserving original files."""
    root, sister = Path(root), Path(sister)
    inputs = {
        "generator_technologies.csv": sister / "input_files/generator_technologies.csv",
        "params.toml": sister / "input_files/params.toml",
        "currency_exchange.csv": sister / "input_files/currency_exchange.csv",
        "cad_inflation.csv": sister / "input_files/cad_inflation.csv",
        "ATBe.csv": root / "data_files/raw/atb/2024/v3.0.0/ATBe.csv",
        "atb_acquisition.json": root
        / "data_files/raw/atb/2024/v3.0.0/acquisition_manifest.json",
        "T1_curve.csv": root
        / "data_files/raw/atb/2024/v3.0.0/technology_evidence/2023NREL_Bespoke_6MW_170.csv",
        "T1_specs.yaml": root
        / "data_files/raw/atb/2024/v3.0.0/technology_evidence/2023NREL_Bespoke_6MW_170.yaml",
    }
    hashes = {key: sha256(path) for key, path in inputs.items()}
    dest = root / "data_files/raw/renewables/source_bundles" / identity(hashes)
    dest.mkdir(parents=True, exist_ok=True)
    for key, source in inputs.items():
        target = dest / key
        if not target.exists():
            shutil.copyfile(source, target)
        if sha256(target) != hashes[key]:
            raise ValueError(f"Snapshot checksum mismatch: {key}")
    rows = pd.read_csv(dest / "generator_technologies.csv").set_index("code")
    selected = rows.loc[["solar", "wind_onshore"]].copy()
    if not (dest / "manifest.json").exists():
        manifest(
            dest / "manifest.json",
            {
                "source_files": {key: str(path) for key, path in inputs.items()},
                "sha256": hashes,
                "selected_codes": list(selected.index),
                "atb_edition": 2024,
                "atb_revision": "v3.0.0",
            },
        )
    return dest, selected


def raw_dataset(payload, extraction_root):
    """CDS can return a NetCDF or a ZIP of NetCDFs; preserve raw bytes."""
    payload, extraction_root = Path(payload), Path(extraction_root)
    if not zipfile.is_zipfile(payload):
        with xr.open_dataset(payload, engine="netcdf4") as ds:
            return ds.load()
    extraction_root.mkdir(parents=True, exist_ok=True)
    datasets = []
    with zipfile.ZipFile(payload) as archive:
        members = archive.namelist()
        names = [Path(name).name for name in members]
        if len(names) != len(set(names)) or any(
            not name.endswith(".nc") for name in names
        ):
            raise ValueError("Expected uniquely named NetCDF files in CDS ZIP.")
        for name, basename in zip(members, names):
            target = extraction_root / basename
            with archive.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            with xr.open_dataset(target, engine="netcdf4") as ds:
                datasets.append(ds.load())
    return xr.merge(datasets, compat="no_conflicts", join="exact")


def prepare_weather(payloads, extraction_root):
    """Pin private weather-preparation functions to the inspected Atlite 0.7.0."""
    import atlite
    from atlite.datasets import era5

    if atlite.__version__ != "0.7.0":
        raise ValueError("Review preparation adapter before changing Atlite 0.7.0.")
    datasets = []
    for feature, payload in payloads.items():
        ds = raw_dataset(payload, Path(extraction_root) / feature)
        ds = era5._rename_and_clean_coords(ds).sortby(["y", "x", "time"])
        if feature == "wind":
            ds = era5.sanitize_wind(era5._process_wind(ds))
        elif feature == "influx":
            ds = era5.sanitize_influx(era5._process_influx(ds))
        else:
            ds = ds.rename(
                {
                    "t2m": "temperature",
                    "stl4": "soil temperature",
                    "d2m": "dewpoint temperature",
                }
            )
        datasets.append(ds)
    return xr.merge(datasets, compat="equals", join="exact")


def aggregation_matrix(cutout, regions):
    """Native Atlite fractional overlap in equal-area CRS; uniform area weights."""
    from atlite.gis import compute_indicatormatrix

    grid = cutout.grid.to_crs(3347)
    matrix = compute_indicatormatrix(
        grid, regions, orig_crs=3347, dest_crs=3347
    ).tocsr()
    area = grid.area.to_numpy() / 1e6
    coverage = (
        np.asarray(matrix @ area).ravel() / regions.production_area_km2.to_numpy()
    )
    # Area weights in cutout.grid order, deliberately independent of land eligibility.
    layout = xr.DataArray(
        area.reshape(len(cutout.data.y), len(cutout.data.x)),
        coords={"y": cutout.data.y, "x": cutout.data.x},
        dims=("y", "x"),
    )
    return matrix, layout, coverage


def t1_config(bundle):
    import yaml

    curve = pd.read_csv(Path(bundle) / "T1_curve.csv")
    specs = yaml.safe_load((Path(bundle) / "T1_specs.yaml").read_text())
    if (specs["turbine_class"], specs["rated_power"], specs["hub_height"]) != (
        "T1",
        6000,
        115,
    ):
        raise ValueError("Expected class-tagged T1 6 MW / 115 m metadata.")
    speeds = curve["Wind Speed [m/s]"].to_numpy(float)
    power = curve["Power [kW]"].to_numpy(float) / 1000
    return {
        "V": np.r_[speeds, speeds[-1]],
        "POW": np.r_[np.minimum(power, 6), 0],
        "P": 6.0,
        "hub_height": 115.0,
    }


def convert(cutout, regions, bundle, config):
    from atlite.resource import get_solarpanelconfig

    for key in ["wind_plant_loss", "pv_additional_loss"]:
        if not math.isfinite(config[key]) or not 0 <= config[key] < 1:
            raise ValueError(f"Invalid {key}.")
    if not math.isfinite(config["pv_ilr"]) or config["pv_ilr"] <= 0:
        raise ValueError("PV ILR must be positive.")
    if not 0 < config["inverter_efficiency"] <= 1:
        raise ValueError("Invalid inverter efficiency.")
    matrix, layout, coverage = aggregation_matrix(cutout, regions)
    if (
        not np.isfinite(coverage).all()
        or np.min(coverage) < 0.999
        or np.max(coverage) > 1.001
    ):
        raise ValueError(
            "Weather grid must cover every selected production polygon completely."
        )
    common = dict(
        matrix=matrix,
        index=pd.Index(regions.region, name="region"),
        layout=layout,
        per_unit=True,
        aggregate_time=None,
        dask_kwargs={"scheduler": "single-threaded"},
    )
    wind = cutout.wind(
        turbine=t1_config(bundle),
        add_cutout_windspeed=False,
        interpolation_method="logarithmic",
        **common,
    )
    # Convert and clip at weather-cell level before spatial averaging.
    panel = dict(get_solarpanelconfig("CSi"))
    panel["inverter_efficiency"] = 1.0
    solar_cell = cutout.pv(
        panel=panel,
        orientation={"slope": 0, "azimuth": 180},
        tracking="horizontal",
        aggregate_time=None,
        dask_kwargs={"scheduler": "single-threaded"},
    )
    solar_cell = (
        solar_cell
        * config["pv_ilr"]
        * config["inverter_efficiency"]
        * (1 - config["pv_additional_loss"])
    ).clip(max=1)
    weights = matrix.multiply(layout.values.ravel())
    denominator = np.asarray(weights.sum(axis=1)).ravel()
    solar = (
        weights
        @ solar_cell.transpose("time", "y", "x")
        .values.reshape(len(solar_cell.time), -1)
        .T
    )
    solar = xr.DataArray(
        solar / denominator[:, None],
        dims=("region", "time"),
        coords={"region": regions.region.to_numpy(), "time": solar_cell.time},
    )
    result = xr.Dataset(
        {"wind_cf": wind * (1 - config["wind_plant_loss"]), "solar_cf": solar}
    )
    values = result.to_array().values
    if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1 + 1e-8:
        raise ValueError("Invalid converted capacity factors.")
    result.attrs.update(
        timezone="UTC",
        spatial_weighting="Native Atlite overlap, equal-area uniform capacity density; no eligibility masks",
        configuration=json.dumps(config, sort_keys=True),
    )
    return result, coverage


def transform_month(item_group, bronze_root, silver_root, regions, bundle, config):
    import atlite

    silver_root = Path(silver_root)
    silver_root.mkdir(parents=True, exist_ok=True)
    month = item_group[0]["month"]
    payloads = {
        item["feature"]: Path(bronze_root) / f"{item['request_id']}.payload"
        for item in item_group
    }
    if set(payloads) != set(FEATURE_VARIABLES) or not all(
        p.is_file() for p in payloads.values()
    ):
        raise ValueError(
            f"Month {month}: all three Bronze feature payloads are required."
        )
    raw_hashes = {key: sha256(p) for key, p in payloads.items()}
    for item in item_group:
        record = json.loads(
            (Path(bronze_root) / f"{item['request_id']}.json").read_text()
        )
        if (
            record["sha256"] != raw_hashes[item["feature"]]
            or record["request"] != item["request"]
        ):
            raise ValueError("Bronze acquisition manifest mismatch.")
    # Silver identity incorporates all physical inputs; weather cache identity does not.
    region_hash = identity(
        [(r, geom.wkb_hex) for r, geom in zip(regions.region, regions.geometry)]
    )
    transform_id = identity(
        {
            "raw": raw_hashes,
            "config": config,
            "regions": region_hash,
            "bundle": sha256(Path(bundle) / "manifest.json"),
            "adapter": sha256(__file__),
        }
    )
    dest = silver_root / f"month_{month:02d}_{transform_id}.nc"
    if dest.exists():
        meta = json.loads(dest.with_suffix(".json").read_text())
        if meta["sha256"] != sha256(dest):
            raise ValueError("Silver checksum mismatch.")
        return dest
    extraction = silver_root / f"raw_extract_{transform_id}"
    weather_path = silver_root / f"prepared_weather_{transform_id}.nc"
    if weather_path.exists():
        # An interrupted conversion can reuse prepared weather; verify its raw
        # inputs again above and validate temporal coverage below.
        with xr.open_dataset(weather_path, engine="netcdf4") as cached_weather:
            weather = cached_weather.load()
    else:
        weather = prepare_weather(payloads, extraction)
    year = int(item_group[0]["request"]["year"][0])
    expected = pd.date_range(
        f"{year}-{month:02d}-01",
        periods=calendar.monthrange(year, month)[1] * 24,
        freq="h",
    )
    if not pd.DatetimeIndex(weather.time.values).equals(expected):
        raise ValueError("Incomplete or duplicate monthly weather timestamps.")
    cutout = atlite.Cutout(
        silver_root / f"prepared_{transform_id}.nc",
        module="era5",
        x=slice(float(weather.x.min()), float(weather.x.max())),
        y=slice(float(weather.y.min()), float(weather.y.max())),
        time=slice(expected[0], expected[-1]),
    )
    # Replacing the Dataset must retain Atlite's module/chunk metadata. Raw
    # ERA5 preparation (and older interrupted weather caches) lacks these attrs.
    weather.attrs = {
        **cutout.data.attrs,
        **weather.attrs,
        "module": "era5",
        "prepared_features": list(FEATURE_VARIABLES),
    }
    weather.to_netcdf(weather_path)
    cutout.data = weather
    result, coverage = convert(cutout, regions, bundle, config)
    result.to_netcdf(
        dest, encoding={key: {"zlib": True, "complevel": 4} for key in result.data_vars}
    )
    manifest(
        dest.with_suffix(".json"),
        {
            "sha256": sha256(dest),
            "raw_sha256": raw_hashes,
            "config": config,
            "region_geometry_identity": region_hash,
            "minimum_coverage": float(coverage.min()),
            "atlite_version": atlite.__version__,
            "helper_sha256": sha256(__file__),
            "prepared_weather_sha256": sha256(weather_path),
        },
    )
    weather.close()
    return dest


def annual_profiles(paths, year):
    # Region time-series are small compared with raw weather and retain hourly outputs.
    pieces = []
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            pieces.append(ds.load())
    ds = xr.concat(pieces, dim="time", join="exact").sortby("time")
    expected = pd.date_range(
        f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left"
    )
    if not pd.DatetimeIndex(ds.time.values).equals(expected):
        raise ValueError(
            "Annual export requires exactly one complete UTC weather year, without duplicates."
        )
    if not np.isfinite(ds.to_array().values).all():
        raise ValueError("Missing capacity factors cannot be silently averaged.")
    return ds, ds.mean("time", skipna=False).to_dataframe()[["wind_cf", "solar_cf"]]


def selected_costs(bundle, cost_year, target_currency_year=2020, currency_scale=1e6):
    """Explicit 2022 USD/kW -> target CAD/MW; separate from CANOE's 2021 setting."""
    bundle = Path(bundle)
    rows = pd.read_csv(bundle / "generator_technologies.csv").set_index("code")
    wanted = rows.loc[["wind_onshore", "solar"]]
    matches = []
    for chunk in pd.read_csv(bundle / "ATBe.csv", dtype=str, chunksize=100000):
        matches.append(
            chunk.loc[
                chunk.display_name.isin(wanted.atb_display_name)
                & chunk.scenario.eq("Moderate")
                & chunk.core_metric_case.eq("Market")
                & chunk.crpyears.eq("20")
                & chunk.core_metric_variable.eq(str(cost_year))
                & chunk.core_metric_parameter.isin(["OCC", "Fixed O&M"])
            ]
        )
    data = pd.concat(matches)
    exchange = pd.read_csv(bundle / "currency_exchange.csv", index_col=0)
    inflation = pd.read_csv(bundle / "cad_inflation.csv", index_col=0)
    factor = float(
        exchange.loc[2022, "USD"]
        * inflation.loc[2022, "gdp_deflator"]
        / (
            exchange.loc[target_currency_year, "CAD"]
            * inflation.loc[target_currency_year, "gdp_deflator"]
        )
    )
    if not math.isfinite(factor) or factor <= 0 or currency_scale <= 0:
        raise ValueError("Invalid currency conversion factor.")
    records = []
    for code, row in wanted.iterrows():
        costs = data.loc[data.display_name == row.atb_display_name]
        if len(costs) != 2 or costs.core_metric_parameter.nunique() != 2:
            raise ValueError(f"Missing/ambiguous ATB cost selectors: {code}")
        vals = costs.set_index("core_metric_parameter").value.astype(float)
        if not np.isfinite(vals.values).all() or (vals < 0).any():
            raise ValueError("Invalid ATB source costs.")
        records.append(
            {
                "code": code,
                "tech": row.base_tech,
                "atb_display_name": row.atb_display_name,
                "cost_year": cost_year,
                "invest": float(vals["OCC"]) * 1000 * factor / currency_scale,
                "fixed": float(vals["Fixed O&M"]) * 1000 * factor / currency_scale,
                "source_currency_year": 2022,
                "cad_factor": factor,
                "units": f"{'million ' if currency_scale == 1e6 else str(currency_scale) + '-scaled '}CAD{target_currency_year}/MW",
            }
        )
    return pd.DataFrame(records).set_index("code")


def sql_candidate(annual, costs, schema_path, dest, *, vintage=2025, period=2025):
    """Insert-only annual renewable candidate; target regions/commodities/time must exist.

    Annual techs need limit_annual_capacity_factor, not hourly CF-process rows.
    Capacity is MW, electricity MWh, and costs million CAD2020/MW by default.
    Never overrides incumbent ELC_GEN or target-global definitions.
    """
    if (
        annual.empty
        or not annual.index.is_unique
        or set(annual.columns) != {"wind_cf", "solar_cf"}
    ):
        raise ValueError(
            "Expected annual regional wind/solar CF table with unique region index."
        )
    if (
        not np.isfinite(annual.values).all()
        or not ((annual >= 0) & (annual <= 1)).all().all()
    ):
        raise ValueError("SQL CFs must be complete and bounded.")
    if (
        costs.loc["wind_onshore", "tech"] != "E_WND_ON"
        or costs.loc["solar", "tech"] != "E_SOL_PV"
    ):
        raise ValueError("Expected imported CANOE renewable technology identities.")
    schema = Path(schema_path).read_text(encoding="utf-8")
    connection = sqlite3.connect(":memory:")
    connection.executescript(schema)
    if connection.execute(
        "SELECT value FROM metadata WHERE element='DB_MAJOR'"
    ).fetchone() != (4,):
        raise ValueError("Expected Temoa v4 schema.")

    def quote(value):
        return connection.execute("SELECT quote(?)", (value,)).fetchone()[0]

    def insert(table, row):
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
        if not set(row).issubset(columns):
            raise ValueError(f"Schema mismatch: {table}")
        return (
            f'INSERT INTO "{table}" ('
            + ",".join(f'"{key}"' for key in row)
            + ") VALUES ("
            + ",".join(quote(v) for v in row.values())
            + ");"
        )

    lines = [
        "-- DRAFT Ontario annual resource candidate; inspect manifest before any merge.",
        "-- Capacity MW; activity MWh/year; no land/build caps, hourly adequacy, or incumbent replacement.",
        "-- Costs use explicitly labelled CAD scale; verify all target costs share that scale.",
        "BEGIN IMMEDIATE;",
        "CREATE TEMP TABLE _renewables_gate (ok INTEGER);",
        "CREATE TEMP TRIGGER _renewables_guard BEFORE INSERT ON _renewables_gate WHEN NEW.ok != 1 BEGIN SELECT RAISE(ROLLBACK, 'Renewables merge prerequisites failed'); END;",
    ]
    predicates = [
        "(SELECT value FROM metadata WHERE element='DB_MAJOR')=4",
        f"EXISTS(SELECT 1 FROM time_period WHERE period={int(period)})",
        f"EXISTS(SELECT 1 FROM time_period WHERE period={int(vintage)})",
        "EXISTS(SELECT 1 FROM commodity WHERE name='ethos')",
        "EXISTS(SELECT 1 FROM commodity WHERE name='elc')",
        "(SELECT count(*) FROM time_season)=1",
        "(SELECT count(*) FROM time_of_day)=1",
        "ABS((SELECT segment_fraction FROM time_season)-1)<1e-9",
        "ABS((SELECT hours FROM time_of_day)-24)<1e-9",
        "NOT EXISTS(SELECT 1 FROM technology WHERE tech IN ('E_WND_ON','E_SOL_PV'))",
    ]
    predicates.extend(
        f"EXISTS(SELECT 1 FROM region WHERE region={quote(str(region))})"
        for region in annual.index
    )
    lines.extend(
        "INSERT INTO _renewables_gate VALUES (CASE WHEN "
        + expr
        + " THEN 1 ELSE 0 END);"
        for expr in predicates
    )
    note = "Ontario pilot: annual resource availability; no ATB CF uplift; not an hourly adequacy representation"
    for code, field in [("wind_onshore", "wind_cf"), ("solar", "solar_cf")]:
        cost = costs.loc[code]
        tech = str(cost.tech)
        lines.append(
            insert(
                "technology",
                {
                    "tech": tech,
                    "flag": "p",
                    "annual": 1,
                    "unlim_cap": 0,
                    "description": f"Ontario pilot {code}; imported CANOE technology identity",
                },
            )
        )
        for region, row in annual.iterrows():
            base = {"region": str(region), "tech": tech}
            lines.extend(
                [
                    insert(
                        "efficiency",
                        {
                            **base,
                            "input_comm": "ethos",
                            "vintage": vintage,
                            "output_comm": "elc",
                            "efficiency": 1.0,
                            "notes": note,
                        },
                    ),
                    insert(
                        "capacity_to_activity",
                        {
                            **base,
                            "c2a": 8760.0,
                            "units": "MWh/(MW year)",
                            "notes": "365-day model representative year; weather-year CF independently averaged",
                        },
                    ),
                    insert(
                        "lifetime_tech",
                        {
                            **base,
                            "lifetime": 30.0,
                            "units": "years",
                            "notes": "ATB utility wind/PV technical-life assumption",
                        },
                    ),
                    insert(
                        "cost_invest",
                        {
                            **base,
                            "vintage": vintage,
                            "cost": float(cost.invest),
                            "units": str(cost.units),
                            "notes": f"ATB 2024 v3 OCC, cost year {int(cost.cost_year)}, source USD2022; no grid connection cost",
                        },
                    ),
                    insert(
                        "cost_fixed",
                        {
                            **base,
                            "period": period,
                            "vintage": vintage,
                            "cost": float(cost.fixed),
                            "units": str(cost.units) + "/year",
                            "notes": "ATB fixed O&M; currency corrected from USD2022",
                        },
                    ),
                    insert(
                        "limit_annual_capacity_factor",
                        {
                            "region": str(region),
                            "tech_or_group": tech,
                            "vintage": vintage,
                            "output_comm": "elc",
                            "operator": "le",
                            "factor": float(row[field]),
                            "notes": note,
                        },
                    ),
                ]
            )
    lines += [
        "DROP TRIGGER _renewables_guard;",
        "DROP TABLE _renewables_gate;",
        "COMMIT;",
    ]
    text = "\n".join(lines) + "\n"
    # Execute on a disposable schema with only required target dimensions.
    for number, year in enumerate(sorted({period, vintage}), 1):
        connection.execute(
            "INSERT INTO time_period(sequence,period,flag) VALUES(?,?,'f')",
            (number, year),
        )
    connection.execute("INSERT INTO time_season VALUES(1,'S',1,'annual')")
    connection.execute("INSERT INTO time_of_day VALUES(1,'D',24,'annual')")
    connection.executemany(
        "INSERT INTO commodity(name,flag) VALUES(?,?)", [("ethos", "s"), ("elc", "a")]
    )
    connection.executemany(
        "INSERT INTO region(region) VALUES(?)", [(str(r),) for r in annual.index]
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(text)
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Foreign-key violations in candidate SQL.")
    expected = len(annual) * 2
    if (
        connection.execute(
            "SELECT count(*) FROM limit_annual_capacity_factor"
        ).fetchone()[0]
        != expected
    ):
        raise ValueError("Candidate SQL row-count mismatch.")
    connection.close()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return {
        "validated_cf_rows": expected,
        "schema_sha256": sha256(schema_path),
        "sql_sha256": sha256(dest),
        "capacity_unit": "MW",
        "electricity_unit": "MWh",
        "production_merged": False,
    }
