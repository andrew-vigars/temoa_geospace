# 8. Spatial gasoline demand

*Layer: Bronze → Silver → Gold. Code: `geocanoe.acquisition.gasoline_demand`,
`geocanoe.preprocessing.gasoline_demand` (Silver stages `D1`–`D3`), Gold `rebuild_demand`.
Controlled input: [registry/gasoline_supply_anchors.csv](../../../registry/gasoline_supply_anchors.csv).*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 8.1 Purpose and role in the model

The model's final demand is for gasoline (`d_gsl`), met by synthetic fuel chains. This chapter turns a
**provincial** statistic into **regional** demand on the grid. The result is one number per basemap cell
(tonnes per year) used by Gold as the `Demand` table.

The method is a proxy: provincial net gasoline sales are allocated to a small number of **market hubs**
(population centres), in proportion to the population served by each hub.

**[cite-method]** Allocating fuel demand to population-based proxies, rather than using retail stations or terminal
inventory, needs support from the literature on spatial disaggregation of fuel demand.

## 8.2 Sources

| Input | Catalogue profile |
|---|---|
| Net gasoline sales by province, table 23-10-0066-01 | [StatCan fuel sales and population centres](../data-sources/statcan-fuel-sales-and-population-centres.md) |
| 2021 population-centre boundaries | same profile |
| DA boundaries and population | [StatCan DA boundaries and population](../data-sources/statcan-da-population.md) |
| Supply-anchor registry (`map_inferred` or `text_named` evidence) | [Controlled inputs](../data-sources/controlled-inputs.md) |
| Gasoline density 0.00074 t/L | Build profile constant. **[cite-data]** |

## 8.3 Configuration (`[gasoline_demand]`)

| Parameter | Sample | Meaning |
|---|---|---|
| `sales_year` | 2024 | Fuel-sales year |
| `gasoline_density_t_per_litre` | 0.00074 | Conversion |
| `proxies.strategy` | `population_threshold` | Or `supply_anchors_plus_fill` |
| `proxies.minimum_population` | 100000 | 1,000, 30,000 or 100,000 (official population-centre classes) |
| `proxies.candidate_minimum_population` | 30000 | Candidate pool for the hybrid strategy |
| `proxies.empty_jurisdiction_policy` | `largest_population_centre` | Fallback when no hub qualifies |
| `proxies.anchor_registry` | `registry/gasoline_supply_anchors.csv` | Controlled anchors |
| `proxies.additional_hubs.<PROVINCE>` | none | Extra greedy hubs per jurisdiction |

## 8.4 Silver transformation

### D1. Hub selection (`run_gasoline_proxy_build`)

1. **Load** population centres in EPSG:3347, tag province from `PRUID`, hub ID from `PCPUID`, class from `PCCLASS`.
2. **Estimate centre population** (`estimate_centre_populations`): sum 2021 DA population for DAs whose
   interior point falls within the centre. This is for **ranking only**.
3. **Select hubs**, by strategy:
   - `population_threshold`: all centres with class ≥ the minimum (class thresholds: 1,000 → 2, 30,000 → 3,
     100,000 → 4). Any jurisdiction with none gets its largest centre (`jurisdiction_fallback`).
   - `supply_anchors_plus_fill`: start with the registry anchors in the province, falling back to the largest centre
     if there are none. Then add `additional_hubs[province]` centres greedily. Each addition is the candidate that most
     reduces the **population-weighted DA-to-nearest-hub distance** (`_weighted_distance_reduction`).
4. Hub location is the polygon's **representative point**.
5. **Assign DAs to hubs** (`assign_das_to_hubs`): each DA goes to the nearest hub **in the same jurisdiction**
   (`sjoin_nearest`), ties broken by hub ID. Every DA must map to exactly one hub (asserted).
6. **Catchments**: assigned population per hub.

### D2. Demand allocation (`allocate_demand`)

1. Load net gasoline sales for the configured year and provinces. Values must be in thousands of litres, so
   `litres = VALUE × 1,000`.
2. `allocation_weight = hub assigned population / province assigned population`.
3. `gasoline_litres = province litres × weight`. `demand (t/yr) = litres × 0.00074`.
4. Assertions: weights sum to 1 per province, and allocated litres equal source litres (conservation).

### D3. Mapping to the grid (`run_gasoline_basemap_build`)

1. **Region province** (`assign_basemap_region_provinces`): each cell takes the province containing its **centroid**
   (`intersects`).
2. **Hub to cell** (`map_proxy_demand_to_regions`): each hub goes to the cell **in its own province** that contains it, otherwise to the
   nearest same-province cell. Mapping method and distance are recorded.
3. **Aggregate** per cell: proxy count, assigned population, litres, tonnes. Cells without hubs get 0.
4. Assertions: proxy counts and demand are conserved by province.

Outputs: `data_files/processed/gasoline_demand/{build-id}/…` (hubs, DA crosswalk, catchments, demand, per-basemap
regional demand, manifests, previews).

## 8.5 Gold handling

Gold joins the regional demand one-to-one on region and writes one `Demand` row per region with demand > 0
(`d_gsl`, t/year, single model period). See [10-gold-spatial-mapping.md](11-gold-spatial-mapping.md).

## 8.6 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Provincial **sales** equal provincial **consumption**. | Cross-border and cross-province purchases, and fuel sold at depots for other uses, are ignored. **[cite-data]** |
| 2 | Demand within a catchment is concentrated at **one point** (the hub). | All of a catchment's demand appears in one cell, even if the population is spread over many. This strongly affects where fuel must be delivered and thus pipeline and truck flows. **[cite-method]** |
| 3 | Demand share is proportional to assigned population (2021 census), with no income, vehicle ownership or rurality effects. | **[cite-method]** (per-capita allocation) |
| 4 | DAs are assigned to the nearest hub by **straight-line** distance within the province. | Road network and geography are ignored. A DA near a provincial border is not served by a nearer hub across it. |
| 5 | Hub selection thresholds are the official 1,000 / 30,000 / 100,000 population-centre classes. | Sample uses 100,000, so only the largest centres host demand. |
| 6 | The anchor registry is partly **map-inferred**. | `map_inferred` rows should not be read as physical terminals. Labels are preserved downstream. **[cite-data]** |
| 7 | Population is 2021, fuel sales are for `sales_year`, and the model period is 2025–2050. | No demand growth, decline, or electrification is applied. The only allowed projection is constant. **[cite-method]** |
| 8 | Gasoline density is a single constant, 0.00074 t/L. | Typical-value assumption, about 740 kg/m³. **[cite-data]** |
| 9 | Province of a basemap cell is set by its centroid. | A border cell may be assigned to the "wrong" province for hub mapping. |

## 8.7 Validation

Weight-sum and conservation assertions at each step, the unique DA-to-hub check, per-province audit tables (including
nearest-fallback counts and maximum mapping distance). Gold validates region coverage (`validate_gasoline_demand_region_coverage`), and the input diagnostics gate checks that encoded demand equals the Silver regional
demand (`check_gasoline_demand_conservation`). Tests: `tests/test_gasoline_demand_silver.py`,
`tests/test_gasoline_demand_acquisition.py`.
