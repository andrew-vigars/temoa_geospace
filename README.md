# Geospatial-CANOE Workflow

## Overview

This repository extends the TEMOA framework with a geospatial
preprocessing workflow for building spatially explicit transport
networks and encoding them into a CANOE/TEMOA-compatible SQLite
database.

The current workflow is built from TEMOA v3 and will later be migrated
to TEMOA v4 once the geospatial framework is fully operational.

To represent economies of scale associated with transport
infrastructure, particularly pipelines, the current implementation is
solved as a mixed-integer linear program (MILP). During development and
testing the model is typically run with a 1% MIP gap to reduce solve
times. For scientific analyses with sufficient computational resources,
this value can be reduced by modifying the solver settings within TEMOA.

The workflow converts raw GIS and tabular datasets into a regional
graph, overlays transport infrastructure, maps demand, emissions,
electricity generation potential, and other spatial attributes onto
graph regions, builds an encoded CANOE/TEMOA SQLite database, executes
TEMOA, and provides diagnostic utilities for validating both model
inputs and outputs. Intermediate products also serve as permanent 
checkpoints that allow individual workflow stages to be rerun 
without recomputing previous spatial operations.

Additional diagnostic scripts compare optimized Pyomo variables against
exported SQLite tables to verify unit consistency, value consistency,
and correct database encoding.

This README is intended as an internal developer guide. It documents the
current workflow structure and the primary scripts used to acquire data,
preprocess geospatial datasets, build model schemas, execute TEMOA,
audit model outputs, and generate visualization products.

------------------------------------------------------------------------

## Workflow Philosophy

The Geospatial-CANOE workflow is intentionally modular.

Each major transformation writes intermediate datasets to disk under
`data_files/processed/` rather than retaining the entire workflow in
memory. This makes every processing stage easier to inspect, rerun,
debug, and modify independently.

Many GIS operations involve computationally expensive spatial joins,
overlays, and set operations. Writing intermediate products to disk
reduces memory pressure while allowing individual processing stages to
be rerun without rebuilding the entire preprocessing workflow.

The overall design pattern is:

1.  Acquire raw datasets.
2.  Clean and standardize datasets into intermediate products.
3.  Build the model-region topology.
4.  Overlay transport infrastructure and spatial attributes onto the
    graph.
5.  Encode the graph into a CANOE/TEMOA SQLite database.
6.  Execute TEMOA.
7.  Audit and visualize the solved model.

This modular architecture supports research development because
individual workflow stages can be replaced, expanded, or improved
without rebuilding the complete preprocessing pipeline.

A longer-term objective is to expose this modularity directly to users,
allowing different geographic regions, transport layers, and
preprocessing methods to be selected during graph construction and
schema encoding.

------------------------------------------------------------------------

## Repository Structure

    data_files/
        raw/
        processed/

    scripts/

    temoa/

    output_files/

    figures/

    old_scripts/

Most development occurs within `scripts/`, while intermediate products
are stored under `data_files/processed/`.

------------------------------------------------------------------------

## Typical Workflow

Stage 0 - Acquire raw datasets --> get_raw_basemap.py, get_raw_nrn.py, get_raw_emissions.py

Stage 1 - Build processed geospatial datasets --> build_basemaps.py

Stage 2 - Construct the regional graph --> build_region_adjacency.py

Stage 3 - Build transport layers --> build_roads.py

Stage 4 - Overlay transport connectivity --> map_roads.py

Stage 5 - Encode the CANOE/TEMOA SQLite schema --> build_schema.py

Stage 6 - Execute TEMOA --> main_run.py

Stage 7 - Audit results and generate Canadian supply chain map --> audit_inputs.py, audit_outputs.py, create_map.py

Optional Stage - Compare pyomo results to written sqlite results to validate ETL logic --> etl_diagnostics.py

------------------------------------------------------------------------

## Future Development

Transport modes

- Rail
- Marine
- Natural gas network reuse
- Expanded electricity transmission

Fuels

- e-diesel
- e-SAF
- e-methanol

Model improvements

- Geographic pipeline routing
- Improved transport costing
- Multi-period optimization
- MGA and network robustness analysis

Carbon management

- DAC
- Geological CO₂ storage
- BECCS

## Current Status

The current workflow represents an active research codebase.

The preprocessing pipeline is considered operational for road and
pipeline transport. Additional transport modes, cost functions, and
spatial datasets are under active development.