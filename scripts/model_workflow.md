# Geospatial-CANOE model workflow

This file is the standalone, detailed workflow reference used by developers. The README contains the same diagram for repository-level orientation, while this file retains the dependency notes immediately below it.

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

## Execution dependencies

- `build_basemaps.py`, `build_region_adjacency.py`, `build_roads.py`, `map_roads.py`, `map_legacy_inputs.py`, and `build_schema.py` must use the same TOML build profile.
- `build_region_adjacency.py` requires the Stage 1 basemaps.
- `map_roads.py` requires both the Stage 2 graph products and Stage 3 processed road networks.
- `build_schema.py` requires the selected Stage 1–4 geospatial products, processed legacy inputs, processed emissions, static CANOE tables, and the H2 pipeline cost templates.
- `main_run.py` operates only on an already encoded SQLite database; it does not rebuild the schema.
- `create_map.py` requires a 