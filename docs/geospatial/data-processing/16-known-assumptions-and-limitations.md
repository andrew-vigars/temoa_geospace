# 16. Known assumptions and limitations

> **Draft status.** This chapter consolidates chapters 3–15 and the "Current limitations" list in the [README](../../../README.md), using the same five categories as the CANOE electricity
> documentation. The exhaustive per-line list is generated in [open items](../../open-items.md).

## 16.1 How to read the model's credibility

The pipeline is deterministic, internally checked ([14](14-diagnostics-and-analysis.md)), and its geospatial processing is explicit and auditable. Its main weakness is **parameter provenance**, not code correctness:
many economic and engineering numbers are inherited from an earlier model and are not yet sourced. Distinguish three categories when reporting results.

| Category | Examples | Status |
|---|---|---|
| **Sourced geospatial data** | Boundaries, roads, hydrography, population, facility emissions, storage, ERA5, land cover and protected areas | Public datasets with manifests and increasingly explicit immutable captures ([Part B](../data-sources/index.md)) |
| **Methodological choices** | Square grid, rook adjacency, straight-line edges, centroid retention, hub-based demand, evidence-presence storage | Defensible simplifications that need methodology citations |
| **Inherited parameters** | Impedance factors and density bands, legacy cost-curve constants, efficiencies and input splits, discount rate and placeholder CO₂ capture cost | `legacy_model_assumption_unvalidated` or undocumented ([controlled inputs](../data-sources/controlled-inputs.md)); renewable ATB selections and currency conversion have stronger provenance |

## 16.2 Limitations by category

### Spatial

| Limitation | Chapter |
|---|---|
| Regions are square cells retained by centroid, so coastal and border coverage is approximate and results depend on resolution and alignment | [4](04-basemap-and-graph.md) |
| Rook (4-neighbour) adjacency only; no diagonal links | [4](04-basemap-and-graph.md) |
| Pipelines and trucks move along straight centroid-to-centroid corridors; impedance and road eligibility are evaluated on that single line | [5](05-road-network.md), [6](06-pipeline-impedance.md) |
| Pipelines follow candidate corridors with impedance, not least-cost paths over a continuous surface | [6](06-pipeline-impedance.md) |
| Strong road connectivity depends on cell size: a single NRN segment must cross both cells | [5](05-road-network.md) |
| Storage mapped onshore only; any overlap makes a cell accessible | [7](07-co2-storage.md) |
| Demand is concentrated at population-centre hubs | [8](08-gasoline-demand.md) |
| Points snap to cells within 2× resolution (100 km at 50 km), with an interactive exclusion prompt | [11](11-gold-spatial-mapping.md) |
| Rail, marine, and existing gas pipeline or transmission infrastructure are not represented | README |
| Renewable siting uses categorical land exclusions and uniform deployment densities; no elevation, slope, offshore-depth, interconnection or parcel-level screen | [renewable workflow](../../renewables_silver.md) |

### Temporal

| Limitation | Chapter |
|---|---|
| One 25-year period and one annual representative year; hourly/monthly renewable profiles are diagnostic rather than solve constraints | [12](12-gold-technology-cost-scenario.md) |
| Renewable supply uses one weather year (2018), not a multi-year climatology | [renewable workflow](../../renewables_silver.md) |
| Emissions (2024), population (2021) and fuel sales (`sales_year`) held constant to 2050 | [8](08-gasoline-demand.md), [9](09-emissions-and-site-inputs.md) |
| Perfect foresight; no staged investment or path dependence | [13](13-temoa-execution.md) |
| Multi-period optimisation and uncertainty analysis are under development | README |

### Technical

| Limitation | Chapter |
|---|---|
| Transport efficiency is 1.0 on every edge (no losses or compression energy) | [11](11-gold-spatial-mapping.md) |
| Conversion efficiencies and input splits are fixed constants with unrecorded derivation | [12](12-gold-technology-cost-scenario.md) |
| Quantitative storage can use P50 equal-weighted regional caps or a shared pool, but neither is an injection-rate estimate; qualitative evidence remains uncapped and offshore storage is excluded | [7](07-co2-storage.md) |
| All emissions treated as capturable, with no capture-rate or process-type screening | [9](09-emissions-and-site-inputs.md) |
| Fractional "pipeline counts" are a post-processing interpretation of EOS capacity | [14](14-diagnostics-and-analysis.md) |
| Truck fleet capacity is unconstrained | [12](12-gold-technology-cost-scenario.md) |

### Economic

| Limitation | Chapter |
|---|---|
| One hydrogen-derived cost curve for H₂, CO₂, methanol and gasoline pipelines | [10](10-pipeline-cost-models.md) |
| Impedance factors (9.0, density bands) are inherited and unvalidated; Aboriginal Lands are a cost, not a legal constraint | [6](06-pipeline-impedance.md) |
| Cost constants (fixed investment, CO₂ capture cost, backup cost, EOS parameters) are inherited and mostly unsourced | [12](12-gold-technology-cost-scenario.md) |
| Renewable costs now use an auditable CAD2020 conversion and the H₂ workbook is an explicit pass-through, but inherited legacy node/truck/EOS costs remain incompletely sourced and labelled | [15](15-unit-conventions.md) |
| Truck investment is zero; truck cost representation is incomplete | [10](10-pipeline-cost-models.md), README |
| The named net-zero comparison stores a chosen fraction of included point-source emissions; it is not a whole-system net-zero constraint | [12](12-gold-technology-cost-scenario.md) |

### Data quality

| Limitation | Chapter |
|---|---|
| Rolling sources (NRN, NHN, Aboriginal Lands, fuel sales) can change between downloads; retrieval dates not yet recorded | [3](03-bronze-acquisition.md), [Part B](../data-sources/index.md) |
| Legacy site table (LCOE, `max_elec`) has undocumented origin and unlabelled electricity-potential units | [9](09-emissions-and-site-inputs.md) |
| The supply-anchor registry is partly map-inferred | [8](08-gasoline-demand.md) |
| Cost workbook sources not recorded | [10](10-pipeline-cost-models.md) |
| Generated Silver and Gold products are not committed even though multiple build profiles are | [2](02-configuration-and-setup.md) |
| Basemap, adjacency, road and snapping logic remain less directly tested than newer renewables, carbon-policy and provenance contracts | [4](04-basemap-and-graph.md), [5](05-road-network.md), [11](11-gold-spatial-mapping.md) |
| Temoa v4 consumed from a personal fork pending an upstream PR; Gurobi licence needed | [13](13-temoa-execution.md) |

## 16.3 Most consequential choices

Ordered roughly by how strongly they could change results.

1. **Straight-line corridors on a coarse grid.** Results depend on resolution and alignment. A resolution sensitivity study (for example 25 km vs 50 km) is the most direct test.
2. **One hydrogen cost curve for four pipeline commodities.**
3. **Demand concentrated at hubs.** Demand location drives where fuel must be delivered.
4. **Inherited impedance factors.**
5. **Road eligibility is binary and permissive.** The sample includes local roads, trucks have zero investment cost, and strong connectivity depends on cell size.
6. **Storage-capacity allocation.** Equal cell weighting and a shared pool are policy representations, not injectivity models.
7. **Single weather year and annual solve resolution.** Hourly renewable profiles are collapsed to annual capacity factors.
8. **Legacy unit and currency basis remains partly unverified** ([15](15-unit-conventions.md)).
9. **Constant 2024 emissions and 2021 population over 2025–2050.**
10. **Unattended-run reproducibility.** Point snapping prompts interactively when outliers exist.

## 16.4 Suggested sensitivity and validation work

| Question | Method |
|---|---|
| Does grid choice drive results? | Rerun baseline at 25 km and 50 km; compare corridors and costs |
| How much do impedance factors matter? | Compare `no_penalties` with `legacy_reference_v1` and the three `impedance_scope` options |
| Is the road assumption driving truck use? | Compare `backbone`, `primary_freight` and `freight_access`, weak and strong connectivity, with and without trucks |
| How sensitive is storage placement to eligibility? | `all_mapped` vs `quantitative` vs `qualitative` |
| How much unmet synthetic supply is there? | Report `GSL_BACKUP` activity in every run |
| Is the cost curve adequate? | Vary EOS segment count and spacing; report fit statistics |
