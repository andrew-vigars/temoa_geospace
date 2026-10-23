"""Configurable ERA5/Atlite Silver resource profiles and annual Temoa input bundles."""

from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from tqdm.auto import tqdm

from geocanoe.acquisition.era5_renewables import (
    FEATURE_VARIABLES,
    cache_verified,
    identity,
    manifest,
    sha256,
)
from geocanoe.config import load_geospatial_build_config
from geocanoe.geospatial.basemaps import basemap_key
from geocanoe.costs.economics import conversion_basis
from geocanoe.paths import find_project_root
from geocanoe.preprocessing.renewables_capacity import capacity_bounds
from geocanoe.regions import PROVINCE_NAME_TO_CODE
from geocanoe.preprocessing.atb_wind import (
    canoe_wind_mapping,
    resolve_wind_crosswalk,
    wind_mapping,
)

try:
    import xarray as xr
    from geocanoe.preprocessing import renewables_weather as physical
except ImportError:
    # Stage registration must not require optional weather libraries when disabled.
    xr = None
    physical = None


def validate_resolutions(resolutions):
    if not resolutions or any(not np.isfinite(r) or r < 25 for r in resolutions):
        raise ValueError(
            "Renewable Silver requires projected basemaps >=25 km; no validated subgrid disaggregation is available."
        )


def production_regions(basemap, boundary, provinces):
    """Keep parent region IDs and clip production geometry to selected jurisdictions."""
    cells = gpd.read_file(basemap, layer="regions").to_crs(3347)
    if not cells.region.is_unique:
        raise ValueError("Basemap region IDs must be unique.")
    if "resolution_km" not in cells or cells.resolution_km.min() < 25:
        raise ValueError(
            "Basemap must declare a projected resolution of at least 25 km."
        )
    # Filter at the reader: detailed northern coastlines are expensive to load.
    names = [name for name, code in PROVINCE_NAME_TO_CODE.items() if code in provinces]
    where = (
        "PRENAME IN (" + ",".join("'" + n.replace("'", "''") + "'" for n in names) + ")"
    )
    borders = gpd.read_file(boundary, where=where).to_crs(3347)
    selected_codes = set(borders.PRENAME.map(PROVINCE_NAME_TO_CODE))
    if selected_codes != set(provinces):
        raise ValueError("Census boundary does not cover the selected province set.")
    geometry = (
        borders.geometry.iloc[0] if len(borders) == 1 else borders.geometry.union_all()
    )
    shapely.prepare(geometry)
    cells = cells.loc[cells.centroid.map(geometry.covers)].copy()
    interior = cells.geometry.map(geometry.covers)
    cells.loc[~interior, "geometry"] = cells.loc[~interior].geometry.intersection(
        geometry
    )
    cells["production_area_km2"] = cells.area / 1e6
    cells = cells.loc[cells.production_area_km2 > 0].set_index("region", drop=False)
    if cells.empty or not cells.is_valid.all():
        raise ValueError("Empty/invalid renewable production polygons.")
    return cells, borders


