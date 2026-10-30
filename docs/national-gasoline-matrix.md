# Manual national gasoline/carbon scenario matrix

The matrix crosses minimum synthetic gasoline shares **0%, 20%, and 100%**
with the following six policy cases, producing **18 independent runs**:

| Carbon-policy case | Facility-emissions price (CAD2020/t CO2e released) | Minimum geological storage |
|---|---:|---:|
| Baseline | 0 | None |
| Net-zero comparison | 0 | 75% of included point-source emissions |
| Price 100 | 100 | None |
| Price 250 | 250 | None |
| Price 350 | 350 | None |
| Price 500 | 500 | None |

The net-zero comparison preserves the existing `net_zero.toml` **75% storage
requirement**. Its name does not assert full-system net zero. The four price
cases apply a price without a hard storage floor. None of these cases adds a
combustion charge to conventional gasoline (`fallback_gasoline_emission_factor = 0`).

All cases use national T4/GCC renewable inputs, the 25 km basemap, all 13
jurisdictions, the [2025, 2050) period, trucks and pipelines, one EOS curve per
pipeline technology, and pipeline impedance scope `none`. Infrastructure is
held common across policies; the older standalone baseline/net-zero scenario
files have different impedance choices and are not copied wholesale.

Conventional gasoline uses the validated Silver delivered-price proxy.
Synthetic requirements are national minima on delivered gasoline mass, not
identical physical blends at every city. Economic synthetic delivery can exceed
the minimum. Storage requirements and gasoline requirements remain independent;
the batch records failures and continues to later runs.

## Files

Only the reusable preparation script, tests and documentation are tracked.
The recipe, scenario overlays, solver configs, batch queue, inventory and Gold
databases are local run artifacts and ignored by Git. They remain editable
between experiments. `registry/model.toml`, `registry/scenarios/baseline.toml`
and the optional canonical `registry/scenarios/net_zero.toml` remain tracked.

- Recipe: `config/scenario_matrices/national_gasoline.toml`.
- Gold overlays: `registry/scenarios/national_gasoline_matrix/*.toml`.
- Solver configs: `config/temoav4/national_gasoline_matrix/*.toml`.
- Prepared database inventory: `config/scenario_matrices/national_gasoline_inventory.json`.
- Manual batch: `config/batch_profiles/national_gasoline_matrix.toml`.

Cases are ordered by synthetic share, then baseline, net-zero comparison, and
ascending carbon price. The batch has `continue_on_failure = true`,
`skip_completed = true`, and `retry_failed = false`. Solver configs use the
existing GeoCANOE TEMOA generator: Gurobi with EOS, a 1% MIP gap and the normal
node-file settings. No optimizations are launched during preparation.

## Run manually

From the repository root, after preparation has finished:

```powershell
.venv/Scripts/python.exe -m geocanoe.execution.batch --config config/batch_profiles/national_gasoline_matrix.toml
```

The existing runner creates isolated working databases and per-run outputs;
the new batch does not replace the previous active batch profile. Each run has
its own scenario ID, solver config, Gold database, manifest and gasoline price
audit. Check batch results for termination status and achieved policy outcomes.

## Regenerate

Draft and validate only the scenario overlays:

```powershell
.venv/Scripts/python.exe scripts/prepare_national_gasoline_matrix.py
```

Prepare fresh Gold databases, solver configs, inventory and runnable batch:

```powershell
.venv/Scripts/python.exe scripts/prepare_national_gasoline_matrix.py --build
```

Preparation does not solve. Regeneration writes new immutable Gold databases
and replaces the generated solver configs and batch queue with their new paths;
it does not delete earlier Gold artifacts. Edit the recipe before regeneration,
or edit solver options after preparation if needed.

The recipe explicitly retains the four offshore CO2 snap exclusions already
used by the national models (facility IDs 10142, 10284, 10354, 11599). The
preparer checks the audited coordinates and stops if a different set of outliers
is encountered. Preparation logs stay under ignored `.tmp/national_gasoline_matrix/`.

## Local recipe example

Create `config/scenario_matrices/national_gasoline.toml` locally from this
example, then edit the study choices before preparation. This TOML is not
tracked as a run configuration.

```toml
# 3 gasoline shares x 6 carbon-policy cases = 18 national runs.
# Preparation builds Gold databases/configs only; optimization is manual.
[matrix]
name = "national_gasoline_carbon_matrix"
build_profile = "config/build_profiles/canada_renewables_t4_gcc.toml"
synthetic_shares = [0.0, 0.20, 1.0]
carbon_prices = [100, 250, 350, 500] # CAD2020/t facility-origin CO2e released
include_net_zero = true
# Preserve the existing net_zero scenario's storage requirement, not a claim
# of whole-system net zero. Price cases have no hard storage floor.
net_zero_point_source_fraction = 0.75
scenario_dir = "registry/scenarios/national_gasoline_matrix"
solver_dir = "config/temoav4/national_gasoline_matrix"
batch_path = "config/batch_profiles/national_gasoline_matrix.toml"
inventory_path = "config/scenario_matrices/national_gasoline_inventory.json"
# Same four offshore outliers excluded by the preceding national models.
# Preparation fails rather than excluding any different outliers.
confirmed_co2_outlier_facility_ids = [10142, 10284, 10354, 11599]

[common]
period_start = 2025
period_end = 2050
gasoline_silver_config = "config/canoe_fuel/silver.toml"
grid_type = "projected"
resolution = 25
roads_enabled = true
pipelines_enabled = true
pipeline_impedance_scope = "none"
co2_pipeline_eos_stack_count = 1
# Preserve the existing facility-carbon boundary; no extra conventional
# combustion charge on top of the US delivered-price proxy.
fallback_gasoline_emission_factor = 0

[batch]
continue_on_failure = true
skip_completed = true
retry_failed = false
```
