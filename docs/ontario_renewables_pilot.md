# Ontario renewable-resource pilot

Notebook: `notebooks/notebook22_ontario_renewables_pilot.ipynb`.
Configuration: `config/renewables_pilots/ontario_25km.toml`.
Implementation: `notebooks/support/renewables_pilot.py`.

ERA5 acquisition is registered as the `era5_renewables` Bronze stage in
`src/geocanoe/acquisition/era5_renewables.py`; Notebook 22 delegates to the same
request planner and downloader. Atlite Silver conversion and the schema adapter
remain pilot code. Existing Gold databases and `ELC_GEN` are unchanged.

## Spatial and technology contract

Read the existing `prov_quant_basemap_25km_centroid.gpkg`, layer `regions`. Select
Ontario using Statistics Canada's `PRUID=35` boundary, not the basemap's shared
`province_codes` study-area metadata. Retain centroid-selected parent region IDs
and clip production footprints to Ontario. These are resource-assessment polygons,
not an eligible-land mask or a new definition of model region IDs.

Snapshot CANOE electricity's generator registry and select codes `solar` and
`wind_onshore` (currently CSV lines 23 and 25). Preserve the original registry,
params, currency tables and checksums, without importing sibling execution modules.
The initial physical wind configuration is the official class-tagged T1 reference
curve, 6 MW, 170 m rotor, 115 m hub, matching the onshore row. T2/T3 sensitivity
cases remain in Notebook 21; they are not independent sources of land potential.

The solar configuration is a CSi single-axis tracking proxy with explicit ILR and
inverter efficiency. Bifacial response, backtracking, losses and ageing remain
validation questions. The configured wind plant loss is a stated 2030 Moderate
sensitivity, not Canadian validation. The reference curve adapter assumes a
shutdown point at 25 m/s. No ATB capacity-factor improvement multiplier is applied
on top of the physical profiles.

## Bronze contracts

### `era5_renewables`

The standardized weather year is **2018**, matching CANOE's explicit
`params.toml: weather_year`. Per the model integration requirement, CANOE's
ResStock residential and ComStock commercial hourly demand profiles use 2018.
Renewable supply and weather-dependent demand must therefore share this weather
year to preserve their hourly relationship during iterative solving. A weather-year
change requires coordinated replacement of the affected demand and supply
profiles; it is not an independent ERA5 scenario switch. The current Bronze check
enforces alignment with CANOE's declared weather year. Validation of the actual
ResStock/ComStock profile timestamps belongs in downstream integration and has
not yet been implemented. Matching weather years also does not replace validation
of UTC/local-time alignment, daylight saving conventions, and complete hourly
coverage.
 The selected `solar`/`wind_onshore` rows use
**ATB 2024** and `atb_min_year=2022`; these are edition and minimum economic
projection years, not meteorological years. Future model/cost years do not
require future weather. Bronze validates the weather year and pinned ATB edition
against the sibling files before acquisition, snapshots both original input
files by content hash, and records selected rows plus all year meanings in the
request plan/reference manifest. A mismatch stops before any CDS call. Configs
without a `[canoe]` section remain available for explicitly independent weather
experiments. No weather-year replacement or existing cache deletion is needed.


The default acquisition config is now `config/era5/canada.toml`. It reads all
provinces and territories from the raw Census boundary
`data_files/raw/basemaps/lpr_000b21a_e.shp`, reprojects to EPSG:4326, and derives
an outward-snapped bounding box plus one 0.25-degree cell of buffer. It records
boundary file/component hashes, CRS, feature count, and resulting request area.
The raw Census boundary must be acquired first; no processed basemap is required.
CDS supplies a rectangle covering the complete boundary, including surrounding
water and areas outside Canada. Province/region selection and polygon clipping
belong in Silver. National raw data are stored under
`data_files/raw/era5/canada_2018`; existing Ontario pilot caches remain intact.
Changing the boundary extent changes request identities and requires new payloads.

All acquisition choices are stored in `config/era5/canada.toml`, referenced by
`registry/geospatial_sources.yaml: acquisition.config`. The generic
`registry/bronze_registry.yaml` currently describes tabular model inputs and is
not the acquisition-settings file.

Configurable fields include `weather_year`, `months`, `grid_degrees` (currently
validated as 0.25), `buffer_cells`, `raw_dir`, `request_interval_seconds`, and
optional `max_new_requests_per_run`. In `[boundary]`, select `kind = "digital"`
or `kind = "cartographic"`, with explicit `digital_file` / `cartographic_file`
paths and `census_year`. The default remains the existing 2021 cartographic file.
Both paths describe local acquired sources; choosing digital fails clearly if
that file is not present. Changing census edition requires updating the year and
corresponding paths together. The year is a declared source label, not inferred
or independently verified from file contents. No boundary replacement is hidden
inside ERA5 acquisition. The request plan records the complete effective config
and its SHA-256 as well as hashes of the selected boundary components.

Statistics Canada's filename code `a` denotes digital and `b` cartographic:
https://www150.statcan.gc.ca/n1/pub/92-160-g/92-160-g2021002-eng.htm

The Ontario pilot remains available with `--era5-config config/era5/ontario.toml`.
Notebook 22 remains the Ontario pilot; the national CLI default provides a reusable
Bronze source for subsequent Silver subsetting. The plan prints an approximate
uncompressed variable size, which is not a download-size or disk-space guarantee.

From the repository root, inspect the cache without contacting CDS:

```powershell
python scripts/build_bronze.py --stages era5_renewables
```

Download all missing requests in one sequential run, reusing verified payloads:

```powershell
python scripts/build_bronze.py --stages era5_renewables --era5-download
```

Use `--era5-config <path>` for another TOML config, and
`--era5-max-new-requests 1` to optionally limit a test run. The optional Python
extra is `pip install -e ".[era5]"`; the existing pilot environment already has
CDS API installed. Credentials and dataset licence acceptance remain with CDS.

The stage reports planned, cached, downloaded and remaining request counts.
An offline stage success means planning/cache verification succeeded, not that
the full year is acquired. `request_plan.json` records the full selection;
each completed payload has its own checksum manifest. `--overwrite` never
bypasses validation or replaces ERA5 caches. Corrupt/unmanifested files stop
execution for inspection. Avoid concurrent writers to the same raw directory.
No live CDS downloads are part of the regression tests.


- Original CDS single-level NetCDF/ZIP payloads under
  `data_files/raw/era5/ontario_pilot/<request-id>.payload`.
- Adjacent JSON records contain exact request, dataset, month, feature, bytes and
  SHA-256. Credentials are never included.
- The weather identity depends on bounds, grid, year/month, variables and format;
  it does not depend on turbine, cost scenario or model vintage.
- One boundary-derived national bounding box by default (explicit Ontario override),
  monthly requests, three feature groups.
  Default full year: 36 sequential requests. A single enabled run processes all
  missing requests; no per-run cap unless explicitly configured. Cache verification
  and acquisition have tqdm progress, with per-request status and file sizes.
  One active request, a one-second interval between completed downloads and new
  submissions, and an exclusive local acquisition lock prevent duplicate runs.
  Persistent failures stop the run; rerunning resumes from verified caches.
- Client HTTP operations are bounded to three attempts, 30-second retry delays
  and a 60-second HTTP timeout. There are no application retry loops or parallel
  submissions. Persistent HTTP/CDS errors stop acquisition; inspect and resume later.
  Normal server job queueing/polling is separate from an individual HTTP timeout.
- Source: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels

### `renewable_technology_references`

- Content-addressed snapshots under
  `data_files/raw/renewables/source_bundles/<identity>/`.
- Preserve full source CSVs and original specifications. Filtering by technology,
  cost year and scenario is downstream work.
- The pilot reuses the pinned ATB 2024 v3.0.0 and official curve evidence acquired
  in Notebook 21. It does not silently update to a new ATB edition.
- Source manifests distinguish source USD2022 from CANOE's configured USD2021
  label. This discrepancy must not enter currency conversion unnoticed.
- Sources: https://atb.nlr.gov/electricity/2024/data and
  https://github.com/NatLabRockies/turbine-models

## Proposed Silver contract: `renewables_atlite`

Location: `data_files/processed/renewables/ontario_pilot/<pilot-id>/`.

1. Verify all three Bronze feature groups for each month.
2. Prepare weather with the inspected Atlite 0.7.0 adapter. Radiation handling,
   Kelvin temperatures, component wind speeds and the preceding-hour solar
   convention follow Atlite. Store prepared weather separately from raw payloads.
3. Build the native Atlite fractional-overlap matrix in EPSG:3347. Use grid-cell
   physical areas as the capacity layout for a uniform-density resource baseline.
   Do not average latitude-longitude cells as though all have the same area.
4. Require complete weather-footprint coverage of every selected production region.
   Convert at weather-cell level; apply PV inverter clipping before spatial means.
5. Retain UTC regional hourly CFs, monthly manifests, coverage, raw/configuration/
   geometry/helper hashes. Cache identities include physical and geometry inputs.
6. Concatenate exactly one full calendar year, reject duplicate/missing hours and
   non-finite CFs, then average over time. Leap years have 8,784 observations; the
   current model's separate representative-year conversion remains 8,760 hours.
7. Map complete production polygons on common extents, report coverage separately,
   and never fill missing annual CFs with zeros.

The default notebook also exercises the same conversion against fully covered
polygons inside Notebook 20's cached one-day cutout. Those diagnostic products are
labelled daily and cannot pass the annual gate.

## Schema adapter contract

Generate `renewables.sql` only from complete annual Ontario profiles. Before then,
write a blocked placeholder which raises a rollback error if executed. Never
substitute a daily profile or imported CANOE resource CF into the annual candidate.

The two technology IDs are `E_WND_ON` and `E_SOL_PV`. Map CANOE's `elc_tx` to the
existing GeoCANOE `elc` commodity; retain `ethos` as the source input. The configured
capacity basis is MW and electricity activity is MWh/year. Set capacity-to-activity
to 8,760 MWh/(MW year). Annual technologies require
`limit_annual_capacity_factor(operator='le')`; hourly CF-process rows are not used
for this annual model representation.

The candidate also inserts technology, efficiency, lifetime, investment and fixed
O&M rows. It does not redefine target region/commodity/time dimensions, invent
capacity ceilings, replace incumbent generation, or claim hourly storage adequacy.

Read source costs in USD2022/kW and convert explicitly using snapshotted CANOE
exchange/deflator tables. By default, the output cost basis is million CAD2020/MW:
multiply by 1,000 kW/MW, the CAD conversion factor, and divide by 1,000,000. Fixed
O&M adds a per-year denominator. Weather year, model vintage, cost projection year
and currency year are independent configuration fields. OCC excludes grid
connection; later integration must decide how to account for the electrical network.

Validate the exact SQL on a disposable database instantiated from the selected
Temoa v4 schema. Check foreign keys, annual CF row counts, missing regions and
periods, duplicate renewable technology IDs and annual time representation. A
failed precondition rolls back. Database-contract validation is not a solver test.

## Promotion criteria

Before formal registry/orchestrator integration or a Gold merge:

- Complete the full Ontario weather-year run and review maps and seasonal profiles.
- Validate wind/PV against Canadian observations and accept explicit loss/ageing policy.
- Reconcile all target cost scales and the MWh electricity basis; legacy unit labels
  in GeoCANOE are not proof of global consistency.
- Decide whether incumbent `ELC_GEN` is retained, constrained or replaced.
- Decide whether eligible-land potential and competing land uses are needed before
  introducing renewable build-capacity limits.
- Test a copied model through the solver and inspect energy/cost results.
- Then implement production acquisition/Silver/schema modules, source registry entries,
  stage orchestration and integration checks using the reviewed pilot contracts.

The acquisition layer can then expand geographically with an explicit request-size
review, stable caches, bounded execution and no repeated scraping.
