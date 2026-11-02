# Gold scenario configurator

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
and fingerprints recorded in the inventory. Successful preparation opens tab 3.
TEMOA working copies and `output_files` behavior are unchanged. Keep the window
open until an operation finishes.

## TEMOA configurations (tab 3)

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
yet. Click **Prepare Gold** on tab 2 before opening it on tab 3. After restarting
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
