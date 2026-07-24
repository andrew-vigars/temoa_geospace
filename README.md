# Geospatial-CANOE

Geospatial-CANOE extends the CANOE/TEMOA energy-system modelling framework with a modular geospatial preprocessing pipeline. It converts Canadian boundary, road, emissions, demand, technology, and cost data into a spatial graph and then encodes that graph as a CANOE/TEMOA-compatible SQLite database.

The repository is an active research codebase. The current implementation uses the TEMOA v3-compatible CANOE backend and introduces mixed-integer formulations where transport infrastructure requires economies of scale, particularly for pipelines. Migration to TEMOA v4 is planned after the geospatial workflow is stable.

## Current capabilities

The workflow currently supports:

- configurable Canadian study areas defined by province and territory;
- geographic grids in EPSG:4326 and projected grids in EPSG:3347;
- centroid-based cell retention and rook adjacency;
- filtered National Road Network backbone and freight-access layers;
- weak and strong road-connectivity mapping;
- province assignment and graph-node snapping for legacy point inputs;
- preprocessing of 2024 large-facility greenhouse-gas emissions;
- topology-independent hydrogen-pipeline cost preprocessing and curve fitting;
- graph-based pipeline, truck, and electricity-transmission links;
- schema encoding into a CANOE/TEMOA SQLite database;
- timestamped model runs with archived inputs, solved databases, provenance records, and Excel exports;
- post-solve transport-flow mapping.

## Design philosophy

The pipeline is intentionally staged. Each expensive spatial or tabular transformation writes a durable intermediate product under `data_files/processed/`. These products act as checkpoints, so downstream stages can be rerun without repeating raw-data acquisition or earlier GIS operations.

The architecture separates three concerns:

1. **Build configuration** — a TOML profile controls the study area and geospatial preprocessing choices.
2. **Model data construction** — scripts transform raw and static inputs into a selected graph and encoded SQLite database.
3. **Model execution and analysis** — TEMOA solves the encoded database, after which outputs are exported and mapped.

The geospatial TOML profile is separate from the TEMOA solver configuration used by `main_run.py`.

## Repository structure

The tree below is intentionally curated. It shows the stable project architecture and omits virtual environments, type-checker caches, generated solver logs, and most individual data products.

```text
Geospatial-CANOE/
├── README.md
├── db_mgmt.py
├── db_update.py
├── requirements.txt
├── requirements-dev.txt
│
├── config/
│   └── build_profiles/
│       └── *.toml                 Geospatial preprocessing profiles
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
├── scripts/
│   ├── project_config.py
│   ├── get_raw_basemap.py
│   ├── get_raw_nrn.py
│   ├── get_raw_emissions.py
│   ├── build_basemaps.py
│   ├── build_region_adjacency.py
│   ├── build_roads.py
│   ├── map_roads.py
│   ├── map_legacy_inputs.py
│   ├── build_emissions.py
│   ├── build_h2_pipeline_costs.py
│   ├── build_h2_pipeline_cost_models.py
│   ├── build_schema.py
│   ├── main_run.py
│   ├── export_output_tables.py
│   ├── create_map.py
│   └── model_workflow.md
│
├── diagnostics/                  Input, balance, and run-audit utilities
├── notebooks/                    Exploratory and development notebooks
├── figures/                      Generated maps and diagnostic figures
├── output_files/                 Timestamped optimization runs
├── legacy_files/                 Retained legacy data and references
├── legacy_workflow/              Superseded workflow implementations
└── temoa/                        CANOE/TEMOA optimization backend
```

Most active development occurs in `scripts/`. Generated intermediate products belong under `data_files/processed/`, while solved scenarios and run provenance belong under `output_files/`.

## Configuration

Stages 1–5 use a shared TOML build profile loaded by `project_config.py`. A typical profile defines:

- study-area label and included provinces or territories;
- geographic and projected grid resolutions;
- centroid retention and coordinate precision;
- rook-adjacency settings;
- road classes, processed road networks, and output CRS;
- weak and/or strong road-connectivity methods;
- the road-connectivity method and basemap used during schema construction;
- point-assignment boundary and snapping tolerances.

