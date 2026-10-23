# Renewable Silver and Gold representation

## Configuration and execution

`src/geocanoe/preprocessing/renewables.py` registers Silver stage
`renewables_atlite`, depending on `basemaps`. It accepts the existing shared
build profile's study area, label, retention method and projected resolutions.
Enable it in a profile with `[renewables] enabled=true` and
`config="config/renewables/atlite.toml"`. It is disabled in the general sample
profile to preserve existing workflows. `sample_renewables.toml` is a committed
Ontario 25/50 km example with renewable Gold replacement enabled by its settings.

Supported resolutions are projected EPSG:3347 grids >=25 km. Finer resolutions
are rejected, not filled with synthetic detail. This is a support policy, not a
claim that ERA5 supplies isotropic 25 km weather. Geographic-degree basemaps
need a separate resolution policy and are currently rejected by this stage.

Build the selected basemaps before direct execution. For the existing local
25 km basemap/profile used for validation:

```powershell
python -m geocanoe.preprocessing.renewables --config config/build_profiles/provinces_orbits_quant.toml
```

For orchestrated use, enable renewables in your profile and run:

```powershell
python scripts/build_silver.py --config <profile.toml> --stages renewables_atlite
```

The stage's dependency rules require the selected basemaps to exist. To build
basemaps as well, include `basemaps` in the requested stages. The direct module
entry point explicitly executes renewables regardless of the profile enable flag.

`config/renewables/atlite.toml` selects 2018 weather, the verified national ERA5
Bronze cache, Census boundary, province subset (initially ON), technology registry,
ATB source audit, physical assumptions, cache locations, native I/O backend, and
model/cost years. Set `province_subset=[]` to use all profile jurisdictions; this
will require a larger Silver conversion before replacing a national Gold model.

## Sources and physical profiles

- Verify all 36 Bronze requests before conversion; Silver makes no CDS calls.
- Verify the ATB subset checksum and exact selectors. The local crosswalk uses
  WIND_GEN/ LandbasedWind Class 7 Technology 1 and SOLAR_GEN/ UtilityPV Class 3,
  Moderate, R&D, no tax credits, 20-year financial recovery period. This is a
  declared scenario selection, not an automatic replacement for ATB 2024 Market.
- Retain raw ATB OCC and fixed O&M selections and units. No ATB LCOE is used as an
  additional generation charge; no ATB CF uplift is applied to physical profiles.
- Physical wind uses the pinned NREL T1 6 MW / 170 m / 115 m reference curve and
  logarithmic height interpolation. The 13.4% plant loss is inherited as an
  explicit pilot sensitivity. ATB 2025 describes T1 at 2035, while the default
  selected cost projection is 2030: this remains a declared reference-technology
  scenario, not an exact dated plant design or calibrated Canadian fleet.
- Solar uses the CSi horizontal single-axis tracking proxy, ILR 1.34, inverter
  efficiency 0.96, with no additional plant losses. Bifacial response,
  backtracking, snow/soiling, degradation and fleet calibration remain open.
- Province-filter Census polygons at the reader, retain basemap region IDs,
  centroid-select cells and clip production footprints to selected jurisdictions.
- Subset national NetCDF arrays spatially before loading values into memory.
  Monthly extraction uses temporary files; original payloads remain untouched.
- Prepare weather with Atlite 0.7.0's ERA5 logic. Native overlap matrices use
  physical EPSG:3347 areas for a uniform capacity-density resource baseline.
  Apply turbine curves and PV AC clipping before regional averaging.
- Reject incomplete polygon coverage and invalid CF values. Weather and monthly
  regional caches have checksums and physical/geometry/source identities.
- Require exactly 8,760 continuous UTC hours for 2018 before annual means. No
  time clustering is used. Different projected resolutions reuse prepared
  weather when their buffered extent and source inputs match.

Outputs under `data_files/processed/renewables/<build-id>/<grid-key>/<run-id>/`:
monthly regional hourly NetCDFs; annual CF CSV and GeoPackage; PNG map preview;
ATB selected-row/crosswalk audits; optional legacy-site comparison;
`renewable_resources.csv` with CFs, costs and a separate LCOE ranking diagnostic; source and
coverage manifests; and Temoa-format CSVs under `temoa_inputs/`.
`selected_run.json` identifies the selected immutable CF run. Parameter/currency
exports can be refreshed without weather conversion using
`--refresh-output <run-dir>`; source checks reject physical assumption changes.

## Costs and Gold replacement

Registry technologies are in `registry/techs.csv`, not technologies.csv. WIND_GEN
and SOLAR_GEN are explicit annual technologies. `sites_full.csv` is a legacy
LCOE/max_elec table, not a CF source, and is retained only for legacy-mode builds
and an optional comparison audit. It is not overwritten with incompatible data.

