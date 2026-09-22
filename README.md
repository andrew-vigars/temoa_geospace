# Geospatial-CANOE

Geospatial-CANOE extends the CANOE/TEMOA energy-system modelling framework with a modular geospatial data, schema, execution, and analysis pipeline. It converts Canadian boundary, road, emissions, demand, technology, and cost data into a spatial graph, encodes that graph as a CANOE/TEMOA-compatible SQLite database, executes model scenarios, and provides post-solve exports and interactive maps.

The repository is an active research codebase. The current implementation uses the TEMOA v3-compatible CANOE backend and introduces mixed-integer formulations where transport infrastructure requires economies of scale, particularly for pipelines. Migration to TEMOA v4 is planned after the geospatial workflow is stable.

## Release and handoff history

The `v0.7.0` tag marks the completed orchestration and scenario-management
release. The current `geo-dev` branch builds on that release with national NHN
Bronze and Silver workflows, Aboriginal Lands acquisition, and the generalized
geospatial source registry. Package metadata remains at `0.7.0` until the next
release is prepared.

| Version | Milestone |
| --- | --- |
| `v0.2.0` | Initial GIS-driven framework, regional graphs, schema encoding, diagnostics, and resolution-aware mapping |
| `v0.3.0` | Automated raw-data acquisition |
| `v0.3.1` | Reproducible model execution and improved command-line selection |
| `v0.3.2` | Environment and installation reproducibility |
| `v0.4.0` | Profile-driven study areas, dual coordinate systems, H2 pipeline costs, and solved-output mapping |
| `v0.4.1` | Reproducible batch model execution |
| `v0.5.0` | Migration of the active implementation into the `src/geocanoe` package |
| `v0.6.0` | Geological CO2 storage workflow, registry migration, diagnostics refactor, typing, and contract tests |
| `v0.7.0` | Bronze orchestration, scenario-aware Gold identities and manifests, scenario-selected resolutions, sample configurations, legacy gasoline opt-in, and multi-resolution batch scenarios |

This history describes research-code milestones rather than a stable public API.
The Git tags preserve the detailed commit boundaries, while this summary gives
new maintainers the architectural progression needed to interpret the current
workflow.

### Post-`v0.7.0` development

Current development adds national hydrography evidence, Aboriginal Lands
acquisition, and schema-v2 geospatial source metadata without changing the
published package version. Release preparation should continue to verify the
locked environment, package metadata, lint checks, and the complete GeoCANOE
test suite before creating the next tag.

## Current capabilities

The workflow currently supports:

- configurable Canadian study areas defined by province and territory;
- geographic grids in EPSG:4326 and projected grids in EPSG:3347;
- centroid-based cell retention and rook adjacency;
- orchestrated Bronze acquisition for boundaries, Aboriginal Lands, roads,
  hydrography, emissions, and geological-storage evidence;
- filtered National Road Network backbone, primary-freight, and freight-access layers;
- registered NHN waterbody and watercourse filtering, clipping, provenance,
  summaries, and previews;
- Silver impedance evidence derived from Aboriginal Lands, population density,
  and NHN hydrography, with resolution-matched edge cost multipliers;
- weak and strong road-connectivity mapping;
- province assignment and graph-node snapping for legacy point inputs;
- preprocessing of 2024 large-facility greenhouse-gas emissions;
- acquisition of a selected CanCO₂ unified-storage release from a local sibling repository;
- mapping of unified geological-storage evidence onto each configured onshore basemap;
- topology-independent hydrogen-pipeline cost preprocessing and curve fitting;
- graph-based pipeline, truck, and electricity-transmission links;
- schema encoding into a CANOE/TEMOA SQLite database;
- timestamped single-scenario execution with archived inputs, solved databases, provenance records, and Excel exports;
- ordered batch execution of multiple CANOE/TEMOA configurations;
- interactive Folium mapping of solved process, demand, and transport flows;
- standalone Excel export of solved `Output*` tables.

## Design philosophy

The pipeline is intentionally staged. Expensive spatial and tabular transformations write durable intermediate products under `data_files/processed/`. These products act as checkpoints so downstream stages can be rerun without repeating raw-data acquisition or earlier GIS operations.

The architecture separates four concerns:

1. **Configuration and metadata** — TOML profiles and package configuration utilities define study areas and preprocessing choices.
2. **Model data construction** — acquisition, preprocessing, geospatial, emissions, cost, and schema modules construct the spatial model database.
3. **Model execution** — single-run and batch execution modules manage CANOE/TEMOA solves and run provenance.
4. **Analysis** — output exports and interactive maps decode solved scenarios without modifying upstream model products.

The importable implementation lives under `src/geocanoe/`. Files under `scripts/` are thin command-line entry points kept for convenient execution and compatibility.

The geospatial build profile is separate from the TEMOA solver configuration used during model execution.

## Repository structure

The tree below is intentionally curated. It shows the active project architecture and omits virtual environments, package metadata, caches, generated solver logs, and most individual data products.

