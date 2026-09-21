# Geospatial-CANOE

Geospatial-CANOE extends the CANOE/TEMOA energy-system modelling framework with a modular geospatial data, schema, execution, and analysis pipeline. It converts Canadian boundary, road, emissions, demand, technology, and cost data into a spatial graph, encodes that graph as a CANOE/TEMOA-compatible SQLite database, executes model scenarios, and provides post-solve exports and interactive maps.

The repository is an active research codebase. The current implementation uses the TEMOA v3-compatible CANOE backend and introduces mixed-integer formulations where transport infrastructure requires economies of scale, particularly for pipelines. Migration to TEMOA v4 is planned after the geospatial workflow is stable.

## Release and handoff history

The current branch is the `0.7.0` release candidate. It consolidates the work
completed since the original April 2026 handoff and is intended to be tagged
`v0.7.0` after the release checks pass and before the branch is proposed for
merge into `main`.

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

### `v0.7.0` release sequence

1. Install the locked development environment with
   `python -m pip install -c requirements-lock.txt -e ".[dev]"`.
2. Confirm both the package module and installed metadata report `0.7.0`.
3. Run `python -m pip check`, `python -m ruff check src tests scripts`, and
   `python -m pytest tests -q`.
4. Commit the reviewed release-preparation changes.
5. Create the annotated tag with
   `git tag -a v0.7.0 -m "GeoCANOE v0.7.0"` and push the branch and tag.
6. Open the pull request from the tagged `geo-dev` head to `main` and include
   the release history and validation results in the PR description.

## Current capabilities

The workflow currently supports:

- configurable Canadian study areas defined by province and territory;
- geographic grids in EPSG:4326 and projected grids in EPSG:3347;
- centroid-based cell retention and rook adjacency;
- filtered National Road Network backbone, primary-freight, and freight-access layers;
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
│   │   ├── basemaps/
│   │   ├── canco2_storage/          Acquired external CanCO₂ Silver release
│   │   ├── emissions/
│   │   ├── nhn/                      National NHN hydrographic features
│   │   └── nrn/
│   ├── models/                    Controlled engineering and cost workbooks
│   │   └── cost_models/
│   ├── processed/                 Durable workflow checkpoints
│   │   ├── basemaps/
│   │   ├── co2_storage/              GeoCANOE storage crosswalk and regional evidence
│   │   ├── nhn/                      Filtered hydrography, manifests, summaries, and previews
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
│   ├── geospatial_sources.yaml    External spatial sources, layers, and coded domains
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
│       ├── acquisition/           Raw basemap, emissions, NRN, NHN, and CanCO₂ acquisition
│       │   └── co2_storage.py     Local CanCO₂ release acquisition
│       ├── analysis/              Folium maps and output-table exports
│       ├── config/                Build-profile parsing and validation
│       ├── costs/
│       │   └── pipelines/
│       │       └── h2/            H2 pipeline capacity and cost models
│       ├── emissions/             Facility-emissions preprocessing
│       ├── execution/             Silver orchestration, single runs, and batches
│       ├── geospatial/            Basemaps, hydrography, adjacency, roads, connectivity
│       │   └── co2_storage.py     Storage-to-basemap integration and previews
│       ├── preprocessing/         Legacy input harmonization
│       ├── registry/              Dataset/stage metadata
│       ├── schema/                CANOE/TEMOA encoding and database utilities
│       └── paths.py               Shared repository-root discovery
│
├── scripts/                       Thin CLI entry points
│   ├── batch_run.py
│   ├── build_schema.py
│   ├── build_silver.py
│   ├── create_map_folium.py
│   ├── export_output_tables.py
│   └── main_run.py
│
├── diagnostics/                  Input, balance, and run-audit utilities
├── notebooks/                    Exploratory and development notebooks
├── figures/                      Generated maps and diagnostic figures
├── output_files/                 Timestamped optimization runs
├── legacy_files/                 Retained legacy data and references
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

For this release candidate, the command should print `0.7.0`.

### Development installation

Use this option if you plan to modify the codebase, run tests, use linting or type checking, or work interactively with Jupyter.

Install Geospatial-CANOE with its development extra:

```bash
python -m pip install -e ".[dev]"
```

The `dev` extra adds testing, coverage, linting, type-checking, pre-commit, and
interactive Jupyter tooling to the complete runtime environment.

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
config/build_profiles/provinces_only.toml
```

The same profile should be used consistently across the basemap, adjacency,
road, road-connectivity, legacy-input, emissions, cost, and schema stages. Each
profile also declares a short artifact identity:

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

Gasoline demand follows the same representative-year convention. Demand points
are summed only when they snap to the same model region, then their values are
written unchanged to Gold `Demand`; they are not multiplied by 25. The
`demand.csv` values are annual tonnes of gasoline calculated from annual litres
using `0.00074 t/L`, and Gold records their units as `t/year`. Solved annual
gasoline flow can be multiplied by 25 for a cumulative single-period report, but
the cumulative value must not be supplied as `Demand`.

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

The preferred preprocessing entry point is the silver-layer orchestrator:

```bash
python scripts/build_silver.py     --config config/build_profiles/provinces_only.toml
```

The package-native equivalent is:

```bash
python -m geocanoe.execution.silver     --config config/build_profiles/provinces_only.toml
```

The silver workflow validates dependencies and executes the configured preprocessing stages in dependency order.

NHN is transformed after basemaps because the configured study-area boundary
defines the spatial subset. `registry/geospatial_sources.yaml` maps stable
source IDs to the Bronze GeoPackage, source layers, and NHN coded domains;
the build profile's `[hydrography]` tables select readable classes and minimum
source area/length thresholds. Outputs are written to:

```text
data_files/processed/nhn/{study_area}_filtered_hydrography.gpkg
data_files/processed/nhn/{study_area}_hydrography_summary.csv
data_files/processed/nhn/{study_area}_hydrography_manifest.json
data_files/processed/nhn/preview/*.png
```

### Raw acquisition

Raw acquisition modules may also be run independently when source datasets need to be refreshed:

```bash
python -m geocanoe.acquisition.basemaps
python -m geocanoe.acquisition.nrn
python -m geocanoe.acquisition.nhn
python -m geocanoe.acquisition.emissions
python -m geocanoe.acquisition.co2_storage
```

Primary outputs:

```text
data_files/raw/basemaps/
data_files/raw/nrn/{PROVINCE}/
data_files/raw/nhn/rhn_nhn_hhyd.gpkg
data_files/raw/nhn/nhn_wms_capabilities.xml
data_files/raw/nhn/nhn_acquisition_manifest.json
data_files/raw/emissions/co2_large_facilities_2024/
data_files/raw/canco2_storage/
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
python scripts/build_bronze.py --stages nhn
```

Use `--overwrite` only when the national archive should be downloaded and
extracted again.

### Silver preprocessing stages

The current silver workflow includes:

1. legacy site and demand province mapping;
2. emissions preprocessing;
3. hydrogen-pipeline capacity-cost preprocessing;
4. hydrogen-pipeline cost-model fitting;
5. study-area basemap construction;
6. onshore CO₂ storage-to-basemap integration;
7. regional adjacency construction;
8. processed road-network construction;
9. road-connectivity mapping.

These stages are implemented under `src/geocanoe/` and orchestrated by `geocanoe.execution.silver`.

Important silver outputs include:

```text
data_files/processed/legacy_inputs/
data_files/processed/emissions/
data_files/processed/costs/
data_files/processed/basemaps/
data_files/processed/co2_storage/
data_files/processed/graph/
data_files/processed/nrn/
data_files/processed/road_connectivity/
```

The storage stage can also be run independently after basemap construction:

```bash
python -m geocanoe.geospatial.co2_storage \
    --config config/build_profiles/provinces_only.toml
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
python scripts/build_schema.py \
    --config config/build_profiles/on-qc.toml \
    --scenario registry/scenarios/baseline.toml
```

or:

```bash
python -m geocanoe.schema.build \
    --config config/build_profiles/on-qc.toml \
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

The current generalized pipeline assumption applies the processed hydrogen-pipeline capacity and cost representation to all pipeline technologies until commodity-specific pipeline cost layers are available.

### Single-scenario execution

Run CANOE/TEMOA from an encoded SQLite database:

```bash
python scripts/main_run.py
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
python scripts/batch_run.py     --config config/batch_profiles/batch_run.toml
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
python diagnostics/check.py
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
python diagnostics/check.py inputs --config config/build_profiles/on-qc.toml --basemap on_qc_basemap_25km_centroid --scenario registry/scenarios/baseline.toml
python diagnostics/check.py outputs output_files/<run>
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
python scripts/export_output_tables.py
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
python scripts/create_map_folium.py
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
    ├── NRN
    └── emissions
        │
        ▼
silver preprocessing
    ├── legacy inputs
    ├── emissions
    ├── H2 pipeline capacity-cost processing
    ├── H2 pipeline cost models
    ├── basemaps
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
data_files/processed/graph/
data_files/processed/nrn/
data_files/processed/road_connectivity/
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
- pipeline routing follows candidate graph corridors rather than a terrain-aware routing model;
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
