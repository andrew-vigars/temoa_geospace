# Overnight national T4 + GCC refresh

Use `config/build_profiles/canada_renewables_t4_gcc.toml` with
`config/renewables/canada_t4_gcc.toml`. The build retains all 13 jurisdictions,
the existing 25 km centroid grid, 2018 weather, ATB Moderate 2030 costs,
CAD2020 conversion, land exclusions, and annual model representation.
Wind uses fixed spatial T1/T2/T3/T4 weights; investment costs include OCC + GCC.
T1 covers the ATB Classes 1–7 design range, using Class 7 as its representative
cost row; T2, T3, and T4 use Classes 8, 9, and 10 respectively.

The profile intentionally retains `profile.id = "canada-vre"` and study-area
label `canada_renewables` so tomorrow's Gold builds reuse the existing demand,
roads, storage, and other spatial products. Its renewable output root is
`data_files/processed/renewables/t4-gcc/`, so it does not change the previous
renewable selection under `data_files/processed/renewables/canada-vre/`.
Schema selection is noninteractive and fixed to the 25 km national basemap.

## Tonight: Silver only

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

## Tomorrow: build both scenario databases

Use the new profile with the existing policy overlays:

```powershell
.venv\Scripts\python.exe scripts/build_schema.py --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/renewables_pair/baseline.toml
.venv\Scripts\python.exe scripts/build_schema.py --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/renewables_pair/price250.toml
```

Use the exact SQLite filenames printed by those builds. Their fingerprints
include the selected renewable contract; do not guess the new filenames.
Confirm each SQLite sidecar manifest's
`resolved_gold_configuration.renewables_bundle_dir` points into `renewables/t4-gcc/`.
Then update `input_database` in the corresponding solver configurations:

- `config/temoav4/renewables_pair/baseline.toml`
- `config/temoav4/renewables_pair/price250.toml`

The existing `config/batch_profiles/renewables_pair.toml` refers to those solver
configurations and can be used after their database paths are updated. Reserve
participation, temporal resolution, and the two scenarios' policy assumptions
are unchanged. The new renewable profile tables/plots are produced after a
successful solve.