def select_costs(settings, root):
    """Resolve exact ATB 2025 selectors; retain native USD2023 and audit source rows."""
    if (
        settings.get("resource_representation", "one_option_per_region")
        != "one_option_per_region"
    ):
        raise ValueError(
            "Only one wind and one solar option per region is currently supported."
        )
    path = root / settings["atb_subset"]
    audit = json.loads((root / settings["atb_selection_manifest"]).read_text())
    if audit["output_sha256"] != sha256(path) or audit["edition"] != 2025:
        raise ValueError("Expected verified ATB 2025 subset.")
    if (settings["source_currency"], settings["source_currency_year"]) != ("USD", 2023):
        raise ValueError("ATB 2025 costs must be identified as native USD2023.")
    master = pd.read_csv(path, dtype=str, keep_default_na=False)
    crosswalk = pd.read_csv(
        root / settings["technology_crosswalk"], dtype=str, keep_default_na=False
    )
    if set(crosswalk.tech) != {"WIND_GEN", "SOLAR_GEN"} or not crosswalk.tech.is_unique:
        raise ValueError("Expected unique wind and solar crosswalk rows.")
    mapping = wind_mapping(master)
    policy = settings.get("wind_mapping", {})
    if policy.get("mode", "canoe_representative") != "canoe_representative":
        raise ValueError(
            "Only explicit CANOE representative wind mapping is supported."
        )
    representatives = canoe_wind_mapping(
        mapping, policy.get("classes", {"T1": 7, "T2": 8, "T3": 9})
    )
    reference = crosswalk.loc[crosswalk.tech == "WIND_GEN", "physical_reference"].iloc[
        0
    ]
    turbine_id = int(reference.split("_")[0].removeprefix("T"))
    representative = representatives.loc[
        representatives.turbine_technology == turbine_id
    ]
    if len(representative) != 1:
        raise ValueError("Physical turbine is not included in the CANOE mapping.")
    crosswalk = resolve_wind_crosswalk(
        crosswalk, mapping, int(representative.resource_class.iloc[0])
    )
    crosswalk.attrs["atb_wind_mapping"] = mapping
    crosswalk.attrs["canoe_wind_mapping"] = representatives
    print(
        "Renewables: using CANOE representative wind mapping "
        + ", ".join(
            f"T{r.turbine_technology}/Class{r.resource_class}"
            for r in representatives.itertuples()
        )
        + "; no wind-speed class assignment.",
        flush=True,
    )
    selected = []
    for _, tech in crosswalk.iterrows():
        mask = master.core_metric_variable == str(settings["cost_projection_year"])
        for col in [
            "technology",
            "display_name",
            "scenario",
            "core_metric_case",
            "tax_credit_case",
            "crpyears",
        ]:
            mask &= master[col] == tech[col]
        for metric, unit in [("OCC", "$/kW"), ("Fixed O&M", "$/kW-yr")]:
            rows = master.loc[mask & (master.core_metric_parameter == metric)].copy()
            if len(rows) != 1 or rows.units.iloc[0] != unit:
                raise ValueError(
                    f"ATB selector must resolve exactly one {metric} row with {unit}: {tech.tech}"
                )
            value = float(rows.value.iloc[0])
            if not np.isfinite(value) or value <= 0:
                raise ValueError("Invalid ATB cost value.")
            rows["tech"] = tech.tech
            rows["value_per_MW"] = value * 1000
            selected.append(rows)
    return crosswalk, pd.concat(selected, ignore_index=True)


def monthly_weather(items, bronze, cache, bounds, year, engine="netcdf4"):
    """Cache prepared regional weather independently of output resolution/technology."""
    raw = {
        item["feature"]: sha256(bronze / f"{item['request_id']}.payload")
        for item in items
    }
    key = identity(
        {
            "raw": raw,
            "bounds": bounds,
            "adapter": sha256(physical.__file__),
            "atlite": "0.7.0",
        }
    )
    path = cache / f"weather_{key}.nc"
    sidecar = path.with_suffix(".json")
    if path.exists():
        if not sidecar.exists() or json.loads(sidecar.read_text())["sha256"] != sha256(
            path
        ):
            raise ValueError("Prepared weather cache checksum mismatch.")
        print(f"Renewables: reusing weather {path.name}", flush=True)
        with xr.open_dataset(path, engine=engine) as ds:
            weather = ds.load()
    else:
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="era5_extract_", dir=cache) as extraction:
            weather = physical.prepare_weather(
                {i["feature"]: bronze / f"{i['request_id']}.payload" for i in items},
                extraction,
                bounds,
                engine,
            )
        weather.attrs.update(module="era5", prepared_features=list(FEATURE_VARIABLES))
        temporary = path.with_suffix(".part")
        weather.to_netcdf(temporary, engine=engine)
        temporary.replace(path)
        manifest(sidecar, {"sha256": sha256(path), "raw_sha256": raw, "bounds": bounds})
    expected = pd.date_range(
        f"{year}-{items[0]['month']:02d}-01",
        periods=calendar.monthrange(year, items[0]["month"])[1] * 24,
        freq="h",
    )
    if not pd.DatetimeIndex(weather.time.values).equals(expected):
        raise ValueError("Weather month must contain every UTC hour exactly once.")
    return weather, path