```text
Geospatial-CANOE/
├── README.md
├── pyproject.toml                 Package and dynamic dependency mapping
├── requirements.txt               Geospatial runtime requirements
├── requirements-dev.txt           Geospatial development requirements
├── requirements-lock.txt          Optional reproducibility constraints
│
├── config/
│   ├── build_profiles/
│   │   └── *.toml                 Geospatial preprocessing profiles
│   └── batch_profiles/
│       └── *.toml                 Ordered model-run batches
│
├── data_files/
│   ├── raw/                       External and downloaded source datasets
│   │   ├── aboriginal_lands/        National legislative boundaries and metadata
│   │   ├── basemaps/
│   │   ├── canco2_storage/          Acquired external CanCO₂ Silver release
│   │   ├── emissions/
│   │   ├── nhn/                      National NHN hydrographic features
│   │   ├── nrn/
│   │   └── residential/              Census DA boundaries and population table
│   ├── models/                    Controlled engineering and cost workbooks
│   │   └── cost_models/
│   ├── processed/                 Durable workflow checkpoints
│   │   ├── basemaps/
│   │   ├── co2_storage/              GeoCANOE storage crosswalk and regional evidence
│   │   ├── nhn/                      Filtered hydrography, manifests, summaries, and previews
│   │   ├── pipeline_impedance/       Polygon evidence and edge cost multipliers
│   │   ├── costs/
│   │   ├── emissions/
│   │   ├── graph/
│   │   ├── legacy_inputs/
│   │   ├── nrn/
│   │   ├── road_connectivity/
│   │   └── schema/
│   ├── CANOE_geospatial.sqlite    Baseline CANOE/TEMOA database
│   ├── canoe_dataset_schema.sql   Base relational schema
│   └── *.csv                      Other model input tables
│
├── registry/                      User-managed registered model inputs
│   ├── bronze_registry.yaml       Dataset IDs, schemas, and source paths
│   ├── geospatial_sources.yaml    Bronze roots, named artifacts, layers, and domains
│   ├── model.toml                 Global Gold-model defaults
│   ├── scenarios/
│   │   └── baseline.toml          Named scenario overlays
│   ├── commodities.csv
│   ├── generation_efficiency.csv
│   ├── techs.csv
│   └── transport_techs.csv
│
├── src/
│   └── geocanoe/
│       ├── acquisition/           Eight Bronze source-acquisition modules
│       │   └── co2_storage.py     Local CanCO₂ release acquisition
│       ├── analysis/              Folium maps and output-table exports
│       ├── config/                Build-profile parsing and validation
│       ├── costs/
│       │   └── pipelines/
│       │       └── h2/            H2 pipeline capacity and cost models
│       ├── emissions/             Facility-emissions preprocessing
│       ├── execution/             Silver orchestration, single runs, and batches
│       ├── geospatial/            Basemaps, impedance, adjacency, roads, connectivity
│       │   └── co2_storage.py     Storage-to-basemap integration and previews
│       ├── preprocessing/         Legacy input harmonization
│       ├── registry/              Dataset/stage metadata
│       ├── schema/                CANOE/TEMOA encoding and database utilities
│       └── paths.py               Shared repository-root discovery
│
├── scripts/                       Thin CLI entry points
│   ├── batch_run.py
│   ├── build_bronze.py
│   ├── build_schema.py
│   ├── build_silver.py
│   ├── create_map_folium.py
│   ├── export_output_tables.py
│   └── main_run.py
│
├── diagnostics/                  Input, balance, and run-audit utilities
├── notebooks/                    Exploratory and development notebooks
├── output_files/                 Timestamped optimization runs
├── legacy_workflow/              Superseded workflow implementations
└── temoa/                        CANOE/TEMOA optimization backend
```

Active reusable code belongs under `src/geocanoe/`. Generated intermediate products belong under `data_files/processed/`, while solved scenarios and run provenance belong under `output_files/`.

## Installation

The root `pyproject.toml` is the canonical package and installation interface
for Geospatial-CANOE. Setuptools dynamically combines the root requirement
fragments with the corresponding files from the bundled TEMOA backend, so no
separate requirements-file installation is needed.

Runtime installation reads `requirements.txt` and `temoa/requirements.txt`.
The `dev` extra additionally reads `requirements-dev.txt` and
`temoa/requirements-dev.txt`. Updating the bundled TEMOA checkout therefore
updates the backend dependency set used by the next installation.

Dependencies imported directly by `geocanoe` are declared in the root runtime
fragment even when TEMOA currently requires the same package. This keeps the
GeoCANOE dependency boundary explicit and prevents a future TEMOA dependency
change from silently removing a package that GeoCANOE still uses. Direct and
transitive versions for the reproducible development environment are captured
in `requirements-lock.txt`.

### Standard runtime installation

Use this option if you only need to run the Geospatial-CANOE workflow and CANOE/TEMOA model.

From the repository root, install Geospatial-CANOE and its runtime dependencies
in editable mode:

```bash
python -m pip install -e .
```

A successful installation should make the package importable without modifying `PYTHONPATH`:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

At the current development head, the command prints `0.7.0`.

Installation also provides the canonical workflow commands:

| Command | Purpose |
| --- | --- |
| `geocanoe-build-bronze` | Acquire Bronze source data |
| `geocanoe-build-silver` | Validate and build Silver products |
| `geocanoe-build-schema` | Encode a Gold CANOE/TEMOA database |
| `geocanoe-run` | Execute one encoded model scenario |
| `geocanoe-batch` | Execute an ordered scenario batch |
| `geocanoe-diagnostics` | Audit model inputs or solved outputs |
| `geocanoe-export` | Export solved output tables |
| `geocanoe-map` | Generate an interactive solved-output map |

The files under `scripts/` and `diagnostics/check.py` remain compatibility
wrappers for existing notebooks and automation. Package-module invocation with
`python -m` is also supported by the corresponding implementation modules.

### Development installation

Use this option if you plan to modify the codebase, run tests, use linting or type checking, or work interactively with Jupyter.

Install Geospatial-CANOE with its development extra:

```bash
python -m pip install -e ".[dev]"
```

The `dev` extra adds testing, coverage, linting, type-checking, pre-commit, and
interactive Jupyter tooling to the complete runtime environment.

Mypy is the canonical static type checker. Its project policy is stored in
`pyproject.toml` and covers the importable package plus the compatibility CLI
and diagnostics adapters:

```bash
python -m mypy
```

Third-party exceptions are scoped to individual scientific and geospatial
libraries that do not publish complete PEP 561 typing information; first-party
GeoCANOE modules remain fully checked by the configured policy.

Verify the installation with:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

### Reproducing the pinned environment

Use this option if you need to reproduce the exact package versions captured in the current project environment as closely as possible.

Use the lock file as a constraints file while installing from `pyproject.toml`:

```bash
python -m pip install -c requirements-lock.txt -e ".[dev]"
```

Verify the installation with:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

In general, use `-e .` for runtime work, `-e ".[dev]"` for development, and
add `-c requirements-lock.txt` when reproducing the currently pinned environment.

When a direct dependency changes, update the appropriate root or TEMOA
requirement fragment, regenerate `requirements-lock.txt`, install with the lock
file as a constraint, and run `python -m pip check`. The release tag should be
created only after the package version, importable `geocanoe.__version__`, lock
file, lint checks, and agreed test suite are consistent.

## Configuration

Geospatial preprocessing stages use a shared TOML build profile loaded through `geocanoe.config`. A typical profile defines:

- study-area label and included provinces or territories;
- geographic and projected grid resolutions;
- centroid retention and coordinate precision;
- rook-adjacency settings;
- road classes, processed road networks, and output CRS;
- weak and/or strong road-connectivity methods;
- the road layer, road-connectivity method, and basemap used during schema construction;
- the Silver evidence rule used to enable geological CO2 injection and whether
  a numerical storage-capacity bound is requested;
- point-assignment boundary and snapping tolerances.

Example profile path:

```text
config/build_profiles/sample_build_profile.toml
```

The same profile should be used consistently across the basemap, hydrography,
CO₂-storage, adjacency, road, road-connectivity, legacy-input, emissions, cost,
and schema stages. Each profile also declares a short artifact identity:

```toml
[profile]
id = "onqc"  # 1-16 lowercase letters, numbers, or hyphens
```

Basemap grid families are enabled directly by their resolution lists; there is
no separate `grid_types` setting. A non-empty geographic list generates
EPSG:4326 grids, and a non-empty projected list generates EPSG:3347 grids. Leave
either list empty when that family is not required, but configure at least one:

```toml
[basemaps.geographic]
resolutions = []  # skip latitude/longitude grids

[basemaps.projected]
resolutions_km = [10, 25, 50]
```

Geological storage is configured in each build profile with:

```toml
[storage]
eligibility = "all_mapped"  # all_mapped, quantitative, or qualitative
use_capacity_bound = false
```

The eligibility setting controls which Silver `regional_storage_evidence`
regions receive `CO2_INJECT`. Numerical capacity bounds remain disabled because
the current Silver product does not contain defensibly allocated regional
storage quantities; setting `use_capacity_bound = true` fails validation rather
than treating evidence coverage as physical capacity.

Global non-spatial model defaults are configured once in
`registry/model.toml`, rather than repeated across geospatial build profiles:

```toml
[time]
start_year = 2025
end_year = 2050

[finance]
global_discount_rate = 0.03
default_loan_rate = 0.03

[emissions]
projection_method = "constant"

[storage]
requirement = "minimum_cumulative_activity"
minimum_cumulative_activity = 7_500_000_000

[pipeline_costs]
impedance_scope = "none"
```

Every Gold build also selects one file from `registry/scenarios/`. A scenario
has a short identity and may override only recognized fields from `model.toml`:

```toml
[scenario]
id = "baseline"  # 1-20 lowercase letters, numbers, or hyphens
description = "Canonical baseline using all global model defaults"
```

The committed `baseline.toml` has no overrides. A sensitivity case can contain,
for example:

```toml
[scenario]
id = "optional-storage"
description = "No minimum geological-storage target"

[storage]
requirement = "none"
minimum_cumulative_activity = 0
```

`model.toml` is loaded first and the selected scenario is applied second.
Unknown scenario sections or settings fail validation, and the complete merged
configuration—not only the override—is recorded with the Gold artifact.

Pipeline scenarios can apply the Silver ``cost_distance_multiplier`` associated
with every directed ``Ri-Rj`` edge without changing the graph's physical
``distance_km``. The supported ``[pipeline_costs].impedance_scope`` values are:

- ``none``: use physical distance for all pipeline costs;
- ``etl_capex_only``: use weighted distance only for pipeline CAPEX tables
  (``CostInvest`` and/or ``ETLSegment``); and
- ``all_km_dependent``: use weighted distance for pipeline CAPEX tables, fixed
  OPEX, and variable OPEX.

For example, a scenario that applies the geographic bias only to pipeline CAPEX
contains:

```toml
[pipeline_costs]
impedance_scope = "etl_capex_only"
```

When weighting is enabled, schema construction requires exact one-to-one edge
coverage from the matching Silver artifact under
``data_files/processed/pipeline_impedance/<build-id>/edges/``. Truck and
electricity-transmission costs always retain physical distance.

The two time boundaries define exactly one optimization period, `[2025, 2050)`,
with a 25-year duration. Temoa optimizes one representative year and assumes its
capacity and activity repeat in every year of that period. Accordingly, the
`constant` emissions projection leaves the Silver facility layer as an observed
annual baseline and encodes it unchanged, apart from converting kilotonnes to
tonnes. Thus `1 kt CO2e/year` in Silver becomes `1,000 t CO2e/year` of Gold
`LimitCapacity`. A conceptual 25-year total may be calculated as `1,000 x 25 =
25,000 t CO2e` for reporting, but that total must not be used as the annual
capacity limit. Physical CO2 quantities are not discounted. Temoa applies the
3% global rate only when converting annual costs to present value.

The only currently supported emissions projection is `constant`; any other
value fails configuration validation. It encodes the observed annual baseline
as representative-year availability without multiplying it by period length.

`requirement = "minimum_cumulative_activity"` treats
`minimum_cumulative_activity` as a system-wide storage target over the complete
model period. Because Temoa's `LimitActivity` is annual, Gold divides that target
by the period length before encoding the `CO2_INJECT` lower bound. For example,
7.5 billion tonnes over 25 years is encoded as 300 million tonnes/year. The
configured target must be positive. With `requirement = "none"`, it must remain
zero and geological storage is available but optional.

Gasoline demand follows the same representative-year convention. Silver maps
population-centre proxy coordinates onto every configured basemap, aggregates
annual tonnes by region, and preserves zero-demand regions. Gold selects the
artifact matching its build ID and basemap, validates exact graph-region
coverage, and writes positive regional values unchanged to `Demand` in
`t/year`; it does not snap gasoline points again or multiply demand by 25.
Solved annual gasoline flow can be multiplied by 25 for a cumulative
single-period report, but the cumulative value must not be supplied as
`Demand`.

### CanCO₂ storage repository layout

The geological-storage workflow can read the unified CanCO₂
Silver GeoPackage directly from a local `canco2-storage` checkout. With no
environment-variable overrides, the repositories must use this layout:

```text
repos/
├── canco2-storage/
│   └── data/
│       └── processed/
│           └── unified_storage/
│               └── <timestamp>_CanadaGeologicalStorageUnified_<version>.gpkg
└── temoa-upstream/
    └── temoa_geospace/
```

For example, the current Windows development layout is:

```text
C:\Users\aviga\Research\repos\canco2-storage
C:\Users\aviga\Research\repos\temoa-upstream\temoa_geospace
```

The acquisition module and exploratory notebook derive the shared `repos/`
directory from the GeoCANOE project root and search:

```text
<canco2-storage>/data/processed/unified_storage/
```

CanCO₂ output filenames begin with a sortable `YYYYMMDD_HH` timestamp. When
the directory contains multiple matching GeoPackages, acquisition selects the
lexicographically newest filename and prints the selected path. This is
convenient for exploration but permits the input to change when CanCO₂ is
rebuilt.

For a reproducible model run, pin the exact GeoPackage for the current shell:

```powershell
$env:CANCO2_STORAGE_GPKG = "C:\Users\aviga\Research\repos\canco2-storage\data\processed\unified_storage\20260918_13_CanadaGeologicalStorageUnified_AV_v2.gpkg"
```

If the repositories do not use the default relative layout, configure the
CanCO₂ repository root instead:

```powershell
$env:CANCO2_STORAGE_REPO = "D:\Research\repos\canco2-storage"
```

`CANCO2_STORAGE_GPKG` takes precedence over `CANCO2_STORAGE_REPO`. The former
pins one immutable input; the latter retains automatic newest-file discovery.
These variables are consumed by the acquisition module and exploratory storage
notebook; they do not yet constitute a packaged inter-project API.

Acquire the selected release and its companion metadata files into the
GeoCANOE raw layer with:

```bash
python -m geocanoe.acquisition.co2_storage
```

The acquisition writes
`data_files/raw/canco2_storage/selected_release.txt`. The Silver transformation
uses that selection instead of re-evaluating timestamps, so an exact
`CANCO2_STORAGE_GPKG` pin remains reproducible even when the raw directory
contains several releases. Running acquisition again updates the selection.

## Workflow

The preferred preprocessing entry point is the silver-layer orchestrator. The
committed sample profile makes this command resolvable in a clean checkout:

```bash
geocanoe-build-silver \
    --config config/build_profiles/sample_build_profile.toml
```

The silver workflow validates dependencies and executes the configured preprocessing stages in dependency order.

NHN is transformed after basemaps because the configured study-area boundary
defines the spatial subset. `registry/geospatial_sources.yaml` inventories all
external geospatial Bronze sources using named fixed or globbed artifacts.
Optional source-layer mappings and coded domains support specialized Silver
stages; the build profile's `[hydrography]` tables select readable NHN classes
and minimum source area/length thresholds. Outputs are written to:

```text
data_files/processed/nhn/{study_area}_filtered_hydrography.gpkg
data_files/processed/nhn/{study_area}_hydrography_summary.csv
data_files/processed/nhn/{study_area}_hydrography_manifest.json
data_files/processed/nhn/preview/*.png
```

### Raw acquisition

The preferred entry point coordinates all eight registered Bronze stages:

```bash
geocanoe-build-bronze
```

Use `--stages` to run a subset, such as
`geocanoe-build-bronze --stages aboriginal_lands nhn`. Acquisition
modules may also be run independently when one source needs to be refreshed:

```bash
python -m geocanoe.acquisition.basemaps
python -m geocanoe.acquisition.residential
python -m geocanoe.acquisition.gasoline_demand
python -m geocanoe.acquisition.aboriginal_lands
python -m geocanoe.acquisition.nrn
python -m geocanoe.acquisition.nhn
python -m geocanoe.acquisition.emissions
python -m geocanoe.acquisition.co2_storage
```

Primary outputs:

```text
data_files/raw/basemaps/
data_files/raw/residential/lda_000b21a_e.shp
data_files/raw/residential/98100015.csv
data_files/raw/gasoline_demand/fuel_sales/23100066.csv
data_files/raw/gasoline_demand/fuel_sales/23100066_MetaData.csv
data_files/raw/gasoline_demand/population_centres/lpc_000b21a_e.shp
data_files/raw/gasoline_demand/population_centres/lpc_000b21a_e.xml
data_files/raw/gasoline_demand/gasoline_demand_acquisition_manifest.json
data_files/raw/aboriginal_lands/AL_TA_CA_*_eng.shp
data_files/raw/aboriginal_lands/aboriginal_lands_wms_capabilities.xml
data_files/raw/aboriginal_lands/aboriginal_lands_acquisition_manifest.json
data_files/raw/nrn/{PROVINCE}/
data_files/raw/nhn/rhn_nhn_hhyd.gpkg
data_files/raw/nhn/nhn_wms_capabilities.xml
data_files/raw/nhn/nhn_acquisition_manifest.json
data_files/raw/emissions/co2_large_facilities_2024/
data_files/raw/canco2_storage/
```

`registry/geospatial_sources.yaml` schema v2 inventories these sources by
stable ID. Each source declares a Bronze root and named artifacts using fixed
paths or globs; source-layer mappings and coded domains are optional extensions
used where downstream transformations require them.

#### Residential Census acquisition

The `residential` Bronze stage acquires the 2021 dissemination-area
cartographic boundary archive and Statistics Canada table 98-10-0015-01. It
validates the national boundary as the 57,932-feature EPSG:3347 cartographic
release and checks the table for the fields required by a later DGUID join.
Bronze preserves the national source records; Ontario filtering, population-
density selection, and impedance-factor derivation belong in Silver.

Run only this Bronze stage with:

```bash
geocanoe-build-bronze --stages residential
```

#### Gasoline-demand acquisition

The `gasoline_demand` Bronze stage groups its gasoline-specific inputs under
one domain root with separate source folders. It acquires Statistics Canada
table 23-10-0066-01, including its distributed metadata CSV, and the 2021
national population-centre cartographic boundary file, including its XML
metadata. The stage validates the presence of net-gasoline observations and
the EPSG:3347 population-centre release: 1,030 provincial-part records
representing 1,026 unique centres (four centres cross provincial boundaries).

The acquisition manifest records the Natural Resources Canada petroleum-
distribution page as methodological support for representing selected
population centres as regional bulk-terminal proxies. That page is not treated
as a geocoded terminal inventory. Dissemination-area population weights are
reused from the `residential` Bronze stage rather than copied into the gasoline
folder.

Run only this Bronze stage with:

```bash
geocanoe-build-bronze --stages gasoline_demand
```

#### National Hydro Network acquisition

