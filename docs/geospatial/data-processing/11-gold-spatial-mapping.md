# 11. Gold: spatial-to-model mapping

*Layer: Gold. Code: [schema/build.py](../../../src/geocanoe/schema/build.py)
(`build_canonical_links`, `attach_pipeline_cost_distances`, `snap_points_to_graph_nodes`,
`build_site_attributes`, `rebuild_demand`, `rebuild_capacity_limits`,
`rebuild_transport_efficiency`, storage eligibility helpers), plus
`geocanoe.preprocessing.legacy_inputs` (Silver, province assignment).*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).
> This chapter covers only the *spatial* half of Gold. Technology, cost and
> scenario parameterization is a separate chapter (see
> [01-architecture-and-temoa-boundary.md](01-overview-and-architecture.md), section 1.3).

## 11.1 Purpose and role in the model

Gold is where Silver spatial products stop being geometry and become Temoa
rows. Spatial information enters Temoa in only three ways:

1. **Nodes**: each graph region becomes a Temoa region (`R0`, `R1`, …).
2. **Edges**: each directed graph edge becomes a pseudo-region named
   `Rfrom-Rto`, on which transport technologies are defined.
3. **Node attributes**: demand, CO₂ availability, electricity potential and
   storage eligibility, aggregated to each node.

Temoa never sees polygons, roads or distances in GIS form. It sees region names
and numbers. This chapter documents how each of those numbers is derived from
Silver products.

## 11.2 Inputs

| Input | Silver product | Resolved by |
|---|---|---|
| Graph nodes | `processed/graph/*_graph_nodes.gpkg` | `schema.artifacts`, basemap stem selection |
| Graph edges | `processed/graph/*_graph_edges.csv` | same |
| Road-enabled edges | `processed/road_connectivity/*_road_edge_connections.csv`, `*_road_edges.gpkg` | `schema.road_connection_method` |
| Pipeline edge impedance | `processed/pipeline_impedance/{build-id}/edges/*_pipeline_impedance_edges.csv` | `[pipeline_costs] impedance_scope` |
| Regional gasoline demand | `processed/gasoline_demand/{build-id}/basemaps/*_gasoline_demand.gpkg` | basemap stem |
| Regional CO₂ storage evidence | `processed/co2_storage/*_co2_storage.gpkg` | basemap stem |
| Site table (LCOE, max electricity) | `processed/legacy_inputs/sites_full_with_province.csv` | province assignment (Silver) |
| Facility emissions | `processed/emissions/co2_large_facilities_2024/*_clean.gpkg` | study-area provinces |

The basemap is chosen by `interactive_basemap_selection` (prompt) or by
`basemap_stem` in the build profile.

## 11.3 Configuration

`[schema]` and related blocks in the build profile:

| Parameter | Sample | Meaning |
|---|---|---|
| `schema.road_connection_method` | `strong` | Which road-connectivity product defines truck edges |
| `schema.interactive_basemap_selection` | `true` | Prompt to choose a basemap |
| `schema.basemap_stem` | (commented) | Non-interactive basemap choice |
| `schema.max_snap_distance_factor` | `2.0` | Nearest-region snap limit as a multiple of grid resolution |
| `schema.boundary_buffer_km` | `50` | See assumption 9 |
| `schema.boundary_simplify_tolerance_km` | `5` | See assumption 9 |
| `storage.eligibility` | `all_mapped` | Which Silver storage flag enables `CO2_INJECT` |
| `storage.quantitative.capacity_mapping` | `unlimited` | Or `equal_weighted` / `shared`; capped mappings require quantitative eligibility |

Scenario/model files: `[pipeline_costs] impedance_scope` (`none`,
`eos_capex_only`, `all_km_dependent`), transport mode switches and individual
`disabled_technologies`, and
`registry/model.toml` for the time period.

## 11.4 Transformation

### A. Canonical topology (`build_canonical_links`)

1. **Node table.** Unique graph-node `region` values sorted numerically
   (`sort_region_ids`). The sentinel `R-999` must not appear.
2. **Pipeline and transmission links.** *Every* directed graph edge. Each gets
   `canoe_region = edge_region` (`"R12-R13"`).