def gold_tables(annual, crosswalk, costs, settings, output):
    """Annual Temoa v4 CSV inputs; currency reconciliation is explicit downstream."""
    output.mkdir(parents=True, exist_ok=True)
    currency = {"target_currency": "USD2023", "multiplier": 1.0}
    if "currency_basis" in settings:
        path = find_project_root() / settings["currency_basis"]
        source = tomllib.loads(path.read_text())
        if (source["source_currency"], source["source_year"]) != ("USD", 2023):
            raise ValueError("Currency source must match ATB 2025 USD2023.")
        currency = conversion_basis(
            source["source_currency"], source["source_year"],
            source.get("source_scale", 1.0), config=source["economic_config"],
        )
    cost_currency = currency["target_currency"]
    period, vintage = settings["model_period"], settings["vintage"]
    tables = {
        name: []
        for name in [
            "technology",
            "efficiency",
            "capacity_to_activity",
            "lifetime_tech",
            "limit_annual_capacity_factor",
            "cost_invest",
            "cost_fixed",
            "cost_variable",
        ]
    }
    for _, tech in crosswalk.iterrows():
        field = "wind_cf" if tech.tech == "WIND_GEN" else "solar_cf"
        investment = float(
            costs.loc[
                (costs.tech == tech.tech) & (costs.core_metric_parameter == "OCC"),
                "value_per_MW",
            ].iloc[0]
        )
        fixed = float(
            costs.loc[
                (costs.tech == tech.tech)
                & (costs.core_metric_parameter == "Fixed O&M"),
                "value_per_MW",
            ].iloc[0]
        )
        investment *= currency["multiplier"]
        fixed *= currency["multiplier"]
        tables["technology"].append(
            {
                "tech": tech.tech,
                "flag": "p",
                "annual": 1,
                "exchange": 0,
                "unlim_cap": 0,
                "sector": "electricity",
                "description": f"ERA5/Atlite {tech.technology}; area-weighted resource baseline",
            }
        )
        for region, row in annual.iterrows():
            base = {"region": str(region), "tech": tech.tech}
            tables["efficiency"].append(
                {
                    **base,
                    "vintage": vintage,
                    "input_comm": "ethos",
                    "output_comm": "elc",
                    "efficiency": 1.0,
                }
            )
            tables["capacity_to_activity"].append(
                {**base, "c2a": 8760.0, "units": "MWh/(MW year)"}
            )
            tables["lifetime_tech"].append(
                {**base, "lifetime": float(tech.lifetime_years), "units": "years"}
            )
            tables["limit_annual_capacity_factor"].append(
                {
                    "region": str(region),
                    "tech_or_group": tech.tech,
                    "vintage": vintage,
                    "output_comm": "elc",
                    "operator": "le",
                    "factor": float(row[field]),
                }
            )
            tables["cost_invest"].append(
                {
                    **base,
                    "vintage": vintage,
                    "cost": investment,
                    "units": f"{cost_currency}/MW",
                }
            )
            tables["cost_fixed"].append(
                {
                    **base,
                    "period": period,
                    "vintage": vintage,
                    "cost": fixed,
                    "units": f"{cost_currency}/MW/year",
                }
            )
            tables["cost_variable"].append(
                {
                    **base,
                    "period": period,
                    "vintage": vintage,
                    "cost": 0.0,
                    "units": f"{cost_currency}/MWh",
                }
            )
    bounds_config = settings.get("capacity_limits", {})
    capacity_input = None
    if bounds_config.get("enabled", False):
        capacity_input = find_project_root() / bounds_config["input_csv"]
        rows, audit = capacity_bounds(capacity_input, annual.index, period)
        tables["limit_capacity"] = rows
        audit.to_csv(output / "capacity_limit_audit.csv", index=False)
        print(
            f"Renewables: exported {len(rows)} explicit regional MW capacity bounds.",
            flush=True,
        )
    for name, rows in tables.items():
        pd.DataFrame(rows).to_csv(output / f"{name}.csv", index=False)
    manifest(
        output / "contract.json",
        {
            "schema": "Temoa v4 annual representation",
            "capacity_unit": "MW",
            "activity_unit": "MWh/year",
            "currency": cost_currency,
            "currency_conversion": currency,
            "model_hours": 8760,
            "capacity_bounds": {
                "enabled": capacity_input is not None,
                "input_sha256": sha256(capacity_input) if capacity_input else None,
                "method": "eligible_area_km2 * density_mw_per_km2",
            },
            "cost_projection_year": settings["cost_projection_year"],
            "weather_year": settings["weather_year"],
            "merge_ready": False,
            "requirements": [
                "Verify target costs share the declared CAD2020 dollar scale",
                "Configured legacy policy: replace ELC_GEN; require complete node coverage",
                "Review land eligibility and capacity bounds",
                "Annual representation does not model hourly adequacy/storage",
            ],
            "table_sha256": {name: sha256(output / f"{name}.csv") for name in tables},
        },
    )
    return tables


