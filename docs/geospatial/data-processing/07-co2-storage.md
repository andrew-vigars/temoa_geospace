# 7. Geological CO₂ storage evidence

*Layer: Bronze → Silver → Gold. Code: `geocanoe.acquisition.co2_storage`,
`geocanoe.geospatial.co2_storage` (Silver stage `S0`), Gold storage helpers in
[schema/build.py](../../../src/geocanoe/schema/build.py).*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 7.1 Purpose and role in the model

Decides **in which model regions CO₂ can be injected underground** and, for
quantitative evidence, whether P50 capacity is left unlimited, allocated equally
across intersecting cells, or represented as one shared system pool. In Temoa
this becomes `co2 → CO2_INJECT → co2_stored`, defined only in eligible regions.

## 7.2 Sources

[CanCO₂ unified storage release](../data-sources/canco2-storage.md): merges three evidence sources, identified by `source_dataset`.

| `source_dataset` | Role in GeoCANOE |
|---|---|
| `NATCARB` | Quantitative evidence |
| `BC_STORAGE_ATLAS` | Quantitative evidence |
| `ATLANTIC_COS` | Qualitative evidence |

## 7.3 Configuration

| Parameter | Sample | Meaning |
|---|---|---|
| `[storage] eligibility` | `all_mapped` | Which Silver flag enables injection: `all_mapped`, `quantitative`, `qualitative` |
| `[storage.quantitative] capacity_mapping` | `unlimited` | Or `equal_weighted` / `shared`; capped mappings require quantitative eligibility |
| `[storage.*] sources` | source-family defaults | Optional explicit source selection for the chosen eligibility class |
| Scenario `[storage] requirement` | `none` | Or `minimum_cumulative_activity` / `minimum_point_source_fraction` |
| Scenario target fields | 0 | Absolute tonnes over the model period or a fraction of included point-source emissions |

## 7.4 Silver transformation (`geocanoe.geospatial.co2_storage`)

1. **Load** storage features and assessments from the selected GeoPackage (`load_storage_source`), repairing
   geometry and keeping polygonal parts (`_polygonal_geometry`).
2. **Capacity-evidence flags** (`build_feature_capacity_flags`). For each of P10, P50, P90, theoretical and effective
   storage (tonnes), a flag is true when the assessed value is greater than zero. Assessments scoped to a feature
   apply directly, and unit-scoped assessments are propagated to the feature's storage unit. A feature
   gets `has_any_capacity_evidence` if any flag is true.
3. **Intersections** (`build_storage_region_intersections`). Overlay storage features with basemap cells in
   EPSG:3347, keep positive-area intersections only, and compute `region_overlap_fraction` and
   `feature_overlap_fraction`. Duplicate region-feature pairs are an error. The result is the
   **crosswalk** (many-to-many).
4. **Regional evidence** (`build_regional_storage_evidence`). Per region:
   - Union the storage geometry for each source dataset and compute its coverage fraction of the cell.
   - `has_natcarb`, `has_bc_storage_atlas`, `has_atlantic_cos`: coverage fraction > 0.
   - `has_quantitative_storage_evidence = has_natcarb OR has_bc_storage_atlas`.
   - `has_qualitative_storage_evidence = has_atlantic_cos`.
   - **`storage_accessible = quantitative OR qualitative`**.
   - Capacity-evidence flags aggregated with `any`, plus the unioned area and coverage fraction of features that
     carry positive capacity evidence.
5. **P50 capacity mapping.** For quantitative assessments with finite positive
   P50 values, `storage_capacity_mapping` records each assessment-region pair,
   its assessment total and its equal share across intersecting cells. This
   preserves assessment totals without treating polygon overlap as capacity.
6. **Validate** (`validate_storage_products`) and export the regional layer, crosswalk, capacity mapping and previews to
   `data_files/processed/co2_storage/`.

## 7.5 Gold handling

Covered in [spatial mapping](11-gold-spatial-mapping.md) and
[technologies, costs and scenarios](12-gold-technology-cost-scenario.md). Gold
requires exact node coverage, validates that selected sources match the Silver
build, enables `CO2_INJECT` only in eligible regions, optionally encodes P50
upper bounds, and optionally encodes a minimum injection target.

## 7.6 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | **Any** positive-area overlap makes a region storage-accessible under `all_mapped`. | A cell with a sliver of a storage polygon is treated like one fully underlain by it. The coverage fraction is computed but not used as a threshold. **[cite-method]** |
| 2 | Qualitative (Atlantic) evidence counts the same as quantitative evidence under `all_mapped`. | Regions with no quantified capacity can host unlimited injection. Use `quantitative` for a stricter reading. |
| 3 | `equal_weighted` divides each assessment's P50 total equally among intersecting cells; `shared` pools unique assessment totals system-wide. | These conserve reported storage quantities but are allocation policies, not spatial capacity estimates or injection-rate constraints. `unlimited` remains available. |
| 4 | Only the **onshore** basemap domain is mapped. | Offshore storage is excluded. The code says offshore requires an offshore model-region product. |
| 5 | Evidence is geometric (does a polygon overlap the cell), with no injectivity, depth, seal, or distance-to-source screening. | The `injectivity_status` attribute is carried in the crosswalk but not used for eligibility. **[confirm]** |
| 6 | Overlay is computed in EPSG:3347 planar area. | **[cite-gis]** |
| 7 | The storage release is an external, versioned input. | See chapter 3 (assumption 4). |

## 7.7 Outputs and consumers

| Product | Path | Consumer |
|---|---|---|
| Regional storage evidence | `data_files/processed/co2_storage/*_co2_storage.gpkg` | Gold (`CO2_INJECT` eligibility) |
| Feature-region crosswalk | same directory | Audit and mapping |
| P50 assessment-region mapping | same GeoPackage plus `*_storage_capacity_mapping.csv` | Optional Gold upper bounds |
| Source and mapping previews | same directory | Visual QA |

## 7.8 Validation

`validate_basemap`, `validate_storage_products`, the duplicate-pair check, and the Gold requirement of exact
node coverage (`validate_storage_region_coverage`). Tests: `tests/test_co2_storage.py`.
