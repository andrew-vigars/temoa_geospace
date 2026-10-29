"""Configurable ERA5/Atlite Silver resource profiles and annual Temoa input bundles."""

from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
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
from geocanoe.preprocessing.wind_options import (
    DESIGNS,
    WIND_GROUP,
    mode as wind_mode,
    profile_field,
    package_identity,
)
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
    """Reuse matching dissolved boundaries; retain IDs and clip production geometry."""
    started = perf_counter()
    print("Production polygons: loading model grid...", flush=True)
    cells = gpd.read_file(basemap, layer="regions").to_crs(3347)
    if not cells.region.is_unique:
        raise ValueError("Basemap region IDs must be unique.")
    if "resolution_km" not in cells or cells.resolution_km.min() < 25:
        raise ValueError(
            "Basemap must declare a projected resolution of at least 25 km."
        )
    print(
        f"Production polygons: loaded {len(cells):,} cells in {perf_counter() - started:.1f}s",
        flush=True,
    )
    step = perf_counter()
    borders = None
    if "study_area" in cells and cells.study_area.nunique() == 1:
        label = str(cells.study_area.iloc[0])
        # Only a filename-safe label can identify a sibling basemap product.
        if Path(label).name == label and "/" not in label and "\\" not in label:
            cached = Path(basemap).parent / f"{label}_boundary_epsg3347.gpkg"
            if cached.is_file():
                print(
                    f"Production polygons: checking existing boundary {cached.name}...",
                    flush=True,
                )
                candidate = gpd.read_file(cached, layer="study_area_boundary")
                if (
                    len(candidate) == 1
                    and "province_codes" in candidate
                    and "study_area" in candidate
                    and candidate.study_area.iloc[0] == label
                    and set(str(candidate.province_codes.iloc[0]).split(","))
                    == set(provinces)
                ):
                    if (
                        candidate.crs is None
                        or candidate.crs.to_epsg() != 3347
                        or candidate.geometry.isna().any()
                        or candidate.geometry.is_empty.any()
                        or not candidate.is_valid.all()
                    ):
                        raise ValueError(
                            f"Invalid existing study-area boundary: {cached}"
                        )
                    borders = candidate
                    print(
                        f"Production polygons: reused dissolved boundary in {perf_counter() - step:.1f}s",
                        flush=True,
                    )
                else:
                    print(
                        "Production polygons: existing boundary does not match requested jurisdictions; using Census source.",
                        flush=True,
                    )
    if borders is None:
        print("Production polygons: loading selected Census boundaries...", flush=True)
        names = [
            name for name, code in PROVINCE_NAME_TO_CODE.items() if code in provinces
        ]
        where = (
            "PRENAME IN ("
            + ",".join("'" + n.replace("'", "''") + "'" for n in names)
            + ")"
        )
        borders = gpd.read_file(boundary, where=where).to_crs(3347)
        if set(borders.PRENAME.map(PROVINCE_NAME_TO_CODE)) != set(provinces):
            raise ValueError(
                "Census boundary does not cover the selected province set."
            )
        print(
            f"Production polygons: boundaries loaded in {perf_counter() - step:.1f}s; dissolving...",
            flush=True,
        )
    step = perf_counter()
    geometry = (
        borders.geometry.iloc[0] if len(borders) == 1 else borders.geometry.union_all()
    )
    shapely.prepare(geometry)
    print(
        f"Production polygons: boundary prepared in {perf_counter() - step:.1f}s; selecting cell centroids...",
        flush=True,
    )
    step = perf_counter()
    cells = cells.loc[shapely.covers(geometry, cells.centroid.array)].copy()
    print(
        f"Production polygons: selected {len(cells):,} cells in {perf_counter() - step:.1f}s; checking full-cell coverage...",
        flush=True,
    )
    step = perf_counter()
    interior = shapely.covers(geometry, cells.geometry.array)
    positions = np.flatnonzero(~interior)
    print(
        f"Production polygons: coverage checked in {perf_counter() - step:.1f}s; clipping {len(positions):,} coastal/border cells...",
        flush=True,
    )
    step = perf_counter()
    for start in range(0, len(positions), 64):
        selected = cells.index[positions[start : start + 64]]
        cells.loc[selected, "geometry"] = cells.loc[selected].geometry.intersection(
            geometry
        )
        print(
            f"Production polygons: clipped {min(start + 64, len(positions)):,}/{len(positions):,} cells ({perf_counter() - step:.1f}s)",
            flush=True,
        )
    step = perf_counter()
    cells["production_area_km2"] = cells.area / 1e6
    cells = cells.loc[cells.production_area_km2 > 0].set_index("region", drop=False)
    if cells.empty or not cells.is_valid.all():
        raise ValueError("Empty/invalid renewable production polygons.")
    print(
        f"Production polygons: area/validity checks {perf_counter() - step:.1f}s; total {perf_counter() - started:.1f}s",
        flush=True,
    )
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
    selections = dict(policy.get("classes", {"T1": 7, "T2": 8, "T3": 9}))
    if wind_mode(settings) == "resource_assigned":
        selections.setdefault("T4", 10)
    else:
        selections.pop("T4", None)
    representatives = canoe_wind_mapping(mapping, selections)
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
    scenario = settings.get("atb_scenario", "Moderate")
    if scenario not in {"Moderate", "Conservative", "Advanced"}:
        raise ValueError("Unsupported ATB scenario.")
    crosswalk["scenario"] = scenario
    if wind_mode(settings) in {"selectable_t1_t2_t3", "resource_assigned"}:
        if wind_mode(settings) == "resource_assigned":
            from geocanoe.preprocessing.wind_assignment import policy

            policy(settings)
            if not settings.get("land_exclusion", {}).get("enabled", False):
                raise ValueError(
                    "Resource-assigned wind requires land exclusions enabled."
                )
        if not settings.get("land_exclusion", {}).get(
            "enabled", False
        ) and not settings.get("capacity_limits", {}).get("enabled", False):
            raise ValueError(
                "Selectable wind designs require a shared resource-capacity bound."
            )
        rows = []
        template = crosswalk.loc[crosswalk.tech == "WIND_GEN"].iloc[0]
        for design, specs in DESIGNS.items():
            if design == "T4" and wind_mode(settings) != "resource_assigned":
                continue
            row = template.copy()
            selected_row = representatives.loc[
                representatives.turbine_technology == int(design[1:])
            ].iloc[0]
            row["tech"] = f"WIND_GEN_{design}"
            row["display_name"] = selected_row.display_name
            row["physical_reference"] = (
                f"{design}_{specs['rated_kw'] / 1000:g}MW_{specs['rotor_m']}m_{specs['hub_m']}m"
            )
            rows.append(row)
        crosswalk = pd.concat(
            [pd.DataFrame(rows), crosswalk.loc[crosswalk.tech == "SOLAR_GEN"]],
            ignore_index=True,
        )
    crosswalk.attrs["atb_wind_mapping"] = mapping
    crosswalk.attrs["canoe_wind_mapping"] = representatives
    print(
        "Renewables: using CANOE representative wind mapping "
        + ", ".join(
            f"T{r.turbine_technology}/Class{r.resource_class}"
            for r in representatives.itertuples()
        )
        + (
            "; annual wind-speed assignment."
            if wind_mode(settings) == "resource_assigned"
            else "; no wind-speed class assignment."
        ),
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
        for metric, unit in [
            ("OCC", "$/kW"),
            ("GCC", "$/kW"),
            ("Fixed O&M", "$/kW-yr"),
        ]:
            rows = master.loc[mask & (master.core_metric_parameter == metric)].copy()
            if len(rows) != 1 or rows.units.iloc[0] != unit:
                raise ValueError(
                    f"ATB selector must resolve exactly one {metric} row with {unit}: {tech.tech}"
                )
            value = float(rows.value.iloc[0])
            if not np.isfinite(value) or value < 0 or (metric != "GCC" and value == 0):
                raise ValueError("Invalid ATB cost value.")
            rows["tech"] = tech.tech
            rows["value_per_MW"] = value * 1000
            selected.append(rows)
    if wind_mode(settings) == "resource_assigned":
        attrs = crosswalk.attrs.copy()
        crosswalk.attrs = {}
        wind = crosswalk.loc[crosswalk.tech == "WIND_GEN_T1"].copy()
        wind["tech"] = "WIND_GEN"
        wind["display_name"] = "Spatially assigned T1/T2/T3/T4 mixture"
        wind["physical_reference"] = (
            "eligible-capacity-weighted spatial T1/T2/T3/T4 assignment"
        )
        crosswalk = pd.concat(
            [wind, crosswalk.loc[crosswalk.tech == "SOLAR_GEN"]], ignore_index=True
        )
        crosswalk.attrs = attrs
    return crosswalk, pd.concat(selected, ignore_index=True)


def monthly_weather(items, bronze, cache, bounds, year, engine="netcdf4"):
    """Cache prepared regional weather independently of output resolution/technology."""
    raw = {
        item["feature"]: sha256(bronze / f"{item['request_id']}.payload")
        for item in items
    }
    import ast

    source = Path(physical.__file__).read_text()
    preparation = identity(
        {
            node.name: ast.get_source_segment(source, node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"spatial_subset", "raw_dataset", "prepare_weather"}
        }
    )
    key = identity(
        {
            "raw": raw,
            "bounds": bounds,
            "adapter": preparation,
            "atlite": "0.7.0",
        }
    )
    path = cache / f"weather_{key}.nc"
    sidecar = path.with_suffix(".json")
    if not path.exists():
        # Legacy cache keys hashed the entire conversion module. The native ERA5
        # preparation routines are unchanged by the wind-assignment change.
        # Accept only source/bounds-identical legacy records, then verify bytes
        # and complete temporal coverage below. New records pin preparation only.
        for candidate in sorted(cache.glob("weather_*.json")):
            entry = json.loads(candidate.read_text())
            if (
                entry.get("raw_sha256") == raw
                and entry.get("bounds") == list(bounds)
                and entry.get("preparation_sha256", preparation) == preparation
            ):
                candidate_path = candidate.with_suffix(".nc")
                if candidate_path.is_file():
                    path, sidecar = candidate_path, candidate
                    break
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
        manifest(
            sidecar,
            {
                "sha256": sha256(path),
                "raw_sha256": raw,
                "bounds": bounds,
                "preparation_sha256": preparation,
            },
        )
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
            source["source_currency"],
            source["source_year"],
            source.get("source_scale", 1.0),
            config=source["economic_config"],
        )
    cost_currency = currency["target_currency"]
    if wind_mode(settings) == "selectable_t1_t2_t3" and not settings.get(
        "capacity_limits", {}
    ).get("enabled", False):
        raise ValueError("Selectable wind Gold export requires shared capacity limits.")
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
    assigned = wind_mode(settings) == "resource_assigned"
    required_metrics = {"OCC", "GCC", "Fixed O&M"}
    for cost_tech, rows in costs.groupby("tech"):
        if (
            len(rows) != len(required_metrics)
            or set(rows.core_metric_parameter) != required_metrics
            or not np.isfinite(rows.value_per_MW).all()
            or (rows.value_per_MW < 0).any()
        ):
            raise ValueError(
                f"Require exactly one OCC, GCC and Fixed O&M cost for {cost_tech}."
            )
    expected_cost_techs = (
        {f"WIND_GEN_{design}" for design in DESIGNS} | {"SOLAR_GEN"}
        if assigned
        else set(crosswalk.tech)
    )
    if set(costs.tech) != expected_cost_techs:
        raise ValueError(
            "ATB cost technologies do not match the renewable representation."
        )
    mixture = (
        pd.read_csv(
            output.parent / "wind_resource/regional_mix.csv", dtype={"region": str}
        ).set_index("region")
        if assigned
        else None
    )
    for _, tech in crosswalk.iterrows():
        field = profile_field(tech.tech)
        cost_tech = "WIND_GEN_T1" if assigned and tech.tech == "WIND_GEN" else tech.tech
        investment = float(
            costs.loc[
                (costs.tech == cost_tech)
                & (costs.core_metric_parameter.isin(["OCC", "GCC"])),
                "value_per_MW",
            ].sum()
        )
        fixed = float(
            costs.loc[
                (costs.tech == cost_tech)
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
            regional_investment, regional_fixed = investment, fixed
            if assigned and tech.tech == "WIND_GEN":
                fractions = mixture.loc[str(region)]
                values = {}
                for metric in ["OCC", "GCC", "Fixed O&M"]:
                    values[metric] = (
                        sum(
                            float(fractions[f"{design}_fraction"])
                            * float(
                                costs.loc[
                                    (costs.tech == f"WIND_GEN_{design}")
                                    & (costs.core_metric_parameter == metric),
                                    "value_per_MW",
                                ].iloc[0]
                            )
                            for design in DESIGNS
                        )
                        * currency["multiplier"]
                    )
                regional_investment, regional_fixed = (
                    values["OCC"] + values["GCC"],
                    values["Fixed O&M"],
                )
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
                    "cost": regional_investment,
                    "units": f"{cost_currency}/MW",
                }
            )
            tables["cost_fixed"].append(
                {
                    **base,
                    "period": period,
                    "vintage": vintage,
                    "cost": regional_fixed,
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
        if wind_mode(settings) == "selectable_t1_t2_t3":
            for row in tables["limit_capacity"]:
                if row["tech_or_group"] == "WIND_GEN":
                    row["tech_or_group"] = WIND_GROUP
            tables["tech_group"] = [
                dict(
                    group_name=WIND_GROUP,
                    notes="Shared total wind MW; common Palmer-Wilson density across selectable designs",
                )
            ]
            tables["tech_group_member"] = [
                dict(group_name=WIND_GROUP, tech=f"WIND_GEN_{design}")
                for design in ("T1", "T2", "T3")
            ]
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
            "investment_cost_components": ["OCC", "GCC"],
            "model_hours": 8760,
            "capacity_bounds": {
                "enabled": capacity_input is not None,
                "input_sha256": sha256(capacity_input) if capacity_input else None,
                "method": "eligible_area_km2 * density_mw_per_km2",
            },
            "cost_projection_year": settings["cost_projection_year"],
            "weather_year": settings["weather_year"],
            "wind_representation": wind_mode(settings),
            "atb_scenario": settings.get("atb_scenario", "Moderate"),
            "wind_assignment_manifest_sha256": sha256(
                output.parent / "wind_resource/manifest.json"
            )
            if assigned
            else None,
            "wind_assignment_basis": "Annual ERA5 at 110 m; fixed eligible-capacity fractions; below 5.9 m/s assigned T4"
            if assigned
            else None,
            "wind_curve_source": package_identity(settings)
            if wind_mode(settings) != "t1_only"
            else {"representation": "T1 reference"},
            "shared_wind_capacity_group": WIND_GROUP
            if wind_mode(settings) == "selectable_t1_t2_t3"
            else None,
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
        field = profile_field(tech.tech)
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
                    "potential_status": "land_constrained_scenario"
                    if "limit_capacity" in tables
                    else "not_estimated",
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


def preview(regions, annual, output, year, land_enabled=False):
    joined = regions.join(annual)
    joined.to_file(
        output / "annual_capacity_factors.gpkg",
        layer="capacity_factors",
        driver="GPKG",
        index=False,
    )
    profiles = (
        [(f"wind_cf_{t.lower()}", f"Onshore wind {t}") for t in ("T1", "T2", "T3")]
        if "wind_cf_t1" in annual
        else [("wind_cf", "Onshore wind")]
    )
    profiles += [("solar_cf", "Utility PV (AC)")]
    fig, axes = plt.subplots(
        1, len(profiles), figsize=(6.5 * len(profiles), 8), constrained_layout=True
    )
    for axis, field, label in zip(
        axes, [p[0] for p in profiles], [p[1] for p in profiles]
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
        f"{year} annual capacity factors | "
        + (
            "eligible-land area weighting"
            if land_enabled
            else "area-weighted resource baseline"
        )
        + "\nERA5 0.25° weather; no subgrid weather disaggregation"
    )
    fig.savefig(output / "annual_capacity_factors.png", dpi=160)
    plt.close(fig)


def renewable_export_settings(settings, output):
    from geocanoe.preprocessing import renewables_land as land

    exported = land.export_settings(settings, output)
    if wind_mode(settings) == "resource_assigned":
        from geocanoe.preprocessing.wind_assignment import export_settings

        exported = export_settings(exported, output)
    return exported


def refresh_parameter_exports(output, settings_path=None):
    """Re-export Gold parameters without rerunning unchanged weather/CF conversion."""
    root = find_project_root()
    output = Path(output)
    settings_path = (
        Path(settings_path) if settings_path else root / "config/renewables/atlite.toml"
    )
    settings = tomllib.loads(settings_path.read_text())
    record = json.loads((output / "manifest.json").read_text())
    from geocanoe.preprocessing import renewables_land as land

    if record["sources"].get(
        "land_exclusion", {"policy": {"enabled": False}}
    ) != land.source_identity(settings, root):
        raise ValueError("Land exclusion inputs changed; rerun Silver conversion.")
    bundle = root / settings["wind_resource_dir"]
    design_hashes = package_identity(settings)
    if record["sources"].get("wind_designs", design_hashes) != design_hashes:
        raise ValueError("Wind design inputs changed; rerun Silver conversion.")
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
    tables = gold_tables(
        annual,
        crosswalk,
        costs,
        renewable_export_settings(settings, output),
        output / "temoa_inputs",
    )
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


def run_renewables_build(
    config, *, settings_path=None, force=False, land_preview_only=False
):
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
    from geocanoe.preprocessing import renewables_land as land

    land_identity = land.source_identity(settings, root)
    if land_preview_only and not land_identity["policy"]["enabled"]:
        raise ValueError("Land preview requires land_exclusion.enabled=true.")
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
        "wind_assignment": sha256(Path(__file__).with_name("wind_assignment.py")),
        "atb_audit": sha256(root / settings["atb_selection_manifest"]),
        "land_exclusion": land_identity,
        "wind_designs": package_identity(settings),
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
        step = perf_counter()
        print("Renewables: transforming production bounds to WGS84...", flush=True)
        xmin, ymin, xmax, ymax = regions.to_crs(4326).total_bounds
        print(
            f"Renewables: transformed bounds in {perf_counter() - step:.1f}s",
            flush=True,
        )
        bounds = [
            np.floor(xmin / 0.25) * 0.25 - 0.25,
            np.floor(ymin / 0.25) * 0.25 - 0.25,
            np.ceil(xmax / 0.25) * 0.25 + 0.25,
            np.ceil(ymax / 0.25) * 0.25 + 0.25,
        ]
        step = perf_counter()
        print("Renewables: hashing basemap and production geometry...", flush=True)
        basemap_digest = sha256(basemap)
        geometry_identity = [
            (r, g.wkb_hex) for r, g in zip(regions.region, regions.geometry)
        ]
        print(
            f"Renewables: geometry hashes prepared in {perf_counter() - step:.1f}s; hashing {len(plan)} ERA5 payloads...",
            flush=True,
        )
        raw_identity = []
        step = perf_counter()
        for item in tqdm(
            plan, desc="Renewables: hashing ERA5 identity", unit="payload"
        ):
            raw_identity.append(
                (item["request_id"], sha256(bronze / f"{item['request_id']}.payload"))
            )
        print(
            f"Renewables: ERA5 hashing completed in {perf_counter() - step:.1f}s",
            flush=True,
        )
        run_id = identity(
            {
                "sources": identities,
                "basemap": basemap_digest,
                "geometry": geometry_identity,
                "raw": raw_identity,
            }
        )
        output = root / settings["output_dir"] / config.build_id / key / run_id
        output.mkdir(parents=True, exist_ok=True)
        if not land_preview_only:
            from geocanoe.preprocessing.renewables_preview import (
                build_raw_weather_preview,
            )

            raw_preview = (
                root
                / settings["output_dir"]
                / "preview"
                / f"{config.build_id}_{key}_{run_id}"
                / "era5"
            )
            for month in tqdm(
                range(1, 13), desc="Renewables: raw ERA5 previews", unit="month"
            ):
                build_raw_weather_preview(
                    {
                        i["feature"]: bronze / f"{i['request_id']}.payload"
                        for i in plan
                        if i["month"] == month
                    },
                    raw_preview,
                    settings["weather_year"],
                    month,
                    bounds,
                    engine,
                    regions=regions,
                )
        monthly = []
        land_weights = None
        resource_assignment = None
        assignment_written = False
        if wind_mode(settings) == "resource_assigned" and not land_preview_only:
            from geocanoe.preprocessing.wind_assignment import annual_assignment

            def weather_month(month):
                return monthly_weather(
                    [i for i in plan if i["month"] == month],
                    bronze,
                    root / settings["weather_cache_dir"],
                    bounds,
                    settings["weather_year"],
                    engine,
                )

            resource_assignment = annual_assignment(output, settings, weather_month)
        for month in tqdm(
            range(1, 13), desc=f"Renewables: {resolution:g} km", unit="month"
        ):
            dest = output / f"month_{month:02d}.nc"
            record = dest.with_suffix(".json")
            if dest.exists() and not land_preview_only:
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
            if land_identity["policy"]["enabled"] and land_weights is None:
                land_weights = land.build_land_weights(
                    cutout, regions, settings, root, output, land_identity
                )
            if land_preview_only:
                weather.close()
                outputs.append(output / "land_exclusion")
                break
            if resource_assignment is not None and not assignment_written:
                from geocanoe.preprocessing.wind_assignment import regional_mix

                regional_mix(
                    resource_assignment, land_weights["wind"], regions, settings, output
                )
                assignment_written = True
            result, coverage = physical.convert(
                cutout,
                regions,
                bundle,
                settings["technology"],
                land_weights=land_weights,
                resource_assignment=resource_assignment,
            )
            result.attrs["land_exclusion_identity"] = identity(land_identity)
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
        if land_preview_only:
            continue
        hourly, annual = physical.annual_profiles(
            monthly, settings["weather_year"], engine
        )
        annual.to_csv(output / "annual_capacity_factors.csv")
        preview(
            regions,
            annual,
            output,
            settings["weather_year"],
            land_identity["policy"]["enabled"],
        )
        costs.to_csv(output / "atb_selected_rows.csv", index=False)
        crosswalk.to_csv(output / "technology_crosswalk.csv", index=False)
        crosswalk.attrs["atb_wind_mapping"].to_csv(
            output / "atb_wind_class_mapping.csv", index=False
        )
        crosswalk.attrs["canoe_wind_mapping"].to_csv(
            output / "canoe_wind_class_mapping.csv", index=False
        )
        tables = gold_tables(
            annual,
            crosswalk,
            costs,
            renewable_export_settings(settings, output),
            output / "temoa_inputs",
        )
        resource_summary(annual, crosswalk, tables, settings, output)
        validation = validate_temoa_tables(
            tables, root.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
        )
        manifest(output / "temoa_inputs/schema_validation.json", validation)
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
                "wind_physical_baseline": (
                    "Spatially assigned T1/T2/T3/T4, eligible-capacity weighted; includes low-resource T4"
                    if wind_mode(settings) == "resource_assigned"
                    else wind_mode(settings)
                ),
                "gold_merge_ready": False,
                "weighting": "eligible area within production polygons"
                if land_identity["policy"]["enabled"]
                else "uniform area within Census-clipped production polygons",
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
    import logging
    import warnings

    logging.basicConfig(level=logging.INFO)
    logging.captureWarnings(True)
    warnings.simplefilter("default", DeprecationWarning)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Shared geospatial build profile")
    parser.add_argument("--settings", type=Path)
    parser.add_argument(
        "--land-preview-only",
        action="store_true",
        help="Build exclusion maps and bounds, stopping before CF conversion or Gold export",
    )
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
            land_preview_only=args.land_preview_only,
        )
    else:
        parser.error("Specify --config or --refresh-output")
