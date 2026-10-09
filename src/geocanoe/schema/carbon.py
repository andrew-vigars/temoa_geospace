"""Encode facility-carbon accounting using only the standard Temoa v4 tables.

The fixed annual baseline must be captured or released. Captured carbon may be
stored, transported, released, or used in fuels. Fuel utilization is treated as
eventual release of all incoming facility carbon within the model horizon; it
does not earn permanent abatement. Electricity and upstream fuel emissions are
outside this first accounting boundary.
"""

from __future__ import annotations

import math

import pandas as pd


FACILITY_EMISSION = "co2_facility_emission"
FALLBACK_EMISSION = "co2_fallback_emission"
BASELINE_TECH = "CO2_BASELINE"
RELEASE_TECH = "CO2_RELEASE"


def _append(db: dict[str, pd.DataFrame], table: str, rows: list[dict]) -> None:
    if not rows:
        return
    extra = pd.DataFrame(rows).reindex(columns=db[table].columns)
    db[table] = (
        extra if db[table].empty else pd.concat([db[table], extra], ignore_index=True)
    )


def rebuild_carbon_accounting(
    db: dict[str, pd.DataFrame],
    sites: pd.DataFrame,
    period: int,
    period_years: int,
    *,
    policy: str = "none",
    price_per_tonne: float = 0,
    fallback_gasoline_emission_factor: float = 0,
) -> None:
    """Add an atmospheric emissions price without storage credits or solver changes.

    Call once on freshly rebuilt Gold tables, after efficiencies and constraints.
    Baseline capacity is fixed with limit_capacity, and annual output is fixed
    at that capacity with limit_annual_capacity_factor. No storage floor is added.
    Emission coefficients multiply output flow, hence input carbon is divided
    by process efficiency. Prices remain annual currency/t, without year scaling.
    """
    if policy not in {"none", "emissions_price"}:
        raise ValueError("Unknown carbon policy.")
    if any(
        not math.isfinite(v) or v < 0
        for v in [price_per_tonne, fallback_gasoline_emission_factor]
    ):
        raise ValueError(
            "Carbon price and fallback factor must be finite and nonnegative."
        )
    if policy == "none":
        if price_per_tonne or fallback_gasoline_emission_factor:
            raise ValueError(
                "Disabled carbon policy requires zero price and fallback factor."
            )
        return
    if period_years <= 0:
        raise ValueError("Carbon accounting requires a positive period duration.")
    if sites.region.duplicated().any():
        raise ValueError("Carbon baseline requires unique site regions.")
    baseline = pd.to_numeric(sites.co2, errors="coerce")
    if not baseline.map(lambda v: math.isfinite(v) and v >= 0).all():
        raise ValueError("Facility carbon baseline must be finite and nonnegative.")
    reserved_techs = {BASELINE_TECH, RELEASE_TECH}
    reserved_comms = {"co2_raw", "co2_released", FACILITY_EMISSION, FALLBACK_EMISSION}
    if (
        db["technology"].tech.isin(reserved_techs).any()
        or db["commodity"].name.isin(reserved_comms).any()
    ):
        raise ValueError(
            "Carbon accounting identifiers already exist; rebuild from fresh inputs."
        )

    _append(
        db,
        "commodity",
        [
            {
                "name": "co2_raw",
                "flag": "a",
                "units": "t CO2e",
                "description": "Fixed facility carbon before capture",
            },
            {
                "name": "co2_released",
                "flag": "wa",
                "units": "t CO2e",
                "description": "Carbon released to atmosphere",
            },
            {
                "name": FACILITY_EMISSION,
                "flag": "e",
                "units": "t CO2e",
                "description": "Facility-origin atmospheric emissions",
            },
            {
                "name": FALLBACK_EMISSION,
                "flag": "e",
                "units": "t CO2",
                "description": "Optional fallback gasoline combustion emissions",
            },
        ],
    )
    _append(
        db,
        "technology",
        [
            {
                **{
                    c: 0
                    for c in [
                        "unlim_cap",
                        "annual",
                        "reserve",
                        "curtail",
                        "retire",
                        "flex",
                        "exchange",
                        "seas_stor",
                    ]
                },
                "tech": tech,
                "flag": "p",
                "annual": 1,
                "unlim_cap": int(tech == RELEASE_TECH),
                "sector": "industrial",
                "description": "Facility carbon bookkeeping",
            }
            for tech in [BASELINE_TECH, RELEASE_TECH]
        ],
    )
    efficiencies, capacities, factors, conversions, lifetimes, investments = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for region, amount in zip(sites.region, baseline):
        for tech in reserved_techs:
            if tech == BASELINE_TECH and amount <= 0:
                continue
            conversions.append({"region": region, "tech": tech, "c2a": 1})
            lifetimes.append({"region": region, "tech": tech, "lifetime": period_years})
        if amount > 0:
            investments.append(
                {
                    "region": region,
                    "tech": BASELINE_TECH,
                    "vintage": period,
                    "cost": 0,
                    "notes": "Bookkeeping source; no physical investment",
                }
            )
            efficiencies.append(
                {
                    "region": region,
                    "input_comm": "ethos",
                    "tech": BASELINE_TECH,
                    "vintage": period,
                    "output_comm": "co2_raw",
                    "efficiency": 1,
                }
            )
            capacities.append(
                {
                    "region": region,
                    "period": period,
                    "tech_or_group": BASELINE_TECH,
                    "operator": "e",
                    "capacity": amount,
                    "units": "t CO2e/year",
                }
            )
            factors.append(
                {
                    "region": region,
                    "tech_or_group": BASELINE_TECH,
                    "vintage": period,
                    "output_comm": "co2_raw",
                    "operator": "e",
                    "factor": 1,
                }
            )
        for comm in ["co2_raw", "co2"]:
            efficiencies.append(
                {
                    "region": region,
                    "input_comm": comm,
                    "tech": RELEASE_TECH,
                    "vintage": period,
                    "output_comm": "co2_released",
                    "efficiency": 1,
                }
            )
    capture = db["efficiency"].tech.eq("CO2_CAP")
    if not db["efficiency"].loc[capture, "input_comm"].eq("ethos").all():
        raise ValueError(
            "Expected CO2_CAP to draw from ethos before carbon accounting."
        )
    if not db["efficiency"].loc[capture, "efficiency"].eq(1).all():
        raise ValueError(
            "Initial carbon accounting requires unit CO2 capture efficiency."
        )
    injection = db["efficiency"].tech.eq("CO2_INJECT")
    if not db["efficiency"].loc[injection, "efficiency"].eq(1).all():
        raise ValueError(
            "Initial carbon accounting requires unit CO2 injection efficiency."
        )
    db["efficiency"].loc[capture, "input_comm"] = "co2_raw"
    # Captured carbon must balance exactly; no waste disposal outside release.
    db["commodity"].loc[db["commodity"].name.eq("co2"), "flag"] = "a"
    for table, rows in [
        ("efficiency", efficiencies),
        ("limit_capacity", capacities),
        ("limit_annual_capacity_factor", factors),
        ("capacity_to_activity", conversions),
        ("lifetime_tech", lifetimes),
        ("cost_invest", investments),
    ]:
        _append(db, table, rows)

    emissions = []
    for row in db["efficiency"].itertuples(index=False):
        coefficient = 0.0
        emis_comm = FACILITY_EMISSION
        note = ""
        efficiency = float(row.efficiency)
        if not math.isfinite(efficiency) or efficiency <= 0:
            raise ValueError("Carbon accounting requires finite positive efficiencies.")
        if row.tech == RELEASE_TECH:
            coefficient, note = 1 / efficiency, "Explicit atmospheric release"
        elif row.input_comm == "co2" and row.output_comm == "co2":
            if efficiency > 1:
                raise ValueError("CO2 transport cannot create carbon.")
            coefficient, note = 1 / efficiency - 1, "CO2 transport losses"
        elif row.input_comm == "co2" and row.tech != "CO2_INJECT":
            coefficient, note = (
                1 / efficiency,
                "All facility carbon used in fuel is eventually released; includes conversion losses",
            )
        elif row.tech in {"GSL_BACKUP", "GSL_EXISTING"}:
            coefficient = fallback_gasoline_emission_factor
            emis_comm, note = (
                FALLBACK_EMISSION,
                "Configured fallback gasoline combustion factor",
            )
        if coefficient:
            emissions.append(
                {
                    "region": row.region,
                    "emis_comm": emis_comm,
                    "input_comm": row.input_comm,
                    "tech": row.tech,
                    "vintage": row.vintage,
                    "output_comm": row.output_comm,
                    "activity": coefficient,
                    "units": "t emissions/t output",
                    "notes": note,
                }
            )
    _append(db, "emission_activity", emissions)
    priced_regions = sorted({(row["region"], row["emis_comm"]) for row in emissions})
    _append(
        db,
        "cost_emission",
        [
            {
                "region": region,
                "period": period,
                "emis_comm": emission,
                "cost": price_per_tonne,
                "units": "model currency/t emissions",
                "notes": "Atmospheric emissions price; no storage credit",
            }
            for region, emission in priced_regions
        ],
    )
    print(
        f"Carbon policy: {policy}; price: {price_per_tonne:g}/t; "
        f"annual facility baseline: {baseline.sum():,.2f} t CO2e; "
        f"fallback gasoline factor: {fallback_gasoline_emission_factor:g}"
    )
