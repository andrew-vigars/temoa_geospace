# Transport-restricted hub price sweeps

This set contains 24 scenario overlays adapted from the emissions-price cases
in `../carbon_sweeps/`: prices 0, 50, 100, 170, 250, and 350, with four transport
variants at each price.

| Filename prefix / suffix | Trucks | Explicitly disabled technologies |
| --- | --- | --- |
| `h2_off_price_*` | Enabled | `H2_PIPE`, `H2_TRUCK` |
| `h2_off_price_*_no_trucks` | Disabled by master switch | `H2_PIPE` |
| `h2_metoh_off_price_*` | Enabled | `H2_PIPE`, `METOH_PIPE`, `H2_TRUCK`, `METOH_TRUCK` |
| `h2_metoh_off_price_*_no_trucks` | Disabled by master switch | `H2_PIPE`, `METOH_PIPE` |

Every scenario retains CO2 EOS stack count 5, stack count 1 for other pipeline
technologies, pipelines enabled, 2025–2050, the projected 25 km grid, and
`all_km_dependent` pipeline impedance. Disabled pipeline stack settings remain
in the configuration but generate no technology rows. There is no storage
minimum; legacy gasoline is disabled and backup gasoline remains unpriced.
Prices retain the draft model-currency-per-tonne interpretation of the source
sweep. The 350 endpoint retains its source reference interpretation.

Removing H2 transport requires local hydrogen supply for methanol and gasoline
synthesis. Removing methanol transport as well requires the operating synthesis
chain to share a model node, approximating H2–methanol–gasoline hubs without
combined technologies or shared cost assumptions. Co-location is at model-node
resolution, not an identified industrial site. CO2 and gasoline transport, and
electricity transmission, remain available subject to the mode switches.
Backup gasoline can still substitute for the synthesis chain; these scenarios
do not require hub construction or guarantee solve feasibility.

## Build preparation

Build the set using the same provincial profile as the original price sweep:

```powershell
.venv/Scripts/python.exe scripts/build_schema_set.py --config config/build_profiles/provinces_orbits_quant.toml --scenario-dir registry/scenarios/hub_sweeps --log-dir data_files/processed/schema/build_logs/hub_sweeps_20261009 --exclude-snap-outliers
```

Use a fresh log directory for subsequent builds. The outlier option accepts
the audited graph-snap exclusions used by the provincial reference builds.
The builder discovers all 24 TOML files directly in this directory and writes
individual build logs plus `schema_set.csv` linking successful Gold databases.

## Completed schema build

All 24 Gold schemas were built successfully on 2026-10-09 using
`config/build_profiles/provinces_orbits_quant.toml` and the command above.
Total recorded schema build time was 367.2 seconds. The inventory is
`data_files/processed/schema/build_logs/hub_sweeps_20261009/schema_set.csv`;
each row links its database, manifest, and complete build log.

All exported databases passed builder validation. Additional read-only checks
confirmed 24 distinct databases with manifests, the intended transport
exclusions from technology, efficiency, and EOS investment tables, retained
transport options, and retained H2, methanol, and gasoline production
technologies. The audit is recorded beside the inventory as
`transport_validation.csv`. These checks validate schema construction, not
solve feasibility.

## Solver configuration and batch execution

All 24 solver configurations are prepared in `config/temoav4/hub_sweeps/`,
using database and manifest paths from the successful build inventory. Each
retains the corresponding original price-sweep run settings: perfect foresight,
EOS, Gurobi, `MIPGap = 0.01`, and `NodefileStart = 0.5`. The run wrapper assigns
node files to the run's own `nodefiles` directory unless an explicit directory
is configured. Node files manage solver memory; they are not saved restart
checkpoints. Input and output paths follow the original same-database convention;
the GeoCANOE run wrapper replaces them with an isolated working database.

`config/batch_profiles/batch_run.toml` now selects the 24 hub cases. Order is
H2 off with trucks, H2 off without trucks, H2 and methanol off with trucks,
then H2 and methanol off without trucks. Each group runs prices from 0 to 350.
The batch continues after failures, skips completed runs, and does not retry
failed runs automatically. The previous carbon-sweep profile is preserved as
`config/batch_profiles/carbon_sweeps.toml`, with an additional copy beside the
build inventory as `previous_batch_run.toml`. `solver_config_set.csv` beside
the inventory records the new solver configs and their schema paths.

Launch manually from the repository root:

```powershell
.venv/Scripts/python.exe scripts/batch_run.py --config config/batch_profiles/batch_run.toml
```

No MILP solves were launched during preparation.
