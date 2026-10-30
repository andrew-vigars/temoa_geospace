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
  overlaid by one `registry/scenarios/*.toml` file (e.g. `baseline.toml` or
  `net_zero.toml`). Controls non-spatial policy: the
  optimization time horizon, discounting/financing rates, the emissions
  projection method, the geological CO2 storage requirement, and master
  switches for road/truck and pipeline transport representations. A scenario
  file only declares the settings it overrides — everything else is
  inherited from `model.toml` — and never touches the Silver evidence
  layers. Consumed via `geocanoe.config.load_model_config`.

A single Gold artifact is the product of exactly one build profile and one
scenario overlay: `gold_<build-id>_<scenario-id>_<fingerprint>.sqlite`. To add
a new scenario set (e.g. "net-zero 2050"), copy
  `registry/scenarios/baseline.toml`, give the local copy a unique `[scenario].id`,
and override only the sections that should differ. Supported override sections are (`[time]`, `[finance]`,
  `[emissions]`, `[storage]`, `[basemap]`, `[conventional_gasoline]`, `[synthetic_gasoline]`,
  `[transport_modes]`, and `[pipeline_costs]`).

The committed scenario endpoints are `baseline.toml` (optional CO2 storage)
and `net_zero.toml` (an explicitly selected carbon-policy case). A net-zero or
carbon-policy scenario needs a positive carbon storage target OR a positive
atmospheric emissions price. Neither is selected by the global defaults.
The current comparison uses an arbitrary 75% point-source storage target;
75% is not a default or a recommended target. Choose a target or price for the
study. High carbon prices or storage targets near 100% can result in long model
solve times, and full point-source storage can conflict with other carbon uses.
Both endpoint files
explicitly enable roads and pipelines and provide starting points for sweeps.
Copy either endpoint to a new scenario file and adjust the desired settings.
All other scenario overlays, including samples, capacity and no-truck variants,
are local and ignored by Git. Individual TEMOA configs, matrix recipes and all
batch selections (including archived runs) are also local. Tests construct their
own temporary scenario inputs rather than depending on these mutable run files.

The tracked [carbon sweep guide](scenarios/carbon_sweeps/README.md) describes 24
local overlays: six storage targets and six emissions prices, each with and
without trucks. Every overlay enables facility carbon accounting and five CO2
EOS curves. The samples also demonstrate carbon accounting and five CO2 curves;
A local `sample_scenario.toml` may select a 50 km validation basemap. Historical canonical
endpoints are separate from the new sweep set.

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

### Commodity table versions

`commodities.csv` is the canonical active table, promoted from the tested v2
commodity definitions. `bronze_registry.yaml` selects this standard filename;
the Gold builder resolves that dataset rather than choosing a versioned
filename itself. The artifact fingerprint includes the selected path and
contents, so changes to the definitions receive a new artifact identity.

The original v1 table is preserved in `../data_files/old_files/commodities_v1.csv`.
Its carbon-accounting comparison companion is preserved alongside it as
`../data_files/old_files/commodities_v1_carbon.csv`. Neither legacy file is selected
by the active registry. Completed comparison runs retain their original input
snapshots and provenance.

The canonical table uses `a` for hydrogen, methanol, gasoline, electricity, captured CO2, and
raw facility carbon: annual supply must equal annual use. Only the terminal
outputs `co2_stored` and `co2_released` use `wa`, allowing production without
a downstream consumer. Atmospheric release remains explicit and is charged
through the separate `e` commodities `co2_facility_emission` and
`co2_fallback_emission`. `d_gsl` remains demand (`d`); `ethos` remains source
(`s`). Blank units retain the existing unspecified units for legacy entries.

The archived v1 carbon companion preserves the original energy-carrier waste
flags and adds the same policy-scoped carbon-accounting entries as the canonical
table. It remains available for reproducing historical flag comparisons.

Commodity definitions are copied from the selected CSV. Its `policy` column
selects either `all` scenarios or only `emissions_price`; this registry-only
column is removed before SQLite export. The four carbon-accounting entries
are included only for `emissions_price`, because Temoa rejects unused physical
carriers. Processes, emissions coefficients, and prices also remain conditional
on the emissions policy. The carbon encoder validates required commodity
roles and reports missing or incompatible definitions; it neither inserts
commodity definitions nor overrides flags. Process relationships still refer
to commodity identifiers in code. Derived Temoa commodity sets do not require
CSV rows. Existing Gold databases must be rebuilt to adopt this version.

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