3. **Cost distances** (`attach_pipeline_cost_distances`). Join Silver edge
   impedance, with these checks: the impedance table must cover the graph edges
   exactly, endpoints must agree, distances must be finite and positive, physical distance must
   match the graph, and `effective = physical × multiplier` must hold. The graph's
   `distance_km` is never modified. Three cost distances are produced:

   | `impedance_scope` | CAPEX distance | Fixed OPEX distance | Variable OPEX distance |
   |---|---|---|---|
   | `none` | physical | physical | physical |
   | `eos_capex_only` | **effective** | physical | physical |
   | `all_km_dependent` | **effective** | **effective** | **effective** |

4. **Truck links.** Directed edges with `has_road_connection = True` under the
   chosen road layer and method, de-duplicated by `edge_region`.
5. **Validation** (`validate_canonical_links`): unique IDs, endpoints in the node
   set, positive distances, `"-"` in every edge ID.

### B. Writing edges into Temoa tables (`rebuild_transport_efficiency` and cost rebuilds)

- Each transport technology is expanded over its link set: pipeline and
  transmission over **all** candidate edges, trucks over **road-enabled** edges only.
- Every row has `region = canoe_region`, `vintage = model_period`, and
  `efficiency = 1.0` (no transport losses are encoded).
- Existing transport rows are removed first so the table is rebuilt, not appended.
- Assertions confirm that the set of regions in each technology's rows equals the
  corresponding valid edge set.

### C. Point inputs to nodes (`snap_points_to_graph_nodes`)

Used for the legacy site table (LCOE, maximum electricity) and CO₂ facilities.
When renewable replacement is enabled, the legacy site table is intentionally
empty and the validated Silver polygons cover every selected node instead.

1. **Province filter** (`filter_to_configured_provinces`). Province values are
   normalized to two-letter codes. Unrecognized values are an error. Points outside the configured
   provinces are excluded and counted. Excluded CO₂ is reported in t CO₂e/year.
2. **Direct assignment.** Point-in-polygon (`within`) against node cells in the grid CRS.
3. **Nearest fallback.** Remaining points go to the nearest cell in EPSG:3347
   (`sjoin_nearest`), accepted only up to `max_snap_distance_factor × resolution`
   (100 km at 50 km resolution; geographic grids use 111 km/degree).
4. **Outliers.** Points beyond the limit are written to a
   `*_graph_snap_distance_audit.csv`, and the build **prompts at the console** to
   exclude them or stop.
5. **Aggregate per node:**

   | Quantity | Aggregation | Temoa table |
   |---|---|---|
   | CO₂ emissions | sum of facility `emissions_kt_co2e_per_year × 1000` (t/yr) | `LimitCapacity` for `CO2_CAP`, operator `le` |
   | Facility count | count | reporting only |
   | Maximum electricity | sum of `max_elec` | `LimitCapacity` for `ELC_GEN`, operator `le` |
   | LCOE | mean of `LCOE` | site attributes |

   Regions with no points receive 0 for every quantity.

### D. Demand and storage (not snapped, joined by region)

- **Gasoline demand.** Already allocated onto the basemap by Silver. Gold
  joins it one-to-one on `region` (`validate="one_to_one"`), and
  `rebuild_demand` writes one `Demand` row per region with demand > 0
  (commodity `d_gsl`, t/year, single model period).
- **CO₂ storage.** Silver gives evidence flags per region. Gold validates that the
  evidence covers *exactly* the graph nodes, then enables `CO2_INJECT` only in
  regions where the configured flag is true (`all_mapped` → `storage_accessible`;
  `quantitative` → `has_quantitative_storage_evidence`;
  `qualitative` → `has_qualitative_storage_evidence`). With `equal_weighted`,
  Gold sums each assessment's equal cell shares into regional annual-equivalent
  P50 upper bounds. With `shared`, it encodes one global annual-equivalent pool.
  `unlimited` leaves injection uncapped.

### E. Final coverage checks

`validate_encoded_region_coverage` and `validate_generalized_pipeline_cost_layer`
check that every encoded regional table refers only to valid node or edge
regions. `verify_exported_sqlite` re-opens the exported database.