Example profile path:

```text
config/build_profiles/provinces_only.toml
```

The same profile should be used consistently across basemap, adjacency, road, connectivity, legacy-input, and schema stages.

## Workflow

### Stage 0 — Acquire raw datasets

```bash
python scripts/get_raw_basemap.py
python scripts/get_raw_nrn.py
python scripts/get_raw_emissions.py
```

Use `--overwrite` to replace valid existing raw outputs. The basemap and NRN scripts also accept optional output-directory overrides.

Primary outputs:

```text
data_files/raw/basemaps/
data_files/raw/nrn/{PROVINCE}/
data_files/raw/emissions/co2_large_facilities_2024/
```

### Stage 0B — Prepare static point and cost inputs

Assign province codes to the legacy site and demand tables:

```bash
python scripts/map_legacy_inputs.py \
    --config config/build_profiles/provinces_only.toml
```

Prepare the hydrogen-pipeline engineering cost dataset:

```bash
python scripts/build_h2_pipeline_costs.py
python scripts/build_h2_pipeline_cost_models.py
```

The first script standardizes the controlled workbook into a canonical capacity-cost table. The second fits and selects cost functions, then exports topology-independent `ETLSegment` and OPEX templates used by `build_schema.py`.

### Stage 1 — Build study-area basemaps

```bash
python scripts/build_basemaps.py \
    --config config/build_profiles/provinces_only.toml
```

Outputs include dissolved study-area boundaries, all configured geographic and projected grids, and a profile-level basemap summary.

### Stage 2 — Build regional adjacency

```bash
python scripts/build_region_adjacency.py \
    --config config/build_profiles/provinces_only.toml
```

For each matching Stage 1 basemap, this stage exports graph-node polygons, graph-edge tables and geometries, preview figures, and a profile-level graph summary.

### Stage 3 — Build processed road networks

```bash
python scripts/build_roads.py \
    --config config/build_profiles/provinces_only.toml
```

The script filters the selected provincial and territorial NRN layers into nested road representations:

- **backbone** — freeway, expressway/highway, and ramp;
- **freight access** — backbone classes plus arterial roads.

Study-area GeoPackages and summary tables are always exported. Provincial outputs are optional and controlled by the build profile.

### Stage 4 — Map roads onto the regional graph

```bash
python scripts/map_roads.py \
    --config config/build_profiles/provinces_only.toml
```

Two road-connectivity definitions are available:

- **weak** — both endpoint regions contain at least one road segment;
- **strong** — both endpoint regions intersect at least one common road segment.

The stage exports region-road presence tables, edge-connectivity tables, road-enabled edge geometries, road-region overlays, optional diagnostic figures, and a profile-level summary.

### Stage 5 — Encode the CANOE/TEMOA schema

```bash
python scripts/build_schema.py \
    --config config/build_profiles/provinces_only.toml
```

This stage selects or resolves one compatible basemap and road-connectivity method, loads the Stage 1–4 products, snaps tabular and point inputs to graph nodes, rebuilds the relevant CANOE/TEMOA tables, applies topology-independent pipeline cost templates to graph corridors, validates the encoded tables, and writes:

```text
data_files/processed/schema/
    CANOE_geospatial_<basemap_stem>_<connection_method>.sqlite
```

The current generalized pipeline assumption applies the processed hydrogen-pipeline capacity and cost representation to all pipeline technologies until commodity-specific pipeline cost layers are available.

### Stage 6 — Execute CANOE/TEMOA

```bash
python scripts/main_run.py
```

The interactive run workflow selects an encoded SQLite database and a TEMOA configuration, updates the database path used by the configuration, creates a timestamped run directory, records file hashes and provenance, archives the input database, executes TEMOA, archives the solved database, and exports available `Output*` tables to Excel.

Typical run outputs:

```text
output_files/<timestamped_run>/
    input_*.sqlite
    solved_*.sqlite
    manifest.json
    run configuration and logs
    output_tables.xlsx
```

### Stage 7 — Export and visualize results

Export output tables independently when needed:

```bash
python scripts/export_output_tables.py
```

