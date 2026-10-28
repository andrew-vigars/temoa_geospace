# Renewable Silver and Gold representation

## Manual national regeneration

From the repository root, install optional dependencies and use the standard
Silver entry point with the national profile. Omitting `--stages` runs the entire
Silver workflow, including renewables. No task-specific regeneration runner is
required. Gold replacement remains a separate operation.

```powershell
.venv\Scripts\python.exe -m pip install -e ".[renewables]"
.venv\Scripts\python.exe scripts/build_silver.py --config config/build_profiles/canada_renewables.toml
```

Profile: `config/build_profiles/canada_renewables.toml`; settings:
`config/renewables/canada.toml`. National settings use PV ILR 1.32 to match
ATB 2025. Other PV-proxy and technology-year limitations documented below remain.
For exclusion previews alone, the renewable module supports `--land-preview-only`
as documented below. Inspect `land_exclusion/` in the printed run directory.
The full workflow reuses validated exclusion/month caches under the same run
identity. Changing inputs/settings/code creates a different run. Stopping during
geometry preparation may require repeating that preparation, but does not remove
older completed runs. Renewable outputs are under
`data_files/processed/renewables/canada-vre/25km_centroid/<run-id>/`.
The stage validates exported tables against the Temoa schema before selecting the
run. For an additional independent audit, use
`python scripts/validate_renewables_run.py <completed-run-directory>`; this writes
`validation_report.json`. It is a reusable validator, not a temporary task runner.

## Resource-assigned wind (national default)

`config/renewables/canada.toml` now uses `wind_representation = "resource_assigned"`.
The national workflow first derives annual mean wind speed at 110 m from the
complete configured ERA5 year, using Atlite logarithmic extrapolation. This is a
single-year resource proxy, not a long-term climatology or subgrid wind atlas.
It then assigns native weather cells using the ATB 2025 design thresholds:
T1 at >=7.10 m/s, T2 at >=6.53 and <7.10, T3 at >=5.90 and <6.53.
Values exactly at a breakpoint use the higher-resource class. These are
resource-speed thresholds, not IEC turbine safety classes. The mapping describes
ATB future turbine designs (2035–2060); using their curves with the currently
configured 2030 cost year remains a representative-technology assumption.

Sources: https://atb.nlr.gov/electricity/2025/land-based_wind

The baseline `low_resource_policy = "t4"` assigns sites below 5.90 m/s to
ATB Class 10 / T4 (6 MW, 196 m rotor, 140 m hub). These sites remain subject
to the same land exclusions and deployment density as the other wind designs.
T4 uses its own packaged physical curve and ATB cost rows. Old Silver bundles
must be regenerated, followed by a Gold rebuild and solve; existing solved
outputs retain their original assumptions.

For every model region, eligible area assigned to each design defines a fixed
capacity fraction (the same MW/km² is assumed for each design). Hourly output
uses the design assigned at each weather cell before eligible-area aggregation.
CAPEX and fixed O&M use those same fractions and ATB Moderate costs. Gold emits
one `WIND_GEN` option per region with a resource-adjusted MW cap. Regions with
no represented wind have zero MW, zero CF and zero weighted wind costs.
This fixed mix does not let the optimizer preferentially build its best subset.

`wind_resource/` contains the annual mean-speed/design raster in `resource.nc`,
regional mixture CSV/GPKG, a preview map, omitted low-resource area, adjusted
capacity assumptions and checksummed manifests. The existing `land_exclusion/`
products retain physical land eligibility before the resource restriction.
Annual means are cached; monthly CFs retain their existing checkpoints.
Prepared-weather cache keys now depend on preparation functions rather than
unrelated conversion changes; source/bounds-identical legacy weather caches
are reused with checksum and full-month temporal validation.

To rerun only this layer after the running process has finished or been stopped:

```powershell
.venv\Scripts\python.exe scripts/build_silver.py --config config/build_profiles/canada_renewables.toml --stages renewables_atlite --reuse-existing-basemaps
```

Changing settings creates a new renewable run identity. The prior run remains
available. No new national regeneration is launched as part of this change.

## Selectable wind designs (optional fallback)

The default `[technology].wind_representation="selectable_t1_t2_t3"` exports
`WIND_GEN_T1`, `WIND_GEN_T2` and `WIND_GEN_T3` plus one `SOLAR_GEN` option per
region, without resource clustering or additional bins. Set `t1_only` to retain
the original `WIND_GEN` fallback. This supersedes older T1-only descriptions below.
`atb_scenario="Moderate"` is explicit in the renewable TOML files, selected-row
audits and Gold contract. Designs use CANOE's representative ATB classes 7/8/9.

Install `pip install -e ".[renewables]"` to include `turbine-models==0.2.2`.
The published package supplies CSV curves and specifications through `Turbines`;
there is no adjacent repository or runtime network request. T1: 6 MW, 170 m rotor,
115 m hub; T2: 8.3 MW, 196 m rotor, 130 m hub; T3: 3.3 MW, 148 m rotor, 100 m hub.
Source power in kW is converted to MW and normalized to each design's rating.
The same explicit endpoint shutdown and plant-loss assumptions apply to all.
Metadata checks verify design tag, rating, rotor, hub height and finite curves.
Package version and source-file SHA-256 hashes enter run/cache identity.

Silver computes `wind_cf_t1/t2/t3` with each design's own curve/hub height and
the same eligible-land weights. `wind_cf` remains a T1 diagnostic alias only;
Gold and multi-design previews use the separate profiles. Solar remains single
axis. Each wind option has matching ATB investment/fixed-O&M costs and efficiency
1.0. There is no automatic ATB CF uplift on top of explicit physical curves.

Gold defines `WIND_RESOURCE` through `tech_group`/`tech_group_member`; each
region has one upper bound on the sum of all three wind capacities, plus its
separate solar bound. This assumes the same Palmer-Wilson MW/km² density for
all three wind designs. It does not model different spacing densities or joint
wind/solar land competition. Unbounded multi-design bundles are rejected, and
Gold verifies complete group membership before replacing encoded rows.

Existing technology-year caveats remain: the configured cost projection is 2030,
Gold vintage is 2025, and these reference wind designs are associated with ATB's
2035 characterization. They are declared scenario inputs, not a fully matched
dated plant specification. The earlier solar ILR/performance-proxy caveats also
remain. No existing CF runs or production Gold database are rewritten by this change.

## Land-exclusion implementation (current)

The following supersedes the historical deferred-land status below. Renewable
settings now enable `[land_exclusion]` by default. `enabled=false` retains the
unmasked area-weighted CF baseline; external `[capacity_limits]` remains a separate
opt-in input and cannot be enabled at the same time as generated land bounds.

Baseline scenario: exclude NALCMS wetland (14), built-up (17) and water (18)
for wind; additionally exclude cropland (15) for solar. NoData and unknown codes
are excluded using an explicit 1–19 class whitelist. Legend references:
https://www.cec.org/files/pulications/cec_nalcms-en_final.pdf and
https://developers.google.com/earth-engine/datasets/catalog/USGS_NLCD_RELEASES_2020_REL_NALCMS.
Protected land uses terrestrial/freshwater CPCAD status 1 by default; OECM and
interim categories can be selected explicitly in `protected_status_codes`.
Both technologies exclude existing wind and Canada-labelled solar footprints.

Wind footprints group NRCan points by province and project name, take convex
hulls in EPSG:3347 and buffer by a configurable 100 m. This is a scenario
approximation, not a regulatory setback or cadastral boundary. Single turbines
and linear projects retain nonzero footprints. Solar uses the supplied TZ-SAM
polygons; original-source geometry is unchanged. Silver applies make-valid to
protected/solar geometries and preserves the derived vectors for audit.

Atlite 0.7.0 `ExclusionContainer` uses a configurable 100 m analysis raster;
this is distinct from native 30 m land cover and 0.25-degree ERA5 weather.
`availabilitymatrix` results are batched into sparse region/weather-cell km²
weights. Wind and solar use independent weights. Regional bounds use those same
eligible areas multiplied by 1000/368.3 MW/km² for wind and 1000/34.4 MW AC/km²
for solar (Palmer-Wilson 2019 medians, including footprint and spacing). PV is
restricted to horizontal single-axis tracking for this baseline. No joint land
competition constraint is imposed between future wind and solar deployments.

All-zero eligible regions retain their region IDs, zero capacity bounds and zero
CFs; zero CF is a model placeholder for an unavailable investment option, not a
physical resource estimate. Wind curves and solar AC clipping are still applied
at weather-cell level before aggregation. Source hashes, policies, geometry,
weather-grid identity and implementation hashes protect against cache mixing.
Parameter-only refresh rejects changed land inputs and validates land outputs.

Before CF conversion, `<run>/land_exclusion/` contains derived exclusion vectors,
regional eligible-area/fraction GeoPackage, sparse weights, generated capacity
assumptions and wind/solar previews. Each preview combines a regional summary
with the actual applied raster mask for the first region. Maps are scenario
eligibility diagnostics, not a comprehensive regulatory compliance assessment.

To review exclusion products before recalculating CFs:

```powershell
python -m geocanoe.preprocessing.renewables --config config/build_profiles/sample_renewables.toml --land-preview-only
```

This prepares/reuses the first month's weather grid to align masks, then stops
before physical conversion, Gold export or changing the selected CF-run pointer.
Omit `--land-preview-only` to build the new hourly/annual CF bundle and MW bounds.
Prepared weather can be reused; prior CF caches are not overwritten. This is
part of `renewables_atlite`, not an independently registered Silver stage.

GEBCO is not acquired or used. The baseline does not use ERA5 geopotential height
to decide land eligibility and has no elevation, slope or offshore-depth rule.
Atlite recommends GEBCO when accurate heights/bathymetry are required:
https://atlite.readthedocs.io/en/master/examples/using_gebco_heightmap.html.
Adding such rules requires a separately reviewed Bronze source. GEBCO surface
elevation is not turbine hub height. The standalone renewable CLI enables INFO
logging, captures warnings and retains deprecation warnings, following Atlite's
logging example. GeoPandas plots projected shapes; Atlite plots applied masks.

Validation: synthetic Atlite raster exclusion, independent technology weighting,
zero-area behavior, wind-project grouping and PV clipping tests pass. A real
two-region southern Ontario mask pilot produced approximately 782.75 km² wind
and 34.40 km² solar eligible area in total. This is not a national CF rebuild,
empirical generation calibration or completed production Gold solve.

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
python -m geocanoe.preprocessing.renewables --config config/build_profiles/sample_renewables.toml
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
- Retain raw ATB OCC, GCC and fixed O&M selections and units. Investment cost
  is OCC + GCC, converted together from USD2023 per kW to the target currency
  per MW. Resource-assigned wind uses the same design fractions for all three
  components. No ATB LCOE is used as an
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
fleet weighting and empirical calibration remain separate from the resource baseline.
The national resource-assigned mode adds land-based capacity caps and ATB GCC;
site-specific distance-based grid/spur costs remain outside this representation. Unconstrained resource-baseline builds must
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
T1 uses Class 7, T2 uses Class 8, and T3 uses Class 9; national resource-assigned
wind also uses Class 10 for T4. T1 is the turbine design for ATB Classes 1–7;
Class 7 is its representative cost row, not a restriction to Class 7 sites. These selections are
validated against the native ATB resource/turbine columns. Cost selection uses
the representative row matching the configured physical turbine reference.
Exports retain the complete `atb_wind_class_mapping.csv` audit and the selected
`canoe_wind_class_mapping.csv`. Changing the representative cost policy does
not introduce ERA5 wind-speed classification or class-aware spatial weighting.

CANOE obtains T1/T2/T3 capacity fractions from its external Cluster Composition
dataset, not from this mapping. We do not invent those fractions or transfer
Ontario cluster mixtures to other provinces. National resource-assigned wind
instead derives local T1/T2/T3/T4 fractions from annual ERA5 wind and eligible
land, and uses each design's physical curve before regional aggregation.
The optional `t1_only` mode retains the original baseline. No ATB CF uplift
is applied automatically.

Mapping provenance: sister repo `canoe-electricity-main/input_files/params.toml`,
`[new_wind_techs]`; weighting logic in `canoe_electricity/new_wind_solar.py`.
The local config is self-contained: CANOE is not a runtime dependency.

### Regional investment options

The configured representation is `one_option_per_region`: each basemap region has exactly one WIND_GEN and one SOLAR_GEN investment option. Hourly CFs vary with local weather, but the physical configuration is fixed for the run. Costs are assigned per option and do not increase with cumulative deployment within a region. No resource bins are introduced. National resource-assigned wind combines T1/T2/T3/T4 with fixed local eligible-area fractions and matching ATB costs; solar uses the configured PV baseline. Regional capacity limits use the land-eligibility and deployment-density assessment.

### Separate Ontario validation run

`config/renewables/ontario_validation.toml` uses the same prepared-weather cache
and immutable Bronze inputs, with a distinct run identity under the shared renewables output root. Reproduce it with:

```powershell
python -m geocanoe.preprocessing.renewables --config config/build_profiles/sample_renewables.toml --settings config/renewables/ontario_validation.toml
python scripts/validate_renewables_run.py <completed-run-directory> --previous-output <previous-run-directory>
```

The build profile must point to an existing 25 km basemap. Validation checks
full-year hourly coverage, CF bounds, exported annual means, one option per
region and technology, checksums, and native ATB-to-CAD2020 cost conversion.
It writes `validation_report.json`; this is internal consistency validation,
not calibration against observed generation. Neither command merges Gold.

### National land constraints and run

The earlier deferred-land migration state has been superseded by the
land-exclusion implementation above. The national settings now derive wind and
solar eligible area from NALCMS land cover and CPCAD, apply configured deployment
densities, and export MW bounds with source provenance. External
`[capacity_limits]` remain disabled because generated land bounds are active;
the two mechanisms cannot be enabled together. Wind and solar retain separate
area limits and therefore do not enforce joint land competition.

The dedicated national profile `config/build_profiles/canada_renewables.toml`
covers all 13 provinces and territories, 25 km, and uses
`config/renewables/canada.toml`. Renewable Gold replacement and generated
land-capacity bounds are enabled; Gold requires complete coverage of every
selected node and rejects an incomplete or unvalidated bundle.

### Monthly diagnostic gallery

Silver automatically writes annual and twelve monthly paired wind/PV maps to
`<output_dir>/preview/<build>_<grid>_<run>/` with consistent CF limits 0–1.
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
not a renewable CF source. When the national renewable bundle is selected,
Gold replaces the legacy `ELC_GEN` representation with validated wind and solar
technologies and generated land-capacity limits.

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


### Native-mask area accounting and recovery

Land weights integrate Atlite `ExclusionContainer.compute_shape_availability` masks
in EPSG:3347 at the configured raster resolution. Boundary pixels are intersected
with the production polygon; their area is assigned to the weather cell containing
the pixel centre. This preserves eligible area while approximating weather-cell
allocation at the exclusion raster resolution. It avoids multiplying GDAL-averaged
availability fractions by whole projected weather-cell areas, which was inaccurate
for small model regions. The raster classification itself remains a raster-scale
approximation; it is not a parcel survey.

Completed batches are saved atomically under `land_exclusion/batches/<identity>/`
with checksums. Re-running the same stage resumes those batches. Changed source,
policy, implementation, region or weather-grid identities do not reuse old batches.
Every newly computed region is checked before its batch is committed, with its
region identifier and area in any error. Existing prepared weather caches remain
usable. Resume the canonical workflow with `--stages renewables_atlite --reuse-existing-basemaps`; do not
remove the successful upstream Silver products.

Reference: https://atlite.readthedocs.io/en/master/examples/landuse-availability.html


For a new CLI process, stage completion state is in memory only. Renewables-only
recovery therefore requires `--reuse-existing-basemaps` together with
`--stages renewables_atlite`. This validates the existing region layer's study
area, province list, projected CRS, resolution, retention policy, geometry and
unique region identifiers before satisfying the basemap dependency. It does not
rebuild the basemaps. Add `--validate-only` to check this resume path without
starting the renewable calculations.


### Production polygon preparation

Renewables reuses the sibling `<study_area>_boundary_epsg3347.gpkg` dissolved
boundary when its study-area label and province set match the requested build.
The reused layer must contain one valid nonempty EPSG:3347 geometry. Subsets or
missing/mismatched products fall back to selected Census boundaries and a union.
Centroid selection and full-cell coverage checks use vectorized prepared geometry
predicates. Only coastal/border cells are intersected, in batches of 64, with
progress and elapsed times. Loading, boundary preparation, selection, clipping,
area validation, WGS84 bounds conversion and source hashing each report progress.
These changes apply on the next process launch; a running process retains its
loaded implementation.


### Shared wind/solar eligibility pass

Land eligibility now processes both technologies together. For each region,
Atlite evaluates the common protected-area and existing-facility exclusions once;
its `projected_mask` reads/reprojects categorical land cover once onto that same
native raster. Separate whitelist filters then apply each technology's configured
excluded codes, including solar-only cropland in the baseline. Unknown codes and
NoData remain excluded. Native pixel geometry, production-boundary intersections,
and weather-cell assignments are calculated once for the union of eligible
pixels, then reused for independent wind and solar area weights. This supports
non-nested configured code sets as well as the baseline solar subset.

Shared checkpoints store two NPZ files per batch and commit a checksum manifest
only after both are complete. A batch without the pair manifest is recomputed;
missing/corrupted committed files fail validation. Batch messages report elapsed
time and progress for both technologies. Output filenames, per-technology maps,
capacity assumptions, and the separate low-wind resource restriction are retained.
The implementation fingerprint changes the renewable run identity: old runs and
checkpoints remain available, but are not automatically imported into the new
shared-pass run. A currently running process retains its loaded implementation.

## Post-solve deployment-weighted profiles

Successful renewable solves automatically create `renewable_profiles/` in the
run directory. `grid_monthly.csv` retains each deployed cell and technology;
`province_monthly.csv` aggregates wind and solar separately using solved net
installed MW, summed across vintages, for each scenario and model period.
Both tables include capacity, monthly capacity factor, potential mean MW and
potential MWh. Zero-capacity cells do not contribute to provincial averages.
Province assignment reuses the demand workflow's centroid-based mapping.
Provincial monthly curves and a grid-cell capacity-factor plot accompany the
CSV tables. These report availability before dispatch and curtailment.
The annual model still solves with annual capacity factors; these monthly
profiles are diagnostics from the exact checksum-verified Silver bundle
recorded in the Gold manifest, not additional intra-annual model constraints.
Export errors are recorded in the run manifest without discarding a solve.
