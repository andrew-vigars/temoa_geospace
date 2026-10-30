# 2. Configuration and setup

*Code: `geocanoe.config` (`project_config.py`, `model_config.py`, `_toml.py`), `geocanoe.paths`.
Files: [config/build_profiles/](../../../config/build_profiles), [config/renewables/](../../../config/renewables),
[registry/model.toml](../../../registry/model.toml), [registry/scenarios/](../../../registry/scenarios),
[config/batch_profiles/](../../../config/batch_profiles).*

> **Draft status.** Conventions are in [conventions](../../conventions.md).

## 2.1 Configuration layers

The model separates **where and how space is processed**, **what the energy system is**, **how Temoa solves**, and **which runs to execute**.

| File type | Location | Decides | Read by |
|---|---|---|---|
| **Build profile** | `config/build_profiles/*.toml` | Study area, grid resolutions, road classes, connectivity method, hydrography and impedance selection, gasoline proxy strategy, snapping and storage eligibility | Silver stages and Gold |
| **Renewables settings** | `config/renewables/*.toml` | ERA5/Atlite conversion, physical technologies, ATB selection, land exclusions, costs, capacity limits and Gold replacement | Renewable Silver and Gold |
| **Global model settings** | `registry/model.toml` | Time horizon, discount rates, emissions projection, storage requirement, basemap choice, transport-mode switches, pipeline-cost scope | Gold |
| **Scenario** | `registry/scenarios/*.toml` | Overrides of the model settings only | Gold |
| **Temoa configuration** | generated or local TOML | Solver, scenario mode, extensions, input database, outputs | Temoa (via `geocanoe.execution.run`) |
| **Batch profile** | `config/batch_profiles/*.toml` | Ordered list of Temoa configurations to run | `geocanoe.execution.batch` |

The build profile is **separate** from the Temoa configuration. The profile is shared by every Silver stage and Gold; the Temoa
configuration is used only for the solve.

## 2.2 Layering and precedence

```text
Silver build profile ──────────────┐
registry/model.toml (defaults)     ├──→ validated effective configuration ──→ Gold SQLite + manifest
  + scenarios/<id>.toml (overrides)┘
Gold SQLite + solver configuration TOML ──→ Temoa solve
```

Rules enforced by `model_config.py`:
- A scenario may override `[time]`, `[finance]`, `[emissions]`, `[storage]`, `[basemap]`, `[transport_modes]`, `[pipeline_costs]`, `[conventional_gasoline]`, and `[synthetic_gasoline]`. Unknown keys and inconsistent policy combinations fail before schema construction. `[emissions]` can select an atmospheric emissions price; `[storage]` can select no floor, an absolute cumulative target, or a fraction of included point-source emissions. Conventional gasoline selects a validated Silver price configuration; synthetic gasoline sets the aggregate minimum share.
- A scenario never changes Silver. Silver evidence layers are built once per profile and reused.
- If no scenario is passed, `registry/scenarios/baseline.toml` is used.

## 2.3 Build-profile reference

Defined by the committed profiles in [config/build_profiles/](../../../config/build_profiles), including the always-resolvable sample and national renewable profiles.

| Block | Key parameters | Chapter |
|---|---|---|
| `[profile]` | `id` (becomes part of the build ID and file names) | this chapter |
| `[study_area]` | `label`, `provinces` | [4](04-basemap-and-graph.md) |
| `[basemaps]`, `[basemaps.projected]`, `[basemaps.geographic]` | `keep_method`, `coordinate_precision`, `resolutions_km`, `resolutions` | [4](04-basemap-and-graph.md) |
| `[adjacency]` | `method = "rook"`, `no_neighbor_id` | [4](04-basemap-and-graph.md) |
| `[roads]`, `[roads.classes]` | `networks`, `output_crs`, class lists | [5](05-road-network.md) |
| `[road_connectivity]` | `road_layer`, `methods` | [5](05-road-network.md) |
| `[schema]` | `road_connection_method`, `interactive_basemap_selection`, `basemap_stem`, `max_snap_distance_factor`, `boundary_buffer_km`*, `boundary_simplify_tolerance_km`* | [11](11-gold-spatial-mapping.md) |
| `[storage]`, `.quantitative`, `.qualitative`, `.all_mapped` | `eligibility`, source selection, `capacity_mapping` (`unlimited`, `equal_weighted`, `shared`) | [7](07-co2-storage.md) |
| `[gasoline_demand]`, `.proxies`, `.proxies.additional_hubs` | `sales_year`, density, `strategy`, population thresholds | [8](08-gasoline-demand.md) |
| `[pipeline_impedance]` | `enabled`, `penalty_registry`, `penalty_profile`, `output_crs` | [6](06-pipeline-impedance.md) |
| `[hydrography]`, `.polygons`, `.lines` | `source_id`, classes, area thresholds, permanency | [6](06-pipeline-impedance.md) |
| `[renewables]` | `enabled`, path to renewable settings | [12](12-gold-technology-cost-scenario.md) and [renewable workflow](../../renewables_silver.md) |