Generate supply-chain and transport-flow maps:

```bash
python scripts/create_map.py
```

`create_map.py` infers the matching basemap and graph products from the selected run or database name and can optionally display web tiles, road overlays, road-enabled edges, and parallel transport arcs.

## Canonical execution order

```text
get_raw_basemap.py
get_raw_nrn.py
get_raw_emissions.py
        │
        ├── map_legacy_inputs.py
        ├── build_emissions.py
        └── build_h2_pipeline_costs.py
                └── build_h2_pipeline_cost_models.py

build_basemaps.py
        └── build_region_adjacency.py

build_roads.py
        └── map_roads.py

all prepared inputs
        └── build_schema.py
                └── main_run.py
                        ├── export_output_tables.py
                        └── create_map.py
```

`build_emissions.py`, `map_legacy_inputs.py`, and the hydrogen-pipeline cost scripts do not depend on the regional adjacency graph and can be rerun independently when their source data change.

## Workflow diagram

The diagram below shows the dependency structure rather than only the nominal script order. Independent input-preparation branches converge during schema construction, after which model execution and post-processing operate on the encoded database and solved run products.

```mermaid
flowchart TD

    %% =====================================================================
    %% Configuration
    %% =====================================================================

    CFG["project_config.py<br/>Load and validate geospatial TOML build profile"]
    TOML["config/build_profiles/*.toml<br/>Study area, grids, roads, connectivity, schema selection"]
    TOML --> CFG

    %% =====================================================================
    %% Stage 0 — Raw acquisition
    %% =====================================================================

    subgraph S0["Stage 0 — Raw data acquisition"]
        A1["get_raw_basemap.py<br/>Download and validate Statistics Canada boundaries"]
        A2["get_raw_nrn.py<br/>Download and validate provincial/territorial NRN GeoPackages"]
        A3["get_raw_emissions.py<br/>Download 2024 large-facility emissions data"]
    end

    subgraph RAW["Raw and controlled inputs"]
        R1["Province and territory boundary shapefile<br/>data_files/raw/basemaps/"]
        R2["NRN GeoPackages<br/>data_files/raw/nrn/{PROVINCE}/"]
        R3["Emissions CSV and GeoJSON<br/>data_files/raw/emissions/"]
        R4["Legacy site and demand CSVs"]
        R5["Technology, commodity, efficiency, and transport CSVs"]
        R6["CANOE/TEMOA SQL schema and baseline SQLite"]
        R7["Controlled H2 pipeline cost workbook<br/>data_files/models/cost_models/"]
    end

    A1 --> R1
    A2 --> R2
    A3 --> R3

    %% =====================================================================
    %% Independent input-preparation branches
    %% =====================================================================

    subgraph INPUTS["Independent input preparation"]
        L1["map_legacy_inputs.py<br/>Assign province codes to legacy site and demand points"]
        E1["build_emissions.py<br/>Clean and standardize facility emissions"]
        C1["build_h2_pipeline_costs.py<br/>Normalize engineering capacity-cost observations"]
        C2["build_h2_pipeline_cost_models.py<br/>Fit/select cost models and export schema templates"]
    end

    CFG --> L1
    R1 --> L1
    R4 --> L1

    R3 --> E1

    R7 --> C1
    C1 --> C2

    L1 --> LP["Processed legacy inputs<br/>data_files/processed/legacy_inputs/"]
    E1 --> EP["Clean emissions CSV, GeoPackage, metadata, and audit<br/>data_files/processed/emissions/"]
    C2 --> CP["H2 ETLSegment template and OPEX coefficients<br/>data_files/processed/costs/"]

    %% =====================================================================
    %% Stage 1 — Basemaps
    %% =====================================================================

    subgraph S1["Stage 1 — Build study-area basemaps"]
        B1["build_basemaps.py<br/>Dissolve configured jurisdictions and build regular grids"]
    end

    CFG --> B1
    R1 --> B1
    B1 --> BP["Study-area boundaries, geographic/projected grids, summary<br/>data_files/processed/basemaps/"]

    %% =====================================================================
    %% Stage 2 — Adjacency
    %% =====================================================================

    subgraph S2["Stage 2 — Build regional topology"]
        G1["build_region_adjacency.py<br/>Construct rook-adjacency graphs"]
    end

    CFG --> G1
    BP --> G1
    G1 --> GP["Graph nodes, graph edges, previews, summary<br/>data_files/processed/graph/"]

    %% =====================================================================
    %% Stage 3 — Roads
    %% =====================================================================

    subgraph S3["Stage 3 — Build processed road networks"]
        N1["build_roads.py<br/>Filter, merge, deduplicate, and export road networks"]
    end

    CFG --> N1
    R2 --> N1
    N1 --> NP["Backbone and freight-access networks, summary<br/>data_files/processed/nrn/"]

    %% =====================================================================
    %% Stage 4 — Road connectivity
    %% =====================================================================

    subgraph S4["Stage 4 — Map roads onto regional graphs"]
        M1["map_roads.py<br/>Build weak and/or strong road connectivity"]
    end

    CFG --> M1
    BP --> M1
    GP --> M1
    NP --> M1
    M1 --> MP["Region-road presence, edge connectivity, road-enabled edges,<br/>road-region overlay, plots, summary<br/>data_files/processed/road_connectivity/"]

    %% =====================================================================
    %% Stage 5 — Schema encoding
    %% =====================================================================

    subgraph S5["Stage 5 — Encode CANOE/TEMOA SQLite database"]
        S51["build_schema.py<br/>Resolve basemap and connectivity selection"]
        S52["Build canonical node and edge regions"]
        S53["Snap and aggregate site, demand, and emissions inputs"]
        S54["Rebuild technology, efficiency, cost, capacity, and ETLSegment tables"]
        S55["Validate and export encoded SQLite database"]
    end

    CFG --> S51
    BP --> S51
    GP --> S51
    MP --> S51
    LP --> S53
    EP --> S53
    CP --> S54
    R5 --> S54
    R6 --> S54

    S51 --> S52 --> S53 --> S54 --> S55
    S55 --> DB["CANOE_geospatial_<basemap>_<method>.sqlite<br/>data_files/processed/schema/"]

    %% =====================================================================
    %% Stage 6 — Model execution
    %% =====================================================================

    subgraph S6["Stage 6 — Execute CANOE/TEMOA"]
        RUN1["main_run.py<br/>Select encoded database and TEMOA configuration"]
        RUN2["Archive inputs and record file hashes/provenance"]
        RUN3["temoa/main.py<br/>Solve the LP/MILP"]
        RUN4["Archive solved database and export Output* tables"]
    end

    DB --> RUN1 --> RUN2 --> RUN3 --> RUN4

    RUN4 --> OUT["Timestamped run directory<br/>input and solved SQLite files, manifest, logs, Excel export<br/>output_files/"]

    %% =====================================================================
    %% Stage 7 — Analysis
    %% =====================================================================

    subgraph S7["Stage 7 — Export and visualize"]
        X1["export_output_tables.py<br/>Export available Output* tables to Excel"]
        X2["create_map.py<br/>Decode and map process and transport flows"]
    end

    OUT --> X1
    OUT --> X2
    GP --> X2
    BP --> X2
    MP -. optional geographic context .-> X2

    X1 --> F1["Output workbook"]
    X2 --> F2["Supply-chain and transport-flow maps"]
```

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

- centroid retention and rook adjacency are the only implemented basemap and neighbourhood methods;
- rail, marine, and existing natural-gas infrastructure are not yet encoded as dedicated transport layers;
- pipeline routing follows candidate graph corridors rather than a terrain-aware routing model;
- the current generalized pipeline cost layer is based on hydrogen-pipeline data;
- the schema workflow remains tied to the current CANOE/TEMOA database structure;
- multi-period scenario logic and uncertainty analysis are still under development.

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

The preprocessing and schema-building pipeline is operational for the current road, truck, pipeline, and electricity-transmission representation. The codebase remains under active refactoring and should be treated as research software rather than a stable public API.

For the full dependency diagram, see [`scripts/model_workflow.md`](scripts/model_workflow.md).