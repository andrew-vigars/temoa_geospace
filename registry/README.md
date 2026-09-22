# Registry

This directory holds two independent kinds of user-managed input: **Bronze
dataset inputs** (raw CSVs and their declarative metadata) and the
**non-spatial model registry** (`model.toml` and `scenarios/*.toml`), which
sets the policy a Gold build encodes on top of a build profile's topology.

## How a Gold build is composed

Building a Gold artifact combines two independent, orthogonal axes plus an
optional orchestration layer. Neither axis knows about the other:

- **Topology** — `config/build_profiles/*.toml` (e.g. `national.toml`,
  `atlantic.toml`). Controls the spatial study area: which
  provinces/territories are included, basemap grid resolutions, road-network
  classes, and adjacency/connectivity method. Consumed by
  `build_basemaps.py`, `build_region_adjacency.py`, `build_roads.py`,
  `map_roads.py`, and `build_schema.py` via
  `geocanoe.config.load_geospatial_build_config`.

  Hydrography selection is also topology-specific. The `[hydrography]`
  section selects registered feature families, classes, permanency values,
  and minimum source area/length thresholds for the Silver build.

- **Model design** — `registry/model.toml` (global defaults) optionally
  overlaid by one `registry/scenarios/*.toml` file (e.g. `baseline.toml`, or
  a future `net_zero_2050.toml`). Controls non-spatial policy: the
  optimization time horizon, discounting/financing rates, the emissions
  projection method, and the geological CO2 storage requirement. A scenario
  file only declares the settings it overrides — everything else is
  inherited from `model.toml` — and never touches the Silver evidence
  layers. Consumed via `geocanoe.config.load_model_config`.

A single Gold artifact is the product of exactly one build profile and one
scenario overlay: `gold_<build-id>_<scenario-id>_<fingerprint>.sqlite`. To add
a new scenario set (e.g. "net-zero 2050"), copy
`registry/scenarios/baseline.toml`, give it a unique `[scenario].id`, and
uncomment only the sections that should differ — see the comments in that
file for the supported override sections (`[time]`, `[finance]`,
`[emissions]`, `[storage]`).

- **Orchestration (optional)** — `config/batch_profiles/*.toml` names an
  ordered list of already-built TEMOA solver configurations (each naming its
  own scenario and input/output database) to run sequentially. It does not
  select a build profile or a model-registry scenario itself; it runs
  whatever solver configs you point it at, which are typically produced from
  a chosen build-profile + scenario pair. See
  `geocanoe.execution.batch.load_batch_config`.

## Bronze dataset inputs

The user-managed CSVs in this directory (`commodities.csv`, `techs.csv`,
`generation_efficiency.csv`, `transport_techs.csv`, and others referenced
elsewhere in the repository) are source inputs to the schema-building
(Gold-layer) workflow. Add or update registry data here, and keep each entry
in `bronze_registry.yaml` synchronized with its file. Registry entries may
also point to source inputs elsewhere in the repository.

`gasoline_supply_anchors.csv` is a separate controlled Silver modelling input.
It resolves selected 2021 population centres as optional distribution anchors
for the `supply_anchors_plus_fill` proxy strategy. Its evidence fields distinguish
centres named in NRCan distribution text from centres interpreted from the
qualitative supply-orbit map; it must not be treated as a physical terminal
inventory.

### Quick start

1. Inspect `bronze_registry.yaml` to see available dataset IDs and their
   registered source paths.

2. Use the helper from your scripts:

```python
from geocanoe.registry import Registry

registry = Registry()

print(registry.list_ids())
print(registry.resolve_path("commodities"))
```

## External geospatial sources

`geospatial_sources.yaml` records the stable identity of every external
geospatial source acquired by the Bronze workflow. Each source declares a
Bronze root and named artifacts using either fixed paths or globs, allowing the
same contract to represent fixed files, rolling versioned releases,
jurisdictional collections, and companion metadata. Layer mappings and coded
domains are optional and are currently used by the Silver hydrography stage to
translate readable selections such as `lake`, `reservoir`, and `watercourse`
into the source database query.

`pipeline_impedance_sources.yaml` is a research-only cross-reference from
potential impedance roles to those canonical national source IDs. It is not an
active workflow registry and contains no routing weights. Its Ontario-only
entries are retained solely as historical fingerprints from the legacy
reference-model investigation.