\* Parsed and printed, but read by no other module (see [11](11-gold-spatial-mapping.md), assumption 9).

**Build ID and artifact naming.** The profile `id` and study-area `label` feed the build ID that names Silver output folders
(`data_files/processed/…/{build-id}/`) and Gold files. Changing the study area therefore requires a new profile ID, not an edit in place.

## 2.4 Global model settings reference

Defined in [registry/model.toml](../../../registry/model.toml). Full table in [12](12-gold-technology-cost-scenario.md) section 12.2.

## 2.5 Paths and layout

All paths resolve through `geocanoe.paths.find_project_root()`. Package modules do not hard-code locations relative to their own file.

```text
data_files/raw/        Bronze (not committed)
data_files/processed/  Silver and Gold (not committed, except the pipeline_impedance README)
output_files/          Solved runs and batches (not committed)
registry/              Controlled inputs, source registry, scenarios (committed)
config/                Build, source, renewable and batch configuration
```

## 2.6 Installation and environment

- `python -m pip install -e .` installs the full research environment, including the Temoa v4 runtime. `.[dev]` adds test, lint, type-check, pre-commit and notebook tooling.
  `pyproject.toml` is the canonical dependency declaration; the requirements files are compatibility installers.
- Temoa v4 is currently consumed from a personal fork pending an upstream pull request (see the README install notes).
- Solver configurations can be generated with `geocanoe-init-temoa-config`; Gurobi configurations require a licence.
- Python 3.12 is the interpreter used for the tests committed in this repository (from the compiled test caches). **[confirm]** the minimum supported version.

## 2.7 CLI entry points

| Command | Module | Part A chapter |
|---|---|---|
| `geocanoe-build-bronze` | `execution.bronze` | [3](03-bronze-acquisition.md) |
| `geocanoe-build-silver` | `execution.silver` | [4](04-basemap-and-graph.md) to [10](10-pipeline-cost-models.md) |
| `geocanoe-build-schema` | `schema.build` | [11](11-gold-spatial-mapping.md), [12](12-gold-technology-cost-scenario.md) |
| `geocanoe-init-temoa-config` | `execution.temoa_config` | [13](13-temoa-execution.md) |
| `geocanoe-run`, `geocanoe-batch` | `execution.run`, `execution.batch` | [13](13-temoa-execution.md) |
| `geocanoe-diagnostics` | `diagnostics.cli` | [14](14-diagnostics-and-analysis.md) |
| `geocanoe-export`, `geocanoe-map` | `analysis.exports`, `analysis.maps` | [14](14-diagnostics-and-analysis.md) |

The files in `scripts/` are thin wrappers around the same entry points, kept for convenience.

## 2.8 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Several build profiles are committed, but published results still depend on selected/generated Silver artifacts and external raw data. | Archive the exact profile and manifests with each result. Gold and run manifests record configuration snapshots and hashes. |
| 2 | The Gold fingerprint covers the normalized profile, selected inputs, effective model settings, and implementation digests. | Any material change gives a new Gold filename, and solver TOMLs naming the old file must be updated (see [update procedures](../data-sources/update-procedures.md)). |
| 3 | Defaults are split across `model.toml`, scenario files and literals in `schema/build.py`. | Some parameters (fixed costs, input splits, analytic EOS constants) are not configurable. **[confirm]** whether to move them into `registry/`. |
