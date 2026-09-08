# Geospatial-CANOE

Geospatial-CANOE extends the CANOE/TEMOA energy-system modelling framework with a modular geospatial data, schema, execution, and analysis pipeline. It converts Canadian boundary, road, emissions, demand, technology, and cost data into a spatial graph, encodes that graph as a CANOE/TEMOA-compatible SQLite database, executes model scenarios, and provides post-solve exports and interactive maps.

The repository is an active research codebase. The current implementation uses the TEMOA v3-compatible CANOE backend and introduces mixed-integer formulations where transport infrastructure requires economies of scale, particularly for pipelines. Migration to TEMOA v4 is planned after the geospatial workflow is stable.

## Current capabilities

The workflow currently supports:

- configurable Canadian study areas defined by province and territory;
- geographic grids in EPSG:4326 and projected grids in EPSG:3347;
- centroid-based cell retention and rook adjacency;
- filtered National Road Network backbone, primary-freight, and freight-access layers;
- weak and strong road-connectivity mapping;
- province assignment and graph-node snapping for legacy point inputs;
- preprocessing of 2024 large-facility greenhouse-gas emissions;
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
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
│
├── config/
│   ├── build_profiles/
│   │   └── *.toml                 Geospatial preprocessing profiles
│   └── batch_profiles/
│       └── *.toml                 Ordered model-run batches
│
├── data_files/
│   ├── raw/                       Downloaded source datasets
│   │   ├── basemaps/
│   │   ├── emissions/
│   │   └── nrn/
│   ├── models/                    Controlled engineering and cost workbooks
│   │   └── cost_models/
│   ├── processed/                 Durable workflow checkpoints
│   │   ├── basemaps/
│   │   ├── costs/
│   │   ├── emissions/
│   │   ├── graph/
│   │   ├── legacy_inputs/
│   │   ├── nrn/
│   │   ├── road_connectivity/
│   │   └── schema/
│   ├── CANOE_geospatial.sqlite    Baseline CANOE/TEMOA database
│   ├── canoe_dataset_schema.sql   Base relational schema
│   └── *.csv                      Static model input tables
│
├── src/
│   └── geocanoe/
│       ├── acquisition/           Raw basemap, emissions, and NRN acquisition
│       ├── analysis/              Folium maps and output-table exports
│       ├── config/                Build-profile parsing and validation
│       ├── costs/
│       │   └── pipelines/
│       │       └── h2/            H2 pipeline capacity and cost models
│       ├── emissions/             Facility-emissions preprocessing
│       ├── execution/             Silver orchestration, single runs, and batches
│       ├── geospatial/            Basemaps, adjacency, roads, connectivity
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

Choose the installation method based on how you plan to use Geospatial-CANOE.

### Standard runtime installation

Use this option if you only need to run the Geospatial-CANOE workflow and CANOE/TEMOA model.