Gold tables encode: MW capacity (AC for PV), MWh activity, 8,760 MWh/(MW year),
resource-input efficiency 1, assumed 30-year life, investment and fixed O&M,
zero variable O&M (explicit assumption because selected ATB rows do not report
it), and `limit_annual_capacity_factor` rows. PV module conversion efficiency
is already in Atlite; it is not multiplied into Temoa efficiency again. Annual
availability does not represent storage dispatch or hourly adequacy.

`registry/renewables/currency_basis.toml` declares native ATB USD2023.
The shared `registry/economics.toml` converts currency first using the source-year CAD/USD exchange entry, then rebases using Canada's GDP implicit price index from
Statistics Canada table 36-10-0223-01: `USD2023 * 1.225042 * 104.3/123.2`.
Exports use CAD2020 dollars, not millions. Index observations for 2000–2024,
selectors, source checksum and exchange-table provenance are versioned in the registry.
See `docs/economics.md` for scope and the preconverted H2 workbook exception.
Existing immutable runs retain their original CPI conversion until explicitly
refreshed; changing this policy does not rewrite historical bundles.

Sources:
https://www.bankofcanada.ca/rates/exchange/annual-average-exchange-rates/
https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810000501
https://atb.nlr.gov/electricity/2025/definitions
https://atb.nlr.gov/electricity/2025/land-based_wind

When profile renewables are enabled and `[gold].enabled=true`, schema building
resolves and pins the selected Silver bundle, incorporates its contract in the
schema fingerprint, and validates its checksums, years, currency, and complete
selected-node coverage before applying replacement. It removes legacy ELC_GEN
technology, costs, limits and other references, then inserts WIND_GEN/SOLAR_GEN
rows. It does not retain legacy LCOE as another operating charge or transfer
legacy wind resource limits to solar. Renewable-mode schema loading/snap logic
skips legacy wind-site input; legacy builds remain available with renewables off.
All candidate rows are also tested on a disposable Temoa v4 schema with foreign
keys enabled. No existing production SQLite database is modified by Silver.

Gold requires every selected node to have CFs. The initial Ontario subset cannot
replace ELC_GEN across a larger multi-province graph. `merge_ready=false` in the
Silver contract indicates that it is not an unconditional merge artifact; the
Gold adapter performs target-specific validation. Land eligibility, installed
fleet weighting, wind/solar potential caps, grid/spur costs and empirical
calibration remain future additions. Unconstrained resource-baseline builds must
not be interpreted as deployable land potential.

CANOE comparison: `new_wind_solar.py` uses LCOE to sort resource bins, then
separate ATB investment/fixed-O&M/CF projections and resource-input efficiency 1.
GeoCANOE instead derives spatial CFs from its own ERA5/Atlite conversion and
keeps those technology and currency assumptions in local versioned registries.

## Initial validation

The existing prov_quant 25 km basemap produced 1,564 Ontario polygons, each with
8,760 hours. The initial uncalibrated resource CF means across equal-weight regions
are approximately 0.285 wind and 0.247 PV AC. These are diagnostics, not a
capacity-weighted provincial fleet statistic or comparison against observations.
Gold parameters were refreshed to WIND_GEN/SOLAR_GEN with CAD2020 currency and
passed Temoa v4 schema/foreign-key validation. Replacement tests verify missing
node coverage fails without mutating any encoded table.


The replacement resource table keeps investment and fixed O&M separate, supplies
an assumed resource efficiency of 1 and zero VOM, and calculates an optional
annuity LCOE diagnostic at the configured discount rate (default 3%).
It labels potential as not estimated. This provides a traceable successor to
legacy LCOE site ranking without manufacturing build limits or charging LCOE
in the optimization in addition to CAPEX/OPEX.

### CANOE representative wind mapping

The default `[wind_mapping]` configuration follows CANOE's `new_wind_techs`:
T1 uses Class 7, T2 uses Class 8, and T3 uses Class 9. These selections are
validated against the native ATB resource/turbine columns. Cost selection uses
the representative row matching the configured physical turbine reference.
Exports retain the complete `atb_wind_class_mapping.csv` audit and the selected
`canoe_wind_class_mapping.csv`. Changing the representative cost policy does
not introduce ERA5 wind-speed classification or class-aware spatial weighting.

CANOE obtains T1/T2/T3 capacity fractions from its external Cluster Composition
dataset, not from this mapping. We do not invent those fractions or transfer
Ontario cluster mixtures to other provinces. The current physical profile is
still the T1 baseline. A mixed-fleet extension needs explicit capacity fractions
and validated T2/T3 curves. Ordinary area-weighted weather aggregation over
basemap polygons remains in place. No ATB CF uplift is applied automatically.

Mapping provenance: sister repo `canoe-electricity-main/input_files/params.toml`,
`[new_wind_techs]`; weighting logic in `canoe_electricity/new_wind_solar.py`.
The local config is self-contained: CANOE is not a runtime dependency.

### Regional investment options

The configured representation is `one_option_per_region`: each basemap region has exactly one WIND_GEN and one SOLAR_GEN investment option. Hourly CFs vary with local weather, but the physical configuration is fixed for the run. Costs are assigned per option and do not increase with cumulative deployment within a region. No resource bins or inferred turbine mixtures are introduced. Wind currently uses the documented T1 baseline and CANOE representative Class 7 ATB costs; solar uses the configured PV baseline. Regional capacity limits still require a separate land-eligibility and deployment-density assessment.

### Separate Ontario validation run

`config/renewables/ontario_validation.toml` uses the same prepared-weather cache
and immutable Bronze inputs, with a distinct run identity under the shared renewables output root. Reproduce it with:

```powershell
python -m geocanoe.preprocessing.renewables --config config/build_profiles/provinces_orbits_quant.toml --settings config/renewables/ontario_validation.toml
python scripts/validate_renewables_run.py <completed-run-directory> --previous-output <previous-run-directory>
```

The build profile must point to an existing 25 km basemap. Validation checks
full-year hourly coverage, CF bounds, exported annual means, one option per
region and technology, checksums, and native ATB-to-CAD2020 cost conversion.
It writes `validation_report.json`; this is internal consistency validation,
not calibration against observed generation. Neither command merges Gold.

### Deferred land constraints and national run

Land-cover eligibility and maximum build capacity are deferred until national
CF coverage has been checked. `[capacity_limits].enabled=false` is explicit;
no whole-polygon MW potential is inferred. An optional later input has columns
`region,tech,eligible_area_km2,density_mw_per_km2,basis,source` and requires both
technologies for every region. It exports MW bounds as area times density with
source provenance retained. Wind and solar independent area limits would not
by themselves enforce joint land competition.

The dedicated national profile `config/build_profiles/canada_renewables.toml`
covers all 13 provinces and territories, 25 km, and uses
`config/renewables/canada.toml`. Gold replacement remains disabled there.
These runs produce resource profiles, not validated buildable capacity.

### Monthly diagnostic gallery

Silver automatically writes annual and twelve monthly paired wind/PV maps to
`<output_dir>/preview/<build>_<grid>_<run>/` with consistent CF limits 0â€“1.
Disable with `[previews].monthly=false`. To generate previews for an existing
run without conversion, use `python -m geocanoe.preprocessing.renewables_preview <run-directory>`.
The gallery includes per-region monthly means, hourly min/max and 5th/95th
percentiles, and RMS error from replacing each hour with its monthly mean.
Month means are weighted by actual hours when checked against annual means.
This preserves energy but loses hourly timing, ramps and resource/demand
coincidence; maps alone do not quantify those effects. Gold inputs are unchanged.

### Shared output root

All renewable builds use `data_files/processed/renewables/`. Completed products
are separated by `<build_id>/<grid_key>/<run_id>/`, with selected-run pointers
per build/grid. Prepared ERA5 remains under `weather_cache/`; annual/monthly
preview galleries live under `preview/<build>_<grid>_<run>/`. Ontario pilot and
validation runs coexist under `prov-quant/`; national runs use `canada-vre/`.
Changing the output location does not alter historical source hashes recorded
in completed run manifests. Those hashes describe the settings at generation.

### Workflow and validation at migration

Run the national workflow from the repository root with the environment active:

```powershell
geocanoe-build-silver --config config/build_profiles/canada_renewables.toml
```

The complete orchestrator still runs `legacy_inputs` for compatibility; it is
not a renewable CF source. Remove that stage only after validation of the
replacement representation and its Gold integration. The national profile
currently disables renewable Gold replacement and land-capacity limits.

The 2018 national 25 km reference run contains 15,273 regions and 8,760 UTC
hours, with one wind and one solar option per region. Its preview gallery has
one annual and twelve monthly paired maps; hour-weighted monthly means
reproduce annual CFs. National and Ontario table checksums and relocated run
pointers were checked after consolidation under the shared root.

Migration checks: 106 relevant regression tests passed; Ruff and Git whitespace
checks passed. Three pandas concatenation FutureWarnings remain in existing
schema paths. These checks establish internal consistency, not generation
calibration or a completed production Gold solve.

Notebook source files and their helpers remain in `notebooks/`. Retired
notebook-only external pilot configs, output copies, documentation and tests
were removed. Production registry resources, Bronze caches, and Silver tests
remain. Generated data and preview images are local outputs, not commit inputs.
