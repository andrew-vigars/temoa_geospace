# 1. Architecture and the Temoa boundary

> **Draft status.** Derived from [scripts/model_workflow.md](../../../scripts/model_workflow.md),
> the README, and the code on `geo-dev`.

## 1.1 Layers

| Layer | CLI | Orchestrator | Responsibility | Output |
|---|---|---|---|---|
| Bronze | `geocanoe-build-bronze` | `execution/bronze.py` | Download and register raw sources | `data_files/raw/` |
| Silver | `geocanoe-build-silver` | `execution/silver.py` (18 stages) | Spatial processing, evidence products, and renewable resource/capacity bundles | `data_files/processed/*` |
| Gold | `geocanoe-build-schema` | `schema/build.py` | Encode Silver + assumptions as a Temoa v4 SQLite | `data_files/processed/schema/gold_*.sqlite` + manifest |
| Solve | `geocanoe-run`, `geocanoe-batch` | `execution/run.py`, `batch.py`, `temoa_backend.py` | Run Temoa on the Gold database | `output_files/` run directories |
| Analysis | `geocanoe-export`, `geocanoe-map` | `analysis/` | Decode solved outputs | Excel, Folium HTML |

## 1.2 Where the Temoa line sits

**Temoa begins at one function call.** `execution/temoa_backend.py::run_temoa`
builds a `TemoaConfig` from a TOML file and calls `TemoaSequencer(config).start()`.
`execution.run` operates only on an already-encoded database and never rebuilds
the schema. Temoa receives exactly two things: a Gold SQLite and a Temoa
TOML solver configuration. Solver TOMLs are generated with
`geocanoe-init-temoa-config` or maintained locally; the updated branch does not
commit a `config/temoav4/` directory.

**Everything upstream is pre-processing, including Gold.** Gold's output is in
Temoa's schema, but it is built by GeoCANOE code. It loads the schema from the
installed `temoa.db_schema/temoa_schema_v4.sql` and the economies-of-scale
`tables.sql`, and its fingerprint includes a digest of the installed Temoa
backend source.

| Concern | Owned by |
|---|---|
| Source acquisition, CRS, grids, graph, roads, impedance, demand/storage/emissions proxies | GeoCANOE Silver |
| Snapping points to nodes, regional tables, EOS curves, cost rows, scenario limits | GeoCANOE Gold |
| Objective, constraints, MILP for piecewise-EOS capacity, solver and MIP gap | Temoa |
| Result tables (`Output*`), logs | Temoa, then GeoCANOE analysis decodes them |

## 1.3 Gold has two jobs

1. **Spatial-to-model mapping**: canonical node and edge regions, snapping
   point inputs to nodes, restricting truck links to road-enabled edges,
   applying Silver impedance multipliers to pipeline costs.
2. **Non-spatial model assumptions** from `registry/model.toml` and
   `registry/scenarios/*.toml`: time horizon (2025–2050, one period), discount
   and loan rate, emissions pricing, absolute or point-source-fraction storage
   targets, transport exclusions, and the legacy-gasoline toggle, plus
   technology and cost tables from `registry/*.csv`.
3. **Optional renewable replacement** from a checksum-validated Silver bundle:
   ERA5/Atlite wind and PV profiles, land-derived capacity bounds, ATB costs,
   and an explicit CAD2020 conversion contract replace legacy `ELC_GEN` rows.

The documentation should treat these as separate chapters. Job 1 is part of
the geospatial methodology. Job 2 is model parameterization.

## 1.4 Configuration inputs

```text
Silver build profile + registry/model.toml + selected scenario.toml
                 ↓ validated effective configuration
                 Gold SQLite + manifest   →   Temoa TOML + Gold  →  solved SQLite
```

Silver build profile (`config/build_profiles/*.toml`) is separate from the
Temoa solver configuration used at run time.

## 1.5 Reproducibility mechanisms

- Silver products are durable checkpoints under `data_files/processed/`.
- Gold filename: `gold_<build-id>_<scenario-id>_<fingerprint>_<barcode>.sqlite`.
  The fingerprint hashes the normalized build profile, basemap, merged model
  config, and schema-builder and Temoa-backend source digests. The barcode is
  unique per invocation, so rebuilds never overwrite.
- A manifest stores resolved paths and the implementation digest.
- `distribution_record()` records the installed Temoa version and VCS commit for each run.

## 1.6 Known gaps relevant to the boundary

- The generalized pipeline cost layer is based on hydrogen-pipeline data and
  is applied to all pipeline technologies.
- Renewable profiles use one weather year (2018), fixed land exclusions and
  configured deployment densities; they are not a siting or climatology study.
- Temoa v4 is consumed from a personal fork pending an upstream PR (see README install notes).

## 1.7 Chapter map

| Layer | Chapters |
|---|---|
| Foundations | [2 configuration](02-configuration-and-setup.md) |
| Bronze | [3](03-bronze-acquisition.md) |
| Silver | [4 basemap and graph](04-basemap-and-graph.md), [5 roads](05-road-network.md), [6 impedance](06-pipeline-impedance.md), [7 CO₂ storage](07-co2-storage.md), [8 gasoline demand](08-gasoline-demand.md), [9 emissions and sites](09-emissions-and-site-inputs.md), [10 pipeline costs](10-pipeline-cost-models.md) |
| Gold | [11 spatial mapping](11-gold-spatial-mapping.md), [12 technologies, costs, scenarios](12-gold-technology-cost-scenario.md) |
| Solve and analysis | [13](13-temoa-execution.md), [14](14-diagnostics-and-analysis.md) |
| Reference | [15 unit conventions](15-unit-conventions.md), [16 known assumptions and limitations](16-known-assumptions-and-limitations.md) |

Data provenance is in [Part B: Data sources](../data-sources/index.md).