From the repository root, install the runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` installs the Geospatial-CANOE runtime dependencies and also installs the TEMOA dependencies referenced by `temoa/requirements.txt`.

Then install Geospatial-CANOE into the active environment in editable mode:

```bash
python -m pip install -e .
```

A successful installation should make the package importable without modifying `PYTHONPATH`:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

### Development installation

Use this option if you plan to modify the codebase, run tests, use linting or type checking, or work interactively with Jupyter.

Install the development dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes all runtime dependencies from `requirements.txt` and adds development tools such as `pytest`, `pytest-cov`, `mypy`, `ruff`, `pre-commit`, `ipykernel`, `jupyter`, and `jupyterlab`.

Then install Geospatial-CANOE into the active environment in editable mode:

```bash
python -m pip install -e .
```

Verify the installation with:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

### Reproducing the pinned environment

Use this option if you need to reproduce the exact package versions captured in the current project environment as closely as possible.

Install the pinned dependency set:

```bash
python -m pip install -r requirements-lock.txt
```

Then install Geospatial-CANOE into the active environment in editable mode:

```bash
python -m pip install -e .
```

Verify the installation with:

```bash
python -c "import geocanoe; print(geocanoe.__version__)"
```

In general:

- Use `requirements.txt` for normal runtime use.
- Use `requirements-dev.txt` for development, testing, linting, and Jupyter work.
- Use `requirements-lock.txt` when reproducibility of the exact dependency versions is important.

## Configuration

Geospatial preprocessing stages use a shared TOML build profile loaded through `geocanoe.config`. A typical profile defines:

- study-area label and included provinces or territories;
- geographic and projected grid resolutions;
- centroid retention and coordinate precision;
- rook-adjacency settings;
- road classes, processed road networks, and output CRS;
- weak and/or strong road-connectivity methods;
- the road layer, road-connectivity method, and basemap used during schema construction;
- point-assignment boundary and snapping tolerances.

Example profile path:

```text
config/build_profiles/provinces_only.toml
```

The same profile should be used consistently across the basemap, adjacency, road, road-connectivity, legacy-input, emissions, cost, and schema stages.

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

### Raw acquisition

Raw acquisition modules may also be run independently when source datasets need to be refreshed:

```bash
python -m geocanoe.acquisition.basemaps
python -m geocanoe.acquisition.nrn
python -m geocanoe.acquisition.emissions
```

Primary outputs:

```text
data_files/raw/basemaps/
data_files/raw/nrn/{PROVINCE}/
data_files/raw/emissions/co2_large_facilities_2024/
```

### Silver preprocessing stages

The current silver workflow includes:

1. legacy site and demand province mapping;
2. emissions preprocessing;
3. hydrogen-pipeline capacity-cost preprocessing;
4. hydrogen-pipeline cost-model fitting;
5. study-area basemap construction;
6. regional adjacency construction;
7. processed road-network construction;
8. road-connectivity mapping.

These stages are implemented under `src/geocanoe/` and orchestrated by `geocanoe.execution.silver`.

Important silver outputs include:

```text
data_files/processed/legacy_inputs/
data_files/processed/emissions/
data_files/processed/costs/
data_files/processed/basemaps/
data_files/processed/graph/
data_files/processed/nrn/
data_files/processed/road_connectivity/
```

### Schema encoding

Encode a selected geospatial configuration into a CANOE/TEMOA database:

```bash
python scripts/build_schema.py     --config config/build_profiles/provinces_only.toml
```

or:

```bash
python -m geocanoe.schema.build     --config config/build_profiles/provinces_only.toml
```

The schema workflow resolves a compatible basemap, graph, road layer, and road-connectivity product; snaps tabular and point inputs to graph nodes; rebuilds topology-dependent CANOE/TEMOA tables; applies topology-independent pipeline cost templates to graph corridors; validates the encoded database; and writes:

```text
data_files/processed/schema/
    CANOE_geospatial_<basemap_stem>_<road_layer>_<connection_method>.sqlite
```

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

### Export solved output tables

Export available `Output*` tables from a solved SQLite database:

```bash
python scripts/export_output_tables.py
```

or:

```bash
python -m geocanoe.analysis.exports
```

The exporter writes each selected table to a separate worksheet in a single Excel workbook and validates worksheet dimensions against Excel limits.

### Interactive mapping

Generate an interactive Folium map from a solved scenario:

```bash
python scripts/create_map_folium.py
```

or:

```bash
python -m geocanoe.analysis.maps
```

The mapping workflow infers the associated geospatial products from the selected solved database, decodes transport pseudo-regions back to graph edges, separates parallel active transport corridors for visualization, and exports an interactive Leaflet/Folium HTML map with layer controls, tooltips, and popups.

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
- geological CO₂ storage, DAC, BECCS, and additional carbon-management pathways;
- rail, marine, reused natural-gas corridors, and expanded electricity transmission;
- e-diesel, e-SAF, and e-methanol pathways;
- multi-period optimization and improved temporal representation;
- terrain- and infrastructure-aware routing;
- multi-objective and modelling-to-generate-alternatives analysis;
- network robustness, hub persistence, and near-optimal corridor analysis;
- stronger automated input, balance, and schema-integrity tests.

## Status

The preprocessing, schema-building, execution, export, and interactive mapping pipeline is operational for the current road, truck, pipeline, and electricity-transmission representation. The codebase remains under active refactoring and should be treated as research software rather than a stable public API.

For the workflow overview, see [`scripts/model_workflow.md`](scripts/model_workflow.md).
