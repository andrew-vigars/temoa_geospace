# Silver, Gold and TEMOAv4 workflow configurator

Open the unified interface from the repository root:

```powershell
.venv/Scripts/python.exe scripts/configure_workflow.py
```

It opens on **Silver construction**, using the existing
`config/build_profiles/canada_renewables_t4_gcc.toml` baseline unchanged:
25 km, all 13 provinces and territories, quantitative equal-weighted storage,
anchor-plus-fill gasoline proxies, and the T4 GCC renewable configuration.
Gold defaults to that same build profile. Opening the UI does not rebuild Silver.
Explicitly loaded profiles and recipes retain their settings.
The remaining tabs are **Gold scenario settings**,
**Generate Gold**, and **TEMOA configurations**. Configure and build Silver in
the first tab, then click **Use saved profile in Gold** to select that profile
and open Gold settings. This handoff does not build Gold or start a run. Prepare
Gold and configure/run TEMOA using the remaining tabs. You can stop after Silver,
start with existing Silver artifacts, or open an existing prepared Gold experiment.

Optional arguments include `--silver-config <profile.toml>`, `--config <model.toml>`,
`--tab silver|gold|temoa`, and `--experiment <prepared-experiment-directory>`.
The existing `configure_gold.py` command opens the same unified interface.
The window and running application's taskbar icon use the repository's
`GeoCANOE Logo` PNG. Windows launches use a GeoCANOE application identity so the
interface can appear separately from Python. Restart the interface to see icon
changes. A separately pinned Python shortcut retains its own shortcut icon.

## Independent Silver construction

The shared configurator includes a **Silver construction** tab. Silver, Gold,
and TEMOA are independent modules; the tabs organize their controls and do not
require completing an earlier module. Silver construction never generates Gold
or launches TEMOA. Use the existing Gold profile selector to choose a saved
Silver profile when you want to continue to Gold.

To open only the Silver module:

```powershell
.venv/Scripts/python.exe scripts/configure_silver.py
```

Optionally pass `--config config/build_profiles/your_profile.toml`. The module
loads the existing T4 GCC baseline by default without changing or building it.
The scrollable form groups labeled fields by TOML section, including nested
sections. Strings and numbers use entry fields; booleans and predefined categories
use checkboxes. Single-choice categories are mutually exclusive. Province,
connectivity-method and hydrography-category selections support multiple choices.
Road networks and strong/weak connectivity use mutually exclusive checkboxes.
The connectivity road layer and schema connectivity method are derived rather
than exposed as duplicate selectors. Existing multi-network profiles are preserved until you
select a network. `[roads.classes]` is hidden and retained unchanged in saved
profiles. Numeric lists use comma-separated values without TOML
brackets; projected resolutions are in kilometres and geographic
resolutions are in degrees. Loading a profile discovers its fields automatically.
Comments and unedited settings are retained in the saved TOML. The form also
exposes optional basemap selection and jurisdiction-specific additional hub counts.
Additional hubs use uppercase jurisdiction codes in the same alphabetical order
as study-area selections. Gasoline minimum and fill-candidate
population thresholds use mutually exclusive class checkboxes (1,000+, 30,000+,
100,000+), reflecting the supported Bronze classifications. Numeric fields remain
available for continuous settings such as grid resolution. Section notes explain
units, dependencies and modelling assumptions throughout the Silver form.
Hydrography polygons represent waterbody
footprints used by the area-based impedance calculation. Leave hydrography lines
disabled until a formal width/crossing heuristic and calibrated penalty exist:
centre-lines have no represented width, and the current NHN impedance stage
excludes line features even if line preprocessing is enabled.

Storage mode-specific controls are enabled only for the selected eligibility.
Quantitative capacity mapping is available only for quantitative eligibility;
all-mapped and qualitative use unlimited capacity. Inactive quantitative settings
are retained for reuse. **Equal weighted** divides each assessment's P50 capacity
equally among its mapped cells, producing per-cell caps. **Shared** sums assessment
P50 totals into one system-wide cap across eligible injection cells. **Unlimited**
does not apply a geological capacity cap. These are capacity assumptions, separate
from the Gold scenario's minimum-storage policy requirements.

**Save profile as** validates the profile and writes it to the chosen file.
**Validate inputs** invokes the existing Silver builder with `--validate-only`;
**Build Silver** invokes its complete workflow. Unsaved edits must be saved
before either operation. Progress and failures appear in the Silver log.
Operations lock editing and other execution controls until finished, to avoid
simultaneous writes to shared build artifacts. Keep the window open while running.

The builder retains its existing output locations and reuse/overwrite behavior.
Distinct filenames alone do not isolate datasets; choose profile identifiers and
study-area labels appropriately for structural variants. This UI does not create
an experiment, baseline/net-zero scenario, or automatic structural sweep. Those
remain explicit user choices. Multiple resolutions can be specified in one profile.

## Gold and TEMOA

Launch from the repository root:

```powershell
.venv/Scripts/python.exe scripts/configure_gold.py
```

The form starts from `registry/model.toml`. Every generated scenario includes
its complete model settings and is validated by the existing model loader.
**Load recipe** accepts a model TOML, a scenario overlay, or a new model-based
experiment recipe. Legacy gasoline matrix recipes retain their existing CLI.
Unrelated files such as `registry/economics.toml` are rejected when selected;
they are not model/scenario overlays. A rejected load preserves the current form.

## Configure and preview

1. On **Scenario settings**, use the **Experiment** box at the top to choose
   Single scenario or Sensitivity matrix. Choose
   an existing build profile and a new output directory. The default is
   `experiments/trial_<timestamp>/` in the repository root. Silver is not rebuilt.
   The optional **Auto-exclude all build outliers** checkbox accepts graph-snap
   exclusions for both site points and CO2 facilities across the experiment.
   It is off by default and is saved in the recipe and plan.
2. In the scrollable model form below, set the national minimum synthetic share, emissions
   policy (`none` or `emissions_price`) and storage requirement (`none`,
   `minimum_point_source_fraction` or `minimum_cumulative_activity`). Pricing
   and storage are independent; a scenario may use both.
3. Enter carbon prices in CAD2020/t released facility-origin CO2e. Enter storage
   fractions and synthetic shares as percentages. Cumulative storage targets
   are tonnes CO2e over the complete model period. Inactive policy quantities
   are set to zero in generated scenarios.
4. Review the sections named after `model.toml`: time, synthetic gasoline,
   finance, emissions, storage, basemap, transport modes and pipeline costs.
   The conventional gasoline Silver config is inherited from the model;
   it is an evidence source, not a scenario choice on this form.
5. Click **Review & generate** to validate and open **Generate Gold**. That
   screen contains the case table, batch execution settings, planning/preparation
   buttons and progress log. Click **Generate plan** to save the experiment.
   Reviewing or generating a plan does not build Gold or launch optimization.
   **Edit settings** returns to the form.

In sensitivity mode, comma-separated numeric values create independent matrix
axes (shares, carbon prices, storage targets, resolution, financing rates,
backup gasoline emissions and pipeline EOS stacks). Dropdown combinations such
as `none + emissions_price` add categorical cases. Inactive values are normalized
and duplicate effective scenarios are removed. For example, 3 shares, 2 positive
carbon prices with `none + emissions_price`, and one cumulative storage target
with `none + minimum_cumulative_activity` produce 18 cases. All matrix axes are
crossed; this is deliberately different from the old gasoline recipe's named,
mutually exclusive policy cases. Start/end years and transport switches are
shared settings in this first version.

Single scenario mode requires one active value per field. Its pricing and
storage choices remain independent. The current model supports only constant
emissions projection. Silver resolutions must already exist for the selected
build profile; preparation uses the existing builder's evidence checks.

## Plan bundle

The experiment contains:

- `README.md`: formatted case table, assumptions, commands and limitations.
- `plan.json`: full resolved models, matrix dimensions, input hashes, Git state,
  Python and installed package versions.
- `snapshots/`: controlled configuration, registry and Python implementation
  copies, including uncommitted changes. Large Silver/Gold datasets are not copied.
- `recipe.toml`: the frozen recipe with isolated scenario, solver, batch,
  inventory and log destinations.
- `scenarios/`: complete scenario TOMLs used by the Gold builder.

Changing form values disconnects the current plan. Generate a new plan before
preparing changed settings. Existing experiment directories are never replaced.
Bundles under `experiments/` are local and ignored by Git; retain them separately
with research outputs.

## Prepare and run

Click **Prepare Gold** to build each reviewed case. The two existing graph-snap
exclusion decisions are shown individually as popups when encountered, including
context, distances and the audit path. **Yes** excludes those outliers;
**No**, or closing the popup, stops the build. Nothing is approved implicitly.
The audit CSV and response are retained per case under `logs/` and
indexed in `preparation.json`, including on a declined/failed preparation.
With auto-exclusion off, each occurrence asks again. With auto-exclusion on,
those outlier prompts are accepted without popups; each decision is marked
`auto_exclude_all`, and the audit is still saved. Unrelated prompts remain
interactive. The setting accepts outliers beyond the existing build threshold;
it does not change that threshold.

For new UI experiments, preparation produces the Gold inventory and logs. Gold
databases remain in the builder's existing artifact directory, with their paths
and fingerprints recorded in the inventory. Successful preparation opens TEMOA configurations.
TEMOA working copies and `output_files` behavior are unchanged. Keep the window
open until an operation finishes.

## TEMOA configurations

This tab is a scrollable form generated from the installed TEMOA template, with
GeoCANOE defaults. It exposes run mode, solver name/options, extensions,
validation and output switches, time sequencing, SQLite settings and mode-specific
tables. Booleans are checkboxes, known choices use dropdowns, and numeric values
use text fields. Arrays and solver options use TOML notation, for example
`["eos"]` and `{MIPGap = 0.01, NodefileStart = 0.5}`. Switching solver clears the
previous solver's options so Gurobi parameters are not passed to a different solver.

Inactive settings are greyed out automatically:

- Mode-specific sections are enabled only for the selected `scenario_mode`.
  Normal perfect-foresight runs leave MGA, myopic, Morris, SVMGA and Monte Carlo
  settings disabled. These tables do not activate modes themselves.
- The myopic evolution script requires both myopic mode and `evolving = true`.
- Commodity-network plotting requires `source_trace`. Dual output is disabled
  for `appsi_highs`, which does not support that output.
- On the Gold settings screen, carbon-price fields require emissions pricing,
  storage quantities follow the chosen requirement, and pipeline cost controls
  require enabled pipelines.

Values stay in the form when disabled and return when re-enabled. Inactive
entries do not create sensitivity axes or block validation; unsupported or
dependent output switches are effectively false while disabled. Inactive TEMOA
mode tables may still appear with template defaults in generated configs and
remain unused by the selected execution mode. Busy operations temporarily lock
all fields, then restore their dependency-based states.

After preparation, the mapping table shows every Gold case. Alternatively, use
**Open prepared experiment** to select an existing experiment directory (including
earlier prepared pilot bundles), or launch directly:

```powershell
.venv/Scripts/python.exe scripts/configure_gold.py --experiment experiments/my_experiment
```

A folder containing only `plan.json`, `recipe.toml` and scenarios is not prepared
yet. Click **Prepare Gold** on Generate Gold before opening TEMOA configurations. After restarting
the UI, load the saved recipe and generate a new plan to continue from the form.

- Double-click a row or use enable/disable buttons to choose which cases run.
- **Generate TEMOA configs** maps each case to its Gold database, writes a config
  for every case, and writes the enabled flags into a new batch TOML. It does
  not rebuild Gold or run optimization. Results labels replace Gold-ID hyphens
  with underscores to satisfy TEMOA's naming contract.
- A named selection is saved under `temoa_runs/<name>/`, with `solver/`,
  `batch.toml`, `README.md`, `runset.json`, and Gold-manifest snapshots. JSON
  records mappings, selections, hashes, package versions and base solver options.
- Use a new selection name when changing settings or case selections. Earlier
  selections are never overwritten. Editing any TEMOA/batch settings or enabled
  flags disables **Run batch** until a new selection is generated. Gold does not
  need rebuilding. An all-disabled selection may be saved, but cannot be run
  from the UI.
- **Run batch** starts only the enabled cases, through the existing batch runner.
  Progress is visible on both the Gold and TEMOA screens.

**Load TEMOA settings** can copy settings from a configuration file; database
paths and scenario labels from that file are ignored because the mapping table
owns them. Solver installation, licenses and model-specific requirements for
advanced run modes are evaluated by the runner. The original CLI gasoline
preparer retains its combined Gold/config/batch behavior for compatibility.

From a terminal, preparation uses ordinary CLI questions instead of popups:

```powershell
.venv/Scripts/python.exe scripts/plan_gold_experiment.py --prepare experiments/trial_example
```

The generated README includes the exact preparation and batch commands. A failed
preparation cannot be retried in the same bundle; create a new plan.

## Reproducibility limits

Preparation verifies controlled input and frozen recipe hashes. It uses the
current checkout and installed environment; snapshots support recovery rather
than forming a standalone executable archive. Silver dataset identity remains
with the Gold manifests; planning does not verify dataset content. Solver
settings use the installed TEMOA defaults. External tool versions are not fully
captured. Output hashes identify prepared artifacts rather than guaranteeing
bitwise-identical future optimization results.