def legacy_comparison(regions, annual, source, output):
    """Audit new resource profiles against legacy site coordinates without inventing potential."""
    if not Path(source).is_file():
        print(
            "Renewables: legacy sites_full.csv absent; optional comparison skipped.",
            flush=True,
        )
        return
    sites = pd.read_csv(source)
    points = gpd.GeoDataFrame(
        sites, geometry=gpd.points_from_xy(sites.lon, sites.lat), crs=4326
    ).to_crs(regions.crs)
    mapping = gpd.sjoin(
        points,
        regions[["region", "geometry"]].reset_index(drop=True),
        how="left",
        predicate="within",
    )
    mapping = mapping.join(annual, on="region")
    pd.DataFrame(mapping.drop(columns="geometry")).to_csv(
        output / "legacy_sites_resource_audit.csv", index=False
    )


def resource_summary(annual, crosswalk, tables, settings, output):
    """Successor to legacy site ranking; diagnostic LCOE is not an extra Gold cost."""
    rate = settings.get("lcoe_discount_rate", 0.03)
    if not np.isfinite(rate) or rate < 0:
        raise ValueError(
            "LCOE diagnostic discount rate must be finite and nonnegative."
        )
    investment = pd.DataFrame(tables["cost_invest"]).set_index(["region", "tech"])
    fixed = pd.DataFrame(tables["cost_fixed"]).set_index(["region", "tech"])
    rows = []
    for _, tech in crosswalk.iterrows():
        life = float(tech.lifetime_years)
        recovery = 1 / life if rate == 0 else rate / (1 - (1 + rate) ** (-life))
        field = "wind_cf" if tech.tech == "WIND_GEN" else "solar_cf"
        for region, cf in annual[field].items():
            ci = investment.loc[(str(region), tech.tech)]
            fo = fixed.loc[(str(region), tech.tech)]
            rows.append(
                {
                    "region": str(region),
                    "tech": tech.tech,
                    "weather_year": settings["weather_year"],
                    "annual_cf": float(cf),
                    "capacity_basis": tech.capacity_basis,
                    "cost_invest": float(ci.cost),
                    "cost_invest_units": ci.units,
                    "cost_fixed": float(fo.cost),
                    "cost_fixed_units": fo.units,
                    "lifetime_years": life,
                    "resource_efficiency": 1.0,
                    "cost_variable": 0.0,
                    "diagnostic_lcoe": float(
                        (ci.cost * recovery + fo.cost) / (8760 * cf)
                    )
                    if cf > 0
                    else np.nan,
                    "diagnostic_lcoe_units": ci.units.split("/")[0] + "/MWh",
                    "lcoe_discount_rate": rate,
                    "potential_status": "not_estimated",
                }
            )
    result = pd.DataFrame(rows)
    result.to_csv(output / "renewable_resources.csv", index=False)
    return result


