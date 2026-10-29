# National T4 wind representation and GCC sensitivity

## Decision context

This structural sensitivity was created after the national renewable build
showed that a T1/T2/T3-only mapping can remove or materially underrepresent
wind supply in lower-resource western regions. Adding the T4 physical power
curve and mapping provides an explicit low-wind design rather than treating
those cells as having no suitable wind representation.

The run also adds ATB grid connection costs (GCC) to overnight capital costs
(OCC). The two changes are evaluated together in this run, so any reported
difference from the earlier national renewable case must be decomposed before
attributing it solely to T4 resource coverage or solely to GCC.

This note records why the structural alternative exists, the configuration
used to construct it, and the artifacts needed to audit it. Generated Silver,
Gold, and solver outputs remain under the local model-output directories and
are not committed here.

> Updated after the 2026-10-27 branch refresh. The former renewable-pair
> scenarios, solver configurations and batch profile referenced by this note
> were removed; use the currently committed scenarios or create a reviewed
> local overlay.

Use `config/build_profiles/canada_renewables_t4_gcc.toml` with
`config/renewables/canada_t4_gcc.toml`. The build retains all 13 jurisdictions,
the existing 25 km centroid grid, 2018 weather, ATB Moderate 2030 costs,
CAD2020 conversion, land exclusions, and annual model representation.
Wind uses fixed spatial T1/T2/T3/T4 weights; investment costs include overnight
capital costs (OCC) + grid connection costs (GCC).
T1 covers the ATB Classes 1–7 design range, using Class 7 as its representative
cost row; T2, T3, and T4 use Classes 8, 9, and 10 respectively.

The profile intentionally retains `profile.id = "canada-vre"` and study-area
label `canada_renewables` so subsequent Gold builds reuse the existing demand,
roads, storage, and other spatial products. Its renewable output root is
`data_files/processed/renewables/t4-gcc/`, so it does not change the previous
renewable selection under `data_files/processed/renewables/canada-vre/`.
Schema selection is noninteractive and fixed to the 25 km national basemap.

## Build Silver only

From the repository root in PowerShell, run:

```powershell
.venv\Scripts\python.exe scripts/build_silver.py --config config/build_profiles/canada_renewables_t4_gcc.toml --stages renewables_atlite --reuse-existing-basemaps
```

The command validates the persisted basemap and runs the renewable layer only.
It reuses checksum-matching prepared weather; the new output location requires
new land-weight products, annual wind assignment, and monthly profile conversion.
There is no additional weather download, Gold build, or model solve in this command.
Keep the machine awake and avoid running another renewable build against this
same profile/output directory. Rerunning the command resumes matching monthly
checkpoints in the same run identity.

The completed selection is:

```text
data_files/processed/renewables/t4-gcc/canada-vre/25km_centroid/selected_run.json
```

The pointer records the immutable run directory. Audit it after successful completion:

```powershell
$t4Selection = Get-Content -Raw data_files/processed/renewables/t4-gcc/canada-vre/25km_centroid/selected_run.json | ConvertFrom-Json
.venv\Scripts\python.exe scripts/validate_renewables_run.py $t4Selection.output_dir
```

Check `validation_report.json`, `wind_resource/regional_mix.csv` (including
`T4_fraction`), `atb_selected_rows.csv` (including GCC), and
`temoa_inputs/schema_validation.json` in that run directory.

## Build current scenario databases

Use the profile with committed policy overlays, for example baseline and the
sample emissions-price case:

```powershell
.venv\Scripts\python.exe scripts/build_schema.py --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/baseline.toml
.venv\Scripts\python.exe scripts/build_schema.py --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/sample_emissions_price.toml
```

Use the exact SQLite filenames printed by those builds. Their fingerprints
include the selected renewable contract; do not guess the new filenames.
Confirm each SQLite sidecar manifest's
`resolved_gold_configuration.renewables_bundle_dir` points into `renewables/t4-gcc/`.
Generate a local solver configuration if needed, then pass each exact database
path explicitly:

```powershell
geocanoe-init-temoa-config
geocanoe-run --database <printed-gold.sqlite> --config config/temoav4/config_sample.toml --non-interactive
```

There is no committed renewable-pair batch profile in the current tree. Create
one only after verifying the database and solver-config paths. Renewable
profile tables and plots are produced after a successful solve.
