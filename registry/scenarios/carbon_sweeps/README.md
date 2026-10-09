# Provincial carbon enforcement sweeps

This draft set contains exactly 24 scenario overlays: six storage-target levels
and six emissions-price levels, each with trucks enabled and disabled. The two
samples and historical canonical scenarios outside this directory are not
additional entries in this sweep set.

All overlays enable `emissions.policy = "emissions_price"`, including target
runs with zero price. They use 2025-2050, a projected 25 km basemap, pipelines
enabled, `all_km_dependent` pipeline impedance, five `CO2_PIPE` EOS curves, and
one curve for each other pipeline technology. These are capacity-cost curves,
not five discrete pipelines. Legacy gasoline is disabled. Backup gasoline
remains available and is reported separately, with no emissions price.

## Storage-target sweep

Targets are permanent geological storage, not gross capture for fuel use.
The reference baseline is 278,713,570 t CO2e/year from the inspected provincial
Gold database `gold_prov-quant_baseline-25km_826e5234_9c7db6af.sqlite`.
Over 25 years this is 6,967,839,250 tonnes. The current schema accepts absolute
cumulative tonnes; percentages below document how these drafts were calculated.
Recalculate targets for a different study area or emissions dataset. They are
not dynamically normalized by the loader.

| Level | Cumulative target (t CO2e) | Trucks | No trucks |
| --- | ---: | --- | --- |
| 0%: baseline, no storage floor | 0 | [target_000.toml](target_000.toml) | [target_000_no_trucks.toml](target_000_no_trucks.toml) |
| 25% | 1,741,959,812.5 | [target_025.toml](target_025.toml) | [target_025_no_trucks.toml](target_025_no_trucks.toml) |
| 50% | 3,483,919,625 | [target_050.toml](target_050.toml) | [target_050_no_trucks.toml](target_050_no_trucks.toml) |
| 75% | 5,225,879,437.5 | [target_075.toml](target_075.toml) | [target_075_no_trucks.toml](target_075_no_trucks.toml) |
| 95% | 6,619,447,287.5 | [target_095.toml](target_095.toml) | [target_095_no_trucks.toml](target_095_no_trucks.toml) |
| 100%: facility net-zero target | 6,967,839,250 | [target_100.toml](target_100.toml) | [target_100_no_trucks.toml](target_100_no_trucks.toml) |

All target runs have zero emissions price. Positive targets use
`minimum_cumulative_activity`; the zero target uses `requirement = "none"`.
The 100% endpoint tests feasibility under the selected storage and network
bounds; it does not assert whole-system net-zero. The historical 6.8 Gt canonical
target is retained outside this set and is approximately 97.6% of this baseline.

## Emissions-price sweep

All price runs have no minimum storage requirement. Price levels are draft
numeric values in model currency per tonne; cost units and currency year must
be harmonized before interpreting them as calibrated CAD/tCO2e. The 350 level
is a compliance-fund reference, not a simulation of Clean Fuel Regulations,
its credit eligibility, CPI indexing, or its 10% funding-program limit.

| Price level | Trucks | No trucks |
| ---: | --- | --- |
| 0: baseline | [price_000.toml](price_000.toml) | [price_000_no_trucks.toml](price_000_no_trucks.toml) |
| 50 | [price_050.toml](price_050.toml) | [price_050_no_trucks.toml](price_050_no_trucks.toml) |
| 100 | [price_100.toml](price_100.toml) | [price_100_no_trucks.toml](price_100_no_trucks.toml) |
| 170 | [price_170.toml](price_170.toml) | [price_170_no_trucks.toml](price_170_no_trucks.toml) |
| 250 | [price_250.toml](price_250.toml) | [price_250_no_trucks.toml](price_250_no_trucks.toml) |
| 350: upper price reference | [price_350.toml](price_350.toml) | [price_350_no_trucks.toml](price_350_no_trucks.toml) |

The upper price does not guarantee zero facility emissions. Report solved
storage, residual emissions, gasoline supply routes, resource costs, and
emissions payments separately. The two zero-level methods intentionally have
identical model settings for each transport variant, but separate scenario IDs
retain the requested six-run sweep structure.

## Building and running

Select one compatible provincial build profile and one of these overlays for
each Gold build. The selected Silver products determine geological eligibility
and capacity bounds. No-truck files set `roads_enabled = false`; this removes
all road/truck commodity transport while retaining pipelines and the separate
electricity-transmission representation.

Build the complete set with individual build logs and an incremental CSV index:

```powershell
.venv/Scripts/python.exe scripts/build_schema_set.py --config config/build_profiles/provinces_orbits_quant.toml --scenario-dir registry/scenarios/carbon_sweeps --log-dir data_files/processed/schema/build_logs/carbon_sweeps_20261008_full --exclude-snap-outliers
```

Use a new log directory for subsequent builds. The explicit outlier option
accepts the audited graph-snap exclusions used by the provincial reference
builds. Each successful row in `schema_set.csv` links its database, manifest,
and complete build log; a failed build stops the set and preserves its log.
Gold construction does not solve the models or change the active batch profile.

The corresponding solver configurations are in
`config/temoav4/carbon_sweeps/`. Each selects its verified Gold database
explicitly and uses perfect foresight, the EOS extension, and Gurobi with a
1% MIP gap. Results are written to that scenario's database, matching the
existing provincial run convention.

`config/batch_profiles/batch_run.toml` selects all 24 configurations, ordered
as target sweeps with and without trucks, then price sweeps with and without
trucks. Each sweep runs from its lowest to highest level. The batch continues
after failures, skips completed runs, and does not automatically retry failures.
The schema build-log directory contains `solver_config_set.csv` and a copy of
the previous active batch selection.

To execute the selected batch:

```powershell
.venv/Scripts/python.exe scripts/batch_run.py --config config/batch_profiles/batch_run.toml
```