def validate_temoa_tables(tables, schema_path):
    """Validate the exact exported rows against a fresh Temoa v4 schema."""
    with sqlite3.connect(":memory:") as db:
        db.executescript(Path(schema_path).read_text())
        periods = sorted(
            {
                int(r[k])
                for name in ("efficiency", "cost_fixed")
                for r in tables[name]
                for k in ("period", "vintage")
                if k in r
            }
        )
        db.executemany(
            "INSERT INTO time_period(sequence,period,flag) VALUES(?,?,'f')",
            enumerate(periods, 1),
        )
        db.execute("INSERT INTO time_season VALUES(1,'S',1,'annual')")
        db.execute("INSERT INTO time_of_day VALUES(1,'D',24,'annual')")
        db.executemany(
            "INSERT INTO commodity(name,flag) VALUES(?,?)",
            [("ethos", "s"), ("elc", "a")],
        )
        db.executemany(
            "INSERT INTO region(region) VALUES(?)",
            [(r,) for r in sorted({row["region"] for row in tables["efficiency"]})],
        )
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
        for name, rows in tables.items():
            columns = list(rows[0])
            available = {r[1] for r in db.execute(f'PRAGMA table_info("{name}")')}
            if not set(columns).issubset(available):
                raise ValueError(
                    f"Temoa schema mismatch for {name}: {set(columns) - available}"
                )
            db.executemany(
                f'INSERT INTO "{name}" ('
                + ",".join(f'"{c}"' for c in columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                [tuple(row[c] for c in columns) for row in rows],
            )
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(
                f"Renewable Temoa rows violate foreign keys: {violations[:3]}"
            )
        return {
            "foreign_key_violations": 0,
            "schema_sha256": sha256(schema_path),
            "table_counts": {
                name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                for name in tables
            },
        }


def preview(regions, annual, output, year):
    joined = regions.join(annual)
    joined.to_file(
        output / "annual_capacity_factors.gpkg",
        layer="capacity_factors",
        driver="GPKG",
        index=False,
    )
    fig, axes = plt.subplots(1, 2, figsize=(13, 8), constrained_layout=True)
    for axis, field, label in zip(
        axes, ["wind_cf", "solar_cf"], ["Onshore wind", "Utility PV (AC)"]
    ):
        joined.plot(
            column=field,
            ax=axis,
            cmap="viridis",
            vmin=0,
            vmax=1,
            legend=True,
            linewidth=0,
        )
        axis.set_title(label)
        axis.set_axis_off()
    fig.suptitle(
        f"{year} annual capacity factors | area-weighted resource baseline\nERA5 0.25° weather; no subgrid disaggregation or land eligibility"
    )
    fig.savefig(output / "annual_capacity_factors.png", dpi=160)
    plt.close(fig)


def refresh_parameter_exports(output, settings_path=None):
    """Re-export Gold parameters without rerunning unchanged weather/CF conversion."""
    root = find_project_root()
    output = Path(output)
    settings_path = (
        Path(settings_path) if settings_path else root / "config/renewables/atlite.toml"
    )
    settings = tomllib.loads(settings_path.read_text())
    record = json.loads((output / "manifest.json").read_text())
    bundle = root / settings["wind_resource_dir"]
    if (
        record["weather_year"] != settings["weather_year"]
        or record["sources"]["curve"] != sha256(bundle / "T1_curve.csv")
        or record["sources"]["specs"] != sha256(bundle / "T1_specs.yaml")
    ):
        raise ValueError(
            "Physical resource inputs changed; rerun Silver rather than just parameter exports."
        )
    months = sorted(output.glob("month_??.nc"))
    if len(months) != 12:
        raise ValueError("Require twelve complete monthly profiles.")
    for path in months:
        if record["monthly_sha256"][path.name] != sha256(path):
            raise ValueError("Existing monthly profile checksum mismatch.")
        with xr.open_dataset(
            path, engine=settings.get("netcdf_engine", "netcdf4")
        ) as ds:
            if json.loads(ds.attrs["configuration"]) != settings["technology"]:
                raise ValueError(
                    "Physical configuration changed; rerun Silver conversion."
                )
    hourly, annual = physical.annual_profiles(
        months, settings["weather_year"], settings.get("netcdf_engine", "netcdf4")
    )
    crosswalk, costs = select_costs(settings, root)
    tables = gold_tables(annual, crosswalk, costs, settings, output / "temoa_inputs")
    resource_summary(annual, crosswalk, tables, settings, output)
    validation = validate_temoa_tables(
        tables, root.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
    )
    manifest(output / "temoa_inputs/schema_validation.json", validation)
    costs.to_csv(output / "atb_selected_rows.csv", index=False)
    crosswalk.to_csv(output / "technology_crosswalk.csv", index=False)
    crosswalk.attrs["atb_wind_mapping"].to_csv(
        output / "atb_wind_class_mapping.csv", index=False
    )
    crosswalk.attrs["canoe_wind_mapping"].to_csv(
        output / "canoe_wind_class_mapping.csv", index=False
    )
    record["parameter_export"] = {
        "settings_sha256": sha256(settings_path),
        "crosswalk_sha256": sha256(root / settings["technology_crosswalk"]),
        "adapter_sha256": sha256(__file__),
        "currency_basis_sha256": sha256(root / settings["currency_basis"]),
    }
    record.pop("written_utc", None)
    manifest(output / "manifest.json", record)
    hourly.close()
    print(
        f"Renewables: refreshed CAD2020 WIND_GEN/SOLAR_GEN parameters and schema checks at {output}",
        flush=True,
    )
    return output


def run_renewables_build(config, *, settings_path=None, force=False):
    if not force and not getattr(config, "renewables_enabled", False):
        print(
            "Renewables: disabled in build profile; set [renewables].enabled=true.",
            flush=True,
        )
        return []
    if physical is None:
        raise RuntimeError(
            'Install renewable dependencies with pip install -e ".[renewables]"'
        )
    import atlite

    if atlite.__version__ != "0.7.0":
        raise ValueError("Review ERA5 adapter before upgrading Atlite 0.7.0.")
    root = find_project_root()
    settings_path = (
        Path(settings_path)
        if settings_path
        else root
        / getattr(config, "renewables_config", "config/renewables/atlite.toml")
    )
    settings = tomllib.loads(settings_path.read_text())
    engine = settings.get("netcdf_engine", "netcdf4")
    if engine not in {"h5netcdf", "netcdf4"}:
        raise ValueError("netcdf_engine must be h5netcdf or netcdf4.")
    resolutions = config.basemaps.projected_resolutions_km
    validate_resolutions(resolutions)
    if config.basemaps.geographic_resolutions_deg:
        raise ValueError(
            "Renewables currently requires projected kilometre basemaps; geographic-grid resolution policy is not defined."
        )
    if settings["weather_year"] != 2018 or sorted(settings["months"]) != list(
        range(1, 13)
    ):
        raise ValueError(
            "Annual renewable products require the shared 2018 weather year and all 12 months."
        )
    provinces = settings.get("province_subset") or list(config.study_area.provinces)
    if not set(provinces).issubset(config.study_area.provinces):
        raise ValueError(
            "Renewable province subset must belong to the build study area."
        )
    bronze = root / settings["bronze_dir"]
    plan = json.loads((bronze / "request_plan.json").read_text())["plan"]
    for item in tqdm(plan, desc="Renewables: verifying ERA5 Bronze", unit="request"):
        if item["request"]["year"] != [
            str(settings["weather_year"])
        ] or not cache_verified(item, bronze):
            raise ValueError(
                "Missing or mismatched ERA5 Bronze; run acquisition first."
            )
    if len(plan) != 36 or {(i["month"], i["feature"]) for i in plan} != {
        (m, f) for m in range(1, 13) for f in FEATURE_VARIABLES
    }:
        raise ValueError("Expected exactly one request per month/feature.")
    crosswalk, costs = select_costs(settings, root)
    bundle = root / settings["wind_resource_dir"]
    identities = {
        "settings": sha256(settings_path),
        "crosswalk": sha256(root / settings["technology_crosswalk"]),
        "curve": sha256(bundle / "T1_curve.csv"),
        "specs": sha256(bundle / "T1_specs.yaml"),
        "physical": sha256(physical.__file__),
        "stage": sha256(__file__),
        "atb_audit": sha256(root / settings["atb_selection_manifest"]),
    }
    outputs = []
    for resolution in resolutions:
        key = basemap_key(
            grid_type="projected",
            resolution=float(resolution),
            keep_method=config.basemaps.keep_method,
        )
        basemap = (
            root
            / "data_files/processed/basemaps"
            / f"{config.study_area.label}_basemap_{key}.gpkg"
        )
        print(
            f"Renewables: preparing {resolution:g} km production polygons for {provinces}.",
            flush=True,
        )
        regions, borders = production_regions(
            basemap, root / settings["boundary_file"], provinces
        )
        xmin, ymin, xmax, ymax = regions.to_crs(4326).total_bounds
        bounds = [
            np.floor(xmin / 0.25) * 0.25 - 0.25,
            np.floor(ymin / 0.25) * 0.25 - 0.25,
            np.ceil(xmax / 0.25) * 0.25 + 0.25,
            np.ceil(ymax / 0.25) * 0.25 + 0.25,
        ]
        run_id = identity(
            {
                "sources": identities,
                "basemap": sha256(basemap),
                "geometry": [
                    (r, g.wkb_hex) for r, g in zip(regions.region, regions.geometry)
                ],
                "raw": [
                    (i["request_id"], sha256(bronze / f"{i['request_id']}.payload"))
                    for i in plan
                ],
            }
        )
        output = root / settings["output_dir"] / config.build_id / key / run_id
        output.mkdir(parents=True, exist_ok=True)
        monthly = []
        for month in tqdm(
            range(1, 13), desc=f"Renewables: {resolution:g} km", unit="month"
        ):
            dest = output / f"month_{month:02d}.nc"
            record = dest.with_suffix(".json")
            if dest.exists():
                if not record.exists() or json.loads(record.read_text())[
                    "sha256"
                ] != sha256(dest):
                    raise ValueError("Monthly CF cache checksum mismatch.")
                monthly.append(dest)
                continue
            print(
                f"Renewables: preparing and converting month {month:02d} ({len(regions)} regions).",
                flush=True,
            )
            weather, weather_path = monthly_weather(
                [i for i in plan if i["month"] == month],
                bronze,
                root / settings["weather_cache_dir"],
                bounds,
                settings["weather_year"],
                engine,
            )
            cutout = atlite.Cutout(
                weather_path.with_name("descriptor_" + weather_path.name),
                module="era5",
                x=slice(float(weather.x.min()), float(weather.x.max())),
                y=slice(float(weather.y.min()), float(weather.y.max())),
                time=slice(weather.time.values[0], weather.time.values[-1]),
            )
            cutout.data = weather
            result, coverage = physical.convert(
                cutout, regions, bundle, settings["technology"]
            )
            result.attrs["weather_year"] = settings["weather_year"]
            temporary = dest.with_suffix(".part")
            result.to_netcdf(
                temporary,
                engine=engine,
                encoding={v: {"zlib": True, "complevel": 4} for v in result.data_vars},
            )
            temporary.replace(dest)
            manifest(
                record,
                {
                    "sha256": sha256(dest),
                    "weather_sha256": sha256(weather_path),
                    "coverage_min": float(coverage.min()),
                },
            )
            result.close()
            weather.close()
            monthly.append(dest)
        hourly, annual = physical.annual_profiles(
            monthly, settings["weather_year"], engine
        )
        annual.to_csv(output / "annual_capacity_factors.csv")
        preview(regions, annual, output, settings["weather_year"])
        costs.to_csv(output / "atb_selected_rows.csv", index=False)
        crosswalk.to_csv(output / "technology_crosswalk.csv", index=False)
        crosswalk.attrs["atb_wind_mapping"].to_csv(
            output / "atb_wind_class_mapping.csv", index=False
        )
        crosswalk.attrs["canoe_wind_mapping"].to_csv(
            output / "canoe_wind_class_mapping.csv", index=False
        )
        tables = gold_tables(
            annual, crosswalk, costs, settings, output / "temoa_inputs"
        )
        resource_summary(annual, crosswalk, tables, settings, output)
        validation = validate_temoa_tables(
            tables, root.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
        )
        manifest(output / "temoa_inputs/schema_validation.json", validation)
        legacy_comparison(regions, annual, root / "data_files/sites_full.csv", output)
        manifest(
            output / "manifest.json",
            {
                "run_id": run_id,
                "sources": identities,
                "resolution_km": resolution,
                "provinces": provinces,
                "regions": len(regions),
                "hours": len(hourly.time),
                "weather_year": settings["weather_year"],
                "annual_cf_ready": True,
                "resource_representation": "one_option_per_region",
                "wind_physical_baseline": "T1; no inferred turbine capacity fractions",
                "gold_merge_ready": False,
                "weighting": "uniform area within Census-clipped production polygons",
                "monthly_sha256": {p.name: sha256(p) for p in monthly},
            },
        )
        hourly.close()
        if settings.get("previews", {}).get("monthly", True):
            from geocanoe.preprocessing.renewables_preview import build_previews

            build_previews(output, root / settings["output_dir"] / "preview", engine)
        manifest(
            root / settings["output_dir"] / config.build_id / key / "selected_run.json",
            {"run_id": run_id, "output_dir": str(output)},
        )
        outputs.append(output)
        print(
            f"Renewables: complete; preview and Temoa input bundle at {output}",
            flush=True,
        )
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Shared geospatial build profile")
    parser.add_argument("--settings", type=Path)
    parser.add_argument(
        "--refresh-output",
        type=Path,
        help="Refresh parameter exports from an existing verified CF run",
    )
    args = parser.parse_args()
    if args.refresh_output:
        refresh_parameter_exports(args.refresh_output, args.settings)
    elif args.config:
        run_renewables_build(
            load_geospatial_build_config(args.config),
            settings_path=args.settings,
            force=True,
        )
    else:
        parser.error("Specify --config or --refresh-output")
