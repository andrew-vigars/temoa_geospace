# 9. Emissions facilities and legacy site inputs

*Layer: Bronze → Silver → Gold. Code: `geocanoe.acquisition.emissions`, `geocanoe.emissions.facilities`
(Silver stage `E1`), `geocanoe.preprocessing.legacy_inputs` (Silver stage `L1`). Gold: `build_site_attributes`,
`rebuild_capacity_limits`, `rebuild_node_costs`.*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 9.1 Purpose and role in the model

Two point datasets give the model its supply side:

1. **Large-facility CO₂ emissions** set how much CO₂ is available for capture in each region.
2. **A legacy site table** gives a cost (LCOE) and a maximum electricity potential for each location, which become
   electricity generation cost and a capacity ceiling in each region.

Both are point data snapped to grid cells in Gold (chapter 11).

## 9.2 Sources

| Dataset | Catalogue profile |
|---|---|
| Large-facility emissions, 2024 | [ECCC large-facility GHG](../data-sources/eccc-large-facility-ghg.md) |
| `data_files/sites_full.csv` (LCOE, `max_elec`) | [Controlled inputs](../data-sources/controlled-inputs.md): not in the source registry, origin undocumented |

The site table spans lat 41.6 to 83.2 and lon −141.3 to −52.5, i.e. all of Canada including areas north of the provinces in the sample study area, and
has about 26,500 rows with values (LCOE median 0.0385, `max_elec` median about 300,000). See [15](15-unit-conventions.md) for the unit question.

## 9.3 Silver transformation

### Emissions (`geocanoe.emissions.facilities`)

1. **Validate source files** and load the CSV (attributes) and GeoJSON.
2. **Column audit** (`build_column_audit`) and **standardize** (`standardize_emissions_table`): rename to canonical
   columns, coerce numerics, and set `is_spatially_assignable` when latitude and longitude lie inside a broad
   Canada box (lat 40–85, lon −145 to −45) and neither is zero.
3. **Validate** (`validate_standardized_table`): reports missing IDs, coordinates and emissions, duplicates, zeros,
   units and reporting years. **Negative emissions are a hard error.**
4. **Spatial layer** (`build_spatial_emissions`): assignable facilities as WGS84 points.
5. Export clean CSV, GeoPackage, metadata and a column audit to `data_files/processed/emissions/co2_large_facilities_2024/`.

### Legacy sites (`geocanoe.preprocessing.legacy_inputs`)

1. Load province polygons (the same StatCan boundary file) and validate coordinates.
2. **Province assignment** (`assign_provinces`): direct point-in-polygon (`within`), then for unmatched points the
   nearest province in EPSG:3347 within **50 km** (`MAX_NEAREST_PROVINCE_DISTANCE_KM`). Method and distance are
   recorded. Points still unresolved go to an audit table.
3. Write `sites_full_with_province.csv` and an assignment audit to `data_files/processed/legacy_inputs/`.

This is run once so routine schema builds avoid repeatedly dissolving or buffering the coastline.

## 9.4 What Gold does with them

See [10-gold-spatial-mapping.md](11-gold-spatial-mapping.md) section 11.4 C. In summary, by node:

| Quantity | Rule | Temoa encoding |
|---|---|---|
| CO₂ available | Sum of facility emissions (kt → t, positive only) | `LimitCapacity`, `CO2_CAP`, `≤`, t CO₂e/year |
| Electricity potential | Sum of `max_elec` | `LimitCapacity`, `ELC_GEN`, `≤` |
| Electricity cost | **Mean** `LCOE` | `CostVariable`, `ELC_GEN`, units written as "M$/MWh" |
| CO₂ capture cost | Constant 50 | `CostVariable`, `CO2_CAP`, units "M$/t" |

## 9.5 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | The 2024 emissions snapshot is held **constant** over the model period (only supported projection method). | No decarbonisation or closure of facilities. **[cite-method]** |
| 2 | **All** reported facility emissions are treated as capturable, as an upper bound on `CO2_CAP` capacity. | No capture-rate, purity, or process-type screening (e.g. combustion vs process streams). **[cite-method]** |
| 3 | Zero-emission facilities are kept in Silver but removed in Gold. Facilities with missing or out-of-range coordinates are not assignable and are **excluded**. | Their emissions never enter the model. The validation step reports the excluded total. |
| 4 | The Canada bounding box (lat 40–85, lon −145 to −45) is a coarse filter. | It does not detect points that are in Canada's box but misplaced. **[cite-gis]** |
| 5 | Legacy sites with no province within 50 km are dropped. | Offshore or far-coast sites are lost. **[cite-data]** |
| 6 | Mean LCOE per node is unweighted. | See chapter 11, assumption 4. |
| 7 | CO₂ capture cost is a flat 50 for every node (labelled a placeholder in code). | Capture cost does not vary with source type or concentration. **[cite-data]** |
| 8 | Cost units are written as "M$/MWh" and "M$/t" without conversion, while the pipeline costs are in CAD 2020. The Temoa configuration sets `check_units = false`. | Unit consistency across technologies is not enforced by the solver. **[confirm]** the intended currency and unit basis. |
| 9 | The site table is a legacy product with undocumented derivation. It covers all of Canada (lat 41.6 to 83.2), not only the study area. | Rows outside the configured provinces are dropped in Gold and counted. Resource assumptions, year and units of `max_elec` are unknown. **[confirm]** |

## 9.6 Validation

Silver: the validation summary, column audit and province-assignment audit. Gold: snap-distance audit, province
filter counts, and the diagnostics gate (chapter 14).