## 11.5 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Each point is assigned to the single cell containing it, or to the nearest cell within 2× the grid resolution. | Points up to 100 km from any cell (50 km grid) are still assigned, so offshore or coastal facilities can be placed in a distant cell. Point-to-node assignment is resolution dependent. **[cite-gis]** **[cite-method]** (spatial aggregation / modifiable areal unit problem) |
| 2 | The snap limit is 2.0 × resolution. | The factor is a configuration default with no stated basis. **[cite-data]** |
| 3 | Outlier handling is interactive (`input()` prompt). | A build cannot run unattended if outliers exist, and the exclusion decision is not recorded in the configuration. Record the decision in the manifest. **[confirm]** |
| 4 | Electricity potential is **summed** per node, LCOE is **averaged** per node. | The mean LCOE is unweighted by capacity, so a node's LCOE need not reflect its potential. **[cite-method]** |
| 5 | CO₂ emissions are encoded as an upper bound on `CO2_CAP` capacity, held constant across the period. | Capture cannot exceed the facility baseline. No growth or decline is applied. The only supported projection method is `constant`. |
| 6 | Every graph edge is a candidate pipeline and transmission corridor. | There is no restriction by terrain, jurisdiction or existing infrastructure except through impedance. Existing gas pipelines and transmission lines are not represented (README limitations). |
| 7 | Transport efficiency is exactly 1.0 on every edge. | No compressor energy, pipeline leakage or transmission losses. **[cite-method]** |
| 8 | Each corridor is two directed Temoa exchange regions (`A-B`, `B-A`). Efficiency and operating-cost rows exist for both directions, but capacity is shared: capacity-driven EOS investment cost is attached to **one canonical orientation** per corridor (`canonicalize_capacity_cost_links`). | Installing capacity in either direction is paid for once. The Temoa convention `Ri-Rj` for exchange regions is native to Temoa (`temoa/components/geography.py`). Flow is directional, capacity is not. **[cite-method]** (Temoa documentation on inter-regional exchanges) |
| 9 | `boundary_buffer_km` and `boundary_simplify_tolerance_km` are parsed and printed by `project_config.py` but no other module reads them (confirmed by a search of `src/`). | The profile comment says points up to this distance outside the study area remain eligible, but eligibility is actually decided by the province filter and the snap-distance limit. The options appear vestigial. **[confirm]** whether to remove them or reimplement. **[stale]** |
| 10 | Province assignment for the legacy site table uses a hard-coded 50 km nearest-province limit (`MAX_NEAREST_PROVINCE_DISTANCE_KM`). | Sites more than 50 km from any province boundary get no province. **[cite-data]** |
| 11 | `co2_cost` is set to a constant 50 for every node. | Treated as a placeholder (code comment: "placeholder CO2 capture cost"). **[cite-data]** |
| 12 | The same graph serves all modes, with trucks restricted to road-enabled edges. | See [04-road-network.md](05-road-network.md) for how that set is defined. |
| 13 | Impedance is applied by scope. | `eos_capex_only` applies the multiplier to CAPEX only. Fixed and variable OPEX use physical distance. The committed baseline, net-zero and sample policy scenarios select `all_km_dependent`; report the resolved scope with results. |

## 11.6 Where the Temoa line falls for spatial information

| Step | Owner |
|---|---|
| Region geometry, adjacency, distances, impedance multipliers, road-enabled flags | Silver |
| Choosing which edges exist for each technology; mapping points to nodes; aggregating to node values; writing `Demand`, `LimitCapacity`, `Efficiency` rows | **Gold (pre-processing)** |
| Treating `R1-R2` as a place where flows can occur, and optimizing build and flow decisions | Temoa |

After Gold, no geometry is used by the solve. The analysis map re-reads the
graph and basemap separately to draw results.

## 11.7 Outputs

`data_files/processed/schema/gold_<build-id>_<scenario-id>_<fingerprint>_<barcode>.sqlite`
and the adjacent `.manifest.json` (resolved paths, implementation digest).
Snap audits are written to `data_files/processed/legacy_inputs/`.

## 11.8 Validation

Canonical-link assertions, exact-coverage checks for storage and gasoline
demand, one-to-one joins, the region-set assertions in the efficiency and
truck-cost rebuilds, final `validate_encoded_region_coverage`, and
`verify_exported_sqlite`. `tests/test_schema_build_contracts.py` and `tests/test_gold_temoa_contract.py` cover the schema builder and the installed Temoa schema contract. No test covers point snapping: the only snapping-related test is a config-parsing test for `max_snap_distance_factor`. Offshore points, ties and outlier exclusion are untested, and the interactive prompt makes them hard to test as written.