The `nhn` Bronze stage acquires the Canada-wide English hydrographic-feature
archive, `rhn_nhn_hhyd.gpkg.zip`, from the official [NRCan GeoPackage download
directory](https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/gpkg_en/CA/).
It also snapshots the official [NHN WMS capabilities
XML](https://open.canada.ca/data/en/dataset/a4b190fe-e090-4e6d-881e-b87956c07977/resource/f6ca81dd-a1ee-4002-ac94-bfaa1118e8ea)
as source metadata.

Because the archive is approximately 15 GB, the stage validates and reuses an
existing `rhn_nhn_hhyd.gpkg` before attempting any archive download. New
downloads use resumable `.part` files and show byte progress, transfer rate,
and ETA. Extraction is atomic, and validation checks the expected NHN layer
family, EPSG:4617 source CRS, and required waterbody classification fields.
After successful GeoPackage and metadata validation, the approximately 15 GB
ZIP is deleted by default; pass `--keep-nhn-archive` to retain it.

Run only this Bronze stage with:

```bash
geocanoe-build-bronze --stages nhn
```

Use `--overwrite` only when the national archive should be downloaded and
extracted again.

#### Aboriginal Lands acquisition

The `aboriginal_lands` Bronze stage acquires the Canada-wide English
`AL_TA_CA_SHP_eng.zip` distribution from the official [NRCan Aboriginal Lands
download directory](https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_al_ta/shp_eng/).
It preserves the versioned source shapefile name, snapshots the official WMS
capabilities XML, validates polygon geometry, EPSG:4617, and identifying source
fields, and writes an acquisition manifest. The ZIP is deleted after successful
validation unless `--keep-aboriginal-lands-archive` is supplied.

Run only this Bronze stage with:

```bash
geocanoe-build-bronze --stages aboriginal_lands
```

The Silver `aboriginal_lands` stage normalizes the registered source polygons,
clips them to the study area, and converts the configured classes into pipeline-
impedance evidence. That evidence is combined with population and NHN evidence
by the later `pipeline_edge_impedance` stage; it is not currently a general
technology-siting exclusion layer.

### Silver preprocessing stages

The current Silver workflow includes seventeen stages in canonical dependency
order:

1. legacy site province mapping for the existing LCOE/electricity workflow;
2. gasoline-demand proxy selection and DA-to-proxy assignment;
3. provincial gasoline-sales allocation to the selected proxies;
4. emissions preprocessing;
5. hydrogen-pipeline capacity-cost preprocessing;
6. hydrogen-pipeline cost-model fitting;
7. study-area basemap construction;
8. proxy-demand mapping onto every configured basemap resolution;
9. Aboriginal Lands pipeline-impedance preprocessing;
10. population-density pipeline-impedance preprocessing;
11. registered NHN hydrography filtering and clipping;
12. NHN pipeline-impedance preprocessing;
13. onshore CO₂ storage-to-basemap integration;
14. regional adjacency construction;
15. aggregation of polygon evidence onto graph-edge cost multipliers;
16. processed road-network construction;
17. road-connectivity mapping.

These stages are implemented under `src/geocanoe/` and orchestrated by `geocanoe.execution.silver`.

Important silver outputs include:

```text
data_files/processed/legacy_inputs/
data_files/processed/emissions/
data_files/processed/costs/
data_files/processed/basemaps/
data_files/processed/nhn/
data_files/processed/pipeline_impedance/<build-id>/
data_files/processed/co2_storage/
data_files/processed/graph/
data_files/processed/nrn/
data_files/processed/road_connectivity/
data_files/processed/gasoline_demand/<build-id>/
```

The gasoline stages support all ten provinces and three territories. Configure
them in the build profile:

```toml
[gasoline_demand]
sales_year = 2024
gasoline_density_t_per_litre = 0.00074

[gasoline_demand.proxies]
strategy = "population_threshold"
minimum_population = 100000
candidate_minimum_population = 30000
empty_jurisdiction_policy = "largest_population_centre"
anchor_registry = "registry/gasoline_supply_anchors.csv"

[gasoline_demand.proxies.additional_hubs]
# Used by supply_anchors_plus_fill, for example:
# ON = 5
```

`population_threshold` selects the official 2021 population-centre class at or
above the configured threshold. `supply_anchors_plus_fill` begins with the
controlled anchor registry and greedily adds eligible centres that most reduce
population-weighted DA-to-proxy distance. A jurisdiction with no qualifying
centre or registered anchor receives its largest population centre, so a
national build always covers all 13 jurisdictions.

Every DA is assigned to the nearest selected proxy within its own jurisdiction.
Provincial net gasoline sales from Statistics Canada table 23-10-0066-01 are
then allocated by assigned 2021 DA population. The output audit reconciles both
allocation weights and litres to the source total. DAs whose published
population is unavailable remain in the crosswalk with zero allocation weight
and an explicit availability flag.

The NRCan distribution-network illustration is qualitative rather than a
terminal inventory. Accordingly, `registry/gasoline_supply_anchors.csv`
distinguishes centres explicitly named in NRCan text from centres inferred from
the map; these are modelling controls, not claims about physical terminal
locations. Silver outputs include selected proxy geometry, the DA crosswalk,
catchment diagnostics, demand by proxy, allocation audits, and a manifest. Gold
consumes the resolution-matched `regional_gasoline_demand` layer directly by
region. The legacy LCOE/electricity site workflow remains unchanged pending
separate research. The former `data_files/demand.csv` input is retained only as
`data_files/old_files/demand.csv` for provenance and is not consumed by Silver
or Gold.

The `gasoline_basemap` stage writes a source-point PNG before grid assignment,
then maps every proxy to a cell in each compatible basemap. Point-in-cell matches
are preferred; coastal or otherwise uncontained points fall back to the nearest
cell whose centroid belongs to the same jurisdiction. Multiple proxies in one
cell are aggregated. Each resolution receives a GeoPackage with
`regional_gasoline_demand` and `gasoline_region_crosswalk` layers, equivalent
CSVs, a conservation audit, and a mapping PNG under:

```text
data_files/processed/gasoline_demand/<build-id>/
    preview/gasoline_demand_proxies.png
    basemaps/<basemap-stem>_gasoline_demand.gpkg
    basemaps/<basemap-stem>_regional_gasoline_demand.csv
    basemaps/<basemap-stem>_gasoline_region_crosswalk.csv
    basemaps/<basemap-stem>_gasoline_mapping_audit.csv
    basemaps/preview/<basemap-stem>_gasoline_demand_mapping.png
```

The storage stage can also be run independently after basemap construction:

```bash
python -m geocanoe.geospatial.co2_storage \
    --config config/build_profiles/sample_build_profile.toml
```

For each compatible basemap, the stage writes one GeoPackage containing
`regional_storage_evidence` and `storage_region_crosswalk` layers, equivalent
inspection CSVs, and a mapping PNG. A separate PNG previews the normalized
source features. The crosswalk preserves feature and storage-unit lineage and
overlap metrics; it does not allocate or sum geological capacity by model
region.

The current basemaps represent the onshore model domain. Source features outside
that domain are retained in the source preview but do not create offshore model
regions. Offshore integration is deferred until an appropriate digital census
boundary and offshore regionalization are available.

### Schema encoding

Encode a selected geospatial configuration into a CANOE/TEMOA database:

```bash
geocanoe-build-schema \
    --config config/build_profiles/sample_build_profile.toml \
    --scenario registry/scenarios/baseline.toml
```

or:

```bash
python -m geocanoe.schema.build \
    --config config/build_profiles/sample_build_profile.toml \
    --scenario registry/scenarios/baseline.toml
```

If `--scenario` is omitted, the committed `baseline.toml` is used. The schema
workflow therefore combines three inputs:

```text
Silver build profile + registry/model.toml + selected scenario.toml
                                      ↓
                         validated effective configuration
                                      ↓
                              Gold SQLite + manifest
```

It resolves a compatible basemap, graph, road layer, and road-connectivity
product; snaps tabular and point inputs to graph nodes; rebuilds
topology-dependent CANOE/TEMOA tables; applies the effective model assumptions;
validates the encoded database; and writes:

```text
data_files/processed/schema/
    gold_<build-id>_<scenario-id>_<fingerprint>.sqlite
    gold_<build-id>_<scenario-id>_<fingerprint>.manifest.json
```

For example:

```text
gold_onqc_baseline_a1b2c3d4.sqlite
gold_onqc_baseline_a1b2c3d4.manifest.json
```

The eight-character fingerprint is deterministic and incorporates the complete
normalized build profile, the selected basemap, and the effective merged model
configuration. It prevents materially different builds from overwriting one
another without making filenames excessively long. The adjacent manifest stores
the full resolved paths and values needed to interpret or reproduce the artifact.

The current generalized pipeline assumption applies the processed
hydrogen-pipeline capacity and cost representation to all pipeline technologies
until commodity-specific pipeline cost layers are available. A scenario may
apply the matching Silver edge-impedance multiplier to CAPEX only or to every
kilometre-dependent pipeline cost table; physical graph distances remain
unchanged. ``CostInvest`` and ``ETLSegment`` are commonly used as alternative
CAPEX representations, but they may coexist when they encode different
investment components. The current generated pipeline layer uses
``ETLSegment`` for its piecewise economies-of-scale CAPEX curve.

### Single-scenario execution

Run CANOE/TEMOA from an encoded SQLite database:

```bash
geocanoe-run
```

or:

```bash
python -m geocanoe.execution.run
```

The execution workflow selects an encoded database and TEMOA configuration, creates a timestamped run directory, records provenance and file hashes, archives the input database, executes TEMOA, archives the solved database, and exports available `Output*` tables.

Typical outputs:

```text
output_files/<timestamped_run>/
    input_*.sqlite
    solved_*.sqlite
    manifest.json
    effective_*.toml
    logs and provenance records
    excel_outputs/
        output_tables.xlsx
```

### Batch execution

Run an ordered set of solver configurations:

```bash
geocanoe-batch --config config/batch_profiles/batch_run.toml
```

or:

```bash
python -m geocanoe.execution.batch     --config config/batch_profiles/batch_run.toml
```

Each model run is launched as an independent subprocess through `geocanoe.execution.run`. Batch state is written incrementally to:

```text
output_files/batches/<batch_name>_<timestamp>/
    batch_manifest.json
    batch_results.csv
    batch.log
```

### Diagnostics

Run the central diagnostics command and choose one of two workflows:

```powershell
geocanoe-diagnostics
```

Choose `inputs` to select a Silver build profile and its processed basemap. The
baseline model scenario is used by default; pass `--scenario` to audit another
Gold variant. The suite derives the same compact artifact identity and then runs
spatial, schema, technology-readiness, numeric, and unit checks.

Choose `outputs` to select a solved SQLite run and check commodity balance, edge
flow capacity, and objective-cost consistency. Both workflows write CSV evidence
by default.

For a scripted run, selections can be provided directly:

```powershell
geocanoe-diagnostics inputs --config config/build_profiles/sample_build_profile.toml --basemap provinces_only_basemap_25km_centroid --scenario registry/scenarios/baseline.toml
geocanoe-diagnostics outputs output_files/<run>
```

Unit findings remain warnings because legacy schemas contain incomplete unit
metadata.

Model execution runs pre- and post-solve diagnostics in non-blocking `report`
mode by default. Select the policy explicitly with
`scripts/main_run.py --diagnostics {off,report,strict}`. Strict mode stops before
the solve when input diagnostics fail and marks post-solve validation failures in
the run manifest. Failed solver runs retain their working database and record any
postmortem diagnostics that can still be evaluated.

### Export solved output tables

Export available `Output*` tables from a solved SQLite database:

```bash
geocanoe-export
```

or:

```bash
python -m geocanoe.analysis.exports
```

The exporter writes each selected table to a separate worksheet in a single
Excel workbook, adds a `CO2StorageSummary` worksheet when solved `CO2_INJECT`
flows are present, and validates worksheet dimensions against Excel limits.

### Interactive mapping

Generate an interactive Folium map from a solved scenario:

```bash
geocanoe-map
```

or:

```bash
python -m geocanoe.analysis.maps
```

The mapping workflow infers the associated geospatial products from the selected
solved database, decodes transport pseudo-regions back to graph edges, separates
parallel active transport corridors for visualization, and exports an interactive
Leaflet/Folium HTML map with layer controls, tooltips, and popups. Solved
`CO2_INJECT` output appears as a purple `CO2 storage` node layer.

## Canonical execution order

```text
raw acquisition
    ├── basemaps
    ├── residential and gasoline-demand sources
    ├── Aboriginal Lands
    ├── NRN
    ├── NHN
    ├── emissions
    └── CanCO₂ storage evidence
        │
        ▼
silver preprocessing
    ├── legacy inputs
    ├── emissions
    ├── H2 pipeline capacity-cost processing
    ├── H2 pipeline cost models
    ├── basemaps
    ├── gasoline proxy allocation and basemap mapping
    ├── Aboriginal Lands, population, and NHN impedance evidence
    ├── pipeline edge impedance
    ├── hydrography and CO₂ storage evidence
    ├── adjacency
    ├── roads
    └── road connectivity
        │
        ▼
schema encoding
        │
        ▼
single-run or batch CANOE/TEMOA execution
        │
        ├── Excel output export
        └── interactive Folium mapping
```

The silver preprocessing branches are dependency-aware rather than strictly linear. For example, emissions and pipeline-cost preprocessing can be rerun independently when their source data change.

## Package architecture

The package is organized by semantic responsibility rather than by the libraries used internally:

- `geocanoe.acquisition` — raw external data retrieval and extraction;
- `geocanoe.preprocessing` — generic or transitional preprocessing;
- `geocanoe.geospatial` — spatial graph, basemap, road, and connectivity construction;
- `geocanoe.emissions` — emissions-domain processing;
- `geocanoe.costs` — engineering and economic cost-model preparation;
- `geocanoe.schema` — CANOE/TEMOA schema encoding and database persistence;
- `geocanoe.execution` — workflow orchestration and model execution;
- `geocanoe.analysis` — solved-output exports and visualization;
- `geocanoe.config` — configuration parsing and validation;
- `geocanoe.registry` — canonical dataset and stage metadata.

Shared repository-root discovery is provided by `geocanoe.paths`; package modules should not depend on hardcoded `Path(__file__).parents[...]` assumptions.

## Intermediate products

Important processed directories include:

```text
data_files/processed/basemaps/
data_files/processed/co2_storage/
data_files/processed/graph/
data_files/processed/nhn/
data_files/processed/pipeline_impedance/
data_files/processed/nrn/
data_files/processed/road_connectivity/
data_files/processed/gasoline_demand/
data_files/processed/legacy_inputs/
data_files/processed/emissions/
data_files/processed/costs/
data_files/processed/schema/
```

These products are intended to be inspectable and reproducible. Rebuilding a stage may overwrite its own output files, but stages should not append duplicate records to existing GeoPackages or SQLite databases without first replacing or rebuilding the target product.

## Model formulation notes

Geospatial-CANOE represents model regions as graph nodes and candidate transport corridors as graph edges. Pipeline and electricity-transmission technologies may use the full candidate graph, while truck technologies are restricted to road-enabled edges under the selected connectivity method.

The current transport infrastructure formulation uses piecewise capacity-cost segments and therefore introduces integer or binary decisions. Development runs commonly use a non-zero MIP gap to reduce solve time. Scientific runs should document the selected solver, optimality gap, time limit, and termination status.

## Current limitations

- centroid retention and rook adjacency are the primary implemented basemap and neighbourhood methods;
- rail, marine, and existing natural-gas infrastructure are not yet encoded as dedicated transport layers;
- pipeline routing follows candidate graph corridors with configurable
  Aboriginal Lands, population-density, and hydrography cost impedance rather
  than least-cost pathfinding over a continuous routing surface;
- the current generalized pipeline cost layer is based on hydrogen-pipeline data;
- the schema workflow remains tied to the current CANOE/TEMOA database structure;
- multi-period scenario logic and uncertainty analysis are still under development;
- batch resume/retry settings are present in configuration but full persistent resume semantics are not yet implemented.

## Planned development

Priority extensions include:

- commodity-specific CO₂, hydrogen, fuel, and natural-gas pipeline cost models;
- geological CO₂ capacity and injectivity storage representation, DAC, BECCS, and additional carbon-management pathways;
- rail, marine, reused natural-gas corridors, and expanded electricity transmission;
- e-diesel, e-SAF, and e-methanol pathways;
- multi-period optimization and improved temporal representation;
- terrain- and infrastructure-aware routing;
- multi-objective and modelling-to-generate-alternatives analysis;
- network robustness, hub persistence, and near-optimal corridor analysis;
- stronger automated input, balance, and schema-integrity tests.

## Status

The preprocessing, schema-building, execution, export, diagnostics, and interactive mapping pipeline is partially operational for the current road, truck, pipeline, electricity-transmission, and geological-storage preprocessing representation. Truck cost representation is still required as well as CO2 and end-fuel pipeline representation. The major scripts-to-package refactor is complete, although the codebase remains active research software and should not yet be treated as a stable public API.

For the workflow overview, see [`scripts/model_workflow.md`](scripts/model_workflow.md).
