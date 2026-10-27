"""Replace legacy ELC_GEN with validated annual WIND_GEN/SOLAR_GEN inputs."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd

from geocanoe.acquisition.era5_renewables import sha256
from geocanoe.geospatial.basemaps import basemap_key
from geocanoe.paths import find_project_root

RENEWABLE_TECHS = {"WIND_GEN", "SOLAR_GEN"}
SELECTABLE_TECHS = {"WIND_GEN_T1", "WIND_GEN_T2", "WIND_GEN_T3", "SOLAR_GEN"}
WIND_GROUP = "WIND_RESOURCE"


def apply_bundle(db_encoded, bundle_dir, nodes, model_period):
    """Validate first, then replace legacy rows atomically in the encoded mapping."""
    bundle_dir = Path(bundle_dir)
    contract = json.loads((bundle_dir / "contract.json").read_text())
    selectable = contract.get("wind_representation", "t1_only") == "selectable_t1_t2_t3"
    renewable_techs = SELECTABLE_TECHS if selectable else RENEWABLE_TECHS
    if (
        contract["currency"],
        contract["capacity_unit"],
        contract["activity_unit"],
        contract["weather_year"],
    ) != ("CAD2020", "MW", "MWh/year", 2018):
        raise ValueError(
            "Renewable bundle must use CAD2020 dollars, MW, MWh/year and shared 2018 weather."
        )
    if (
        contract.get("currency_conversion", {}).get("basis", {}).get("target_scale")
        != 1
    ):
        raise ValueError("Gold renewable costs require CAD2020 dollars, not millions.")
    tables = {}
    nodes = set(map(str, nodes))
    for name, digest in contract["table_sha256"].items():
        path = bundle_dir / f"{name}.csv"
        if sha256(path) != digest:
            raise ValueError(f"Renewable input checksum mismatch: {name}")
        table = pd.read_csv(path)
        if "region" in table:
            table["region"] = table.region.astype(str)
            table = table.loc[table.region.isin(nodes)].copy()
        tables[name] = table
    if set(tables["technology"].tech) != renewable_techs:
        raise ValueError("Unexpected renewable technology definitions.")
    if selectable:
        if not contract.get("capacity_bounds", {}).get("enabled", False):
            raise ValueError("Selectable wind requires shared capacity bounds.")
        groups = tables.get("tech_group", pd.DataFrame())
        members = tables.get("tech_group_member", pd.DataFrame())
        if (
            list(groups.get("group_name", [])) != [WIND_GROUP]
            or len(members) != 3
            or set(zip(members.get("group_name", []), members.get("tech", [])))
            != {(WIND_GROUP, t) for t in SELECTABLE_TECHS - {"SOLAR_GEN"}}
        ):
            raise ValueError("Invalid shared wind resource group membership.")
    for tech in renewable_techs:
        for name in [
            "efficiency",
            "capacity_to_activity",
            "lifetime_tech",
            "limit_annual_capacity_factor",
            "cost_invest",
            "cost_fixed",
            "cost_variable",
        ]:
            frame = tables[name]
            column = "tech" if "tech" in frame else "tech_or_group"
            subset = frame.loc[frame[column] == tech]
            if len(subset) != len(nodes) or set(subset.region) != nodes:
                raise ValueError(
                    f"Renewable Silver must cover every selected Gold node: {tech}/{name}."
                )
    for name, table in tables.items():
        for column in ["period", "vintage"]:
            if column in table and not (table[column] == model_period).all():
                raise ValueError(
                    "Renewable bundle model period/vintage must match the Gold build."
                )
    factors = tables["limit_annual_capacity_factor"].factor.to_numpy(float)
    if not np.isfinite(factors).all() or np.any((factors < 0) | (factors > 1)):
        raise ValueError("Invalid renewable annual capacity factors.")
    if contract.get("capacity_bounds", {}).get("enabled", False):
        if "limit_capacity" not in tables:
            raise ValueError("Capacity-bounded bundle is missing limit_capacity.")
        bounds = tables["limit_capacity"]
        bound_targets = {WIND_GROUP, "SOLAR_GEN"} if selectable else RENEWABLE_TECHS
        if set(bounds.tech_or_group) != bound_targets:
            raise ValueError("Unexpected renewable capacity-bound targets.")
        for tech in bound_targets:
            selected = bounds.loc[bounds.tech_or_group == tech]
            if len(selected) != len(nodes) or set(selected.region) != nodes:
                raise ValueError("Capacity bounds must cover every selected Gold node.")
        if (
            not np.isfinite(bounds.capacity).all()
            or (bounds.capacity < 0).any()
            or not bounds.units.eq("MW").all()
            or not bounds.operator.eq("le").all()
        ):
            raise ValueError("Invalid renewable MW capacity bounds.")
    # Work on copies; a failed validation above never mutates a live build.
    updated = {}
    removed = {}
    for name, frame in db_encoded.items():
        keep = pd.Series(True, index=frame.index)
        for column in ["tech", "tech_or_group", "tech_in", "tech_out", "group_name"]:
            if column in frame:
                keep &= ~frame[column].isin(
                    RENEWABLE_TECHS | SELECTABLE_TECHS | {"ELC_GEN", WIND_GROUP}
                )
        removed[name] = int((~keep).sum())
        updated[name] = frame.loc[keep].copy()
    for name, table in tables.items():
        if name not in updated:
            raise ValueError(
                f"Target Gold build is missing renewable parameter table {name}."
            )
        # Core technology reconstruction omits this optional Temoa v4 column.
        # Keep the validated bundle's descriptions when merging into that frame.
        if name == "technology" and "description" not in updated[name]:
            updated[name]["description"] = None
        unknown = set(table.columns) - set(updated[name].columns)
        if unknown:
            raise ValueError(f"Target Temoa schema mismatch {name}: {unknown}")
        updated[name] = pd.concat([updated[name], table], ignore_index=True)
    db_encoded.update(updated)
    return {
        "replaced_legacy": "ELC_GEN",
        "technologies": sorted(renewable_techs),
        "nodes": len(nodes),
        "removed_rows": removed,
    }


def configured_bundle_dir(build_config, basemap_stem):
    """Resolve one immutable Silver bundle for the Gold build's selected basemap."""
    if not getattr(build_config, "renewables_enabled", False):
        return None
    root = find_project_root()
    settings = tomllib.loads((root / build_config.renewables_config).read_text())
    if not settings.get("gold", {}).get("enabled", False):
        print(
            "Renewable Gold replacement disabled in renewable settings; ELC_GEN retained.",
            flush=True,
        )
        return None
    if settings["gold"]["legacy_policy"] != "replace":
        raise ValueError("Supported renewable Gold policy is replace ELC_GEN.")
    candidates = []
    for resolution in build_config.basemaps.projected_resolutions_km:
        key = basemap_key(
            "projected", float(resolution), build_config.basemaps.keep_method
        )
        stem = f"{build_config.study_area.label}_basemap_{key}"
        if stem == basemap_stem:
            selected = (
                root
                / settings["output_dir"]
                / build_config.build_id
                / key
                / "selected_run.json"
            )
            candidates.append(selected)
    if len(candidates) != 1:
        raise ValueError(
            "Gold basemap must match exactly one configured renewable Silver resolution."
        )
    pointer = json.loads(candidates[0].read_text())
    return Path(pointer["output_dir"]) / "temoa_inputs"


def apply_configured_renewables(db_encoded, build_config, runtime_config, nodes):
    """Schema-build hook; requires explicit Gold enablement and complete Silver."""
    bundle = getattr(runtime_config, "renewables_bundle_dir", None)
    if bundle is None:
        bundle = configured_bundle_dir(build_config, runtime_config.basemap_stem)
    if bundle is None:
        return None
    report = apply_bundle(db_encoded, bundle, nodes, runtime_config.model_start_year)
    print(
        f"Renewable Gold replacement: {report['nodes']} nodes; {', '.join(report['technologies'])} replace ELC_GEN.",
        flush=True,
    )
    return report
