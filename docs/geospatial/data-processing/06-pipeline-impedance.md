# 6. Pipeline-routing impedance

*Layer: Silver, consumed in Gold. Code: `geocanoe.geospatial.aboriginal_lands`,
`population_impedance`, `nhn_impedance`, `hydrography`, `pipeline_impedance`,
`pipeline_edge_impedance`. Silver stages `H1`, `H2`, `A0`, `P0`, `I1`.
Penalty assumptions: [registry/pipeline_impedance_penalties.yaml](../../../registry/pipeline_impedance_penalties.yaml).*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 6.1 Purpose and role in the model

Pipelines in the model do not follow a routed least-cost path. A pipeline uses
the straight line between two neighbouring region centroids (chapter 4). This
chapter adds a **cost-distance multiplier** to each of those edges, so that
building through lakes, Aboriginal Lands or dense population centres is more
expensive than building through open land. The multiplier is applied in Gold to
pipeline costs. The physical graph distance is never altered.

**[cite-method]** Representing routing difficulty as an additive equivalent-distance
penalty on a fixed candidate edge, rather than through least-cost-path analysis over a
cost surface, is a modelling simplification. Candidate references: least-cost
corridor and pipeline-siting literature, and GIS-based cost-surface methods.

## 6.2 Sources

| Layer | Catalogue profile |
|---|---|
| Aboriginal Lands boundaries | [NRCan Aboriginal Lands](../data-sources/nrcan-aboriginal-lands.md) |
| Waterbodies | [NRCan National Hydro Network](../data-sources/nrcan-nhn.md) |
| Population density | [StatCan DA boundaries and population](../data-sources/statcan-da-population.md) |
| Population-centre mask | [StatCan fuel sales and population centres](../data-sources/statcan-fuel-sales-and-population-centres.md) |
| Penalty values | [Impedance penalty profiles](../data-sources/controlled-inputs.md) (controlled input; inherited and unvalidated) |

## 6.3 Configuration

Build profile `[pipeline_impedance]` and `[hydrography]`:

| Parameter | Sample | Meaning |
|---|---|---|
| `pipeline_impedance.enabled` | `true` | Master switch |
| `penalty_registry` | `registry/pipeline_impedance_penalties.yaml` | Penalty assumptions file |
| `penalty_profile` | `legacy_reference_v1` | Named assumption set (`no_penalties` is the control) |
| `output_crs` | `EPSG:3347` | Projected CRS for lengths and areas |
| `hydrography.polygons.classes` | `["lake"]` | NHN classes used |
| `hydrography.polygons.minimum_source_area_km2.lake` | `50.0` | Minimum lake area |
| `hydrography.permanency` | unknown, permanent, intermittent | Permanency classes retained |
| `hydrography.lines.enabled` | `false` | Watercourse lines not used |

Scenario level (`registry/scenarios/*.toml`, `[pipeline_costs]`): `impedance_scope`
selects whether the multiplier applies to CAPEX only or to all
kilometre-dependent pipeline cost tables. Gold rejects a non-`none` scope if
`pipeline_impedance.enabled` is false (schema/build.py, around line 750).

## 6.4 Silver transformation

### Penalty profile `legacy_reference_v1`

| Layer | Method | Value |
|---|---|---|
| Aboriginal Lands | additional-distance factor | 9.0 |
| NHN waterbodies | additional-distance factor | 9.0 |
| Population exposure | population-density band factor | 0.11 / 0.43 / 0.82 for density bands |

Density bands (persons/km²): `<157` → 0.0; `157–703` → 0.11; `703–1199` → 0.43; `>1199` → 0.82.

A factor of 9.0 means one kilometre through the layer adds nine equivalent
kilometres, so ten effective kilometres in total.

All of these values are labelled `legacy_model_assumption_unvalidated` in the
registry, i.e. inherited from the earlier `Hydrogen_Project_Final` model.
**[cite-data]** Replace or support the 9.0 factors and the 157/703/1199 band
thresholds and 0.11/0.43/0.82 factors with literature or consultation-based sources.

### Evidence layers

**Aboriginal Lands** (`normalize_aboriginal_lands`)
1. Require projected `output_crs` so lengths and areas are metric. **[cite-gis]**
2. Keep source features whose legislative jurisdiction fields (`JUR1`–`JUR4`)
   include a study-area jurisdiction. Complete polygons are kept, not clipped to
   the census boundary.
3. Reproject, repair invalid geometries (`make_valid`), explode multipart geometries, keep polygons.
4. Dissolve overlapping polygons into non-overlapping components
   (`dissolve_area_overlaps`) so overlap is not double counted.
5. Write `applied_scalar` (configured value if the penalty is enabled, else 0).

**NHN waterbodies** (`hydrography` then `nhn_impedance`)
1. Hydrography stage selects the configured NHN classes (sample: lakes) and permanency, keeping
   features above the area threshold (sample: 50 km² per lake). Thresholds apply to the complete
   source geometry *before* clipping to the study area.
2. Impedance stage validates source ID and study area, reprojects, repairs geometry, dissolves
   overlaps, and writes `applied_scalar`.

**Population exposure** (`build_population_exposure`)
1. Join 2021 DA polygons to the population table on `DGUID` (one-to-one). Missing rows are an error;
   missing density is set to 0.
2. Intersect DA polygons with population-centre polygons, so density is applied only *inside*
   population centres. Rural DAs get no penalty regardless of density.
3. Drop intersections where DA province ≠ population-centre province.
4. Classify density into bands (`classify_population_density`) and assign `applied_factor`.

### Edge aggregation (`pipeline_edge_impedance`)

1. Require all three evidence GeoPackages for the build.
2. Treat each **undirected** pair once. The directed edges `A-B` and `B-A` share one result.
3. For each evidence layer, intersect the straight edge `LineString` with the layer
   (`geopandas.overlay`) and sum intersected length in km. Constant-factor layers are
   dissolved first so overlap within a layer is not double counted. Feature-specific factors
   (population bands) are additive.
4. Compute for each edge:

   ```text
   layer_penalty_km        = layer_intersection_km × layer_factor
   total_penalty_km        = Σ layer_penalty_km
   effective_distance_km   = physical_distance_km + total_penalty_km
   cost_distance_multiplier = effective_distance_km / physical_distance_km
   ```
5. Write `edges/{basemap_stem}_pipeline_impedance_edges.csv` (read by Gold) and previews.

Gold sets the multiplier to 1.0 for every edge when `impedance_scope = "none"`.

## 6.5 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Penalties are additive equivalent distance, summed across layers. | A segment crossing both a lake and Aboriginal Lands accrues both penalties (up to +18 km per km). **[cite-method]** |
| 2 | The edge geometry is the straight centroid-to-centroid line. | Penalties depend on whether that one line crosses a feature, not on the best route through the cell. A nearby feature missed by the line has no effect. The multiplier is sensitive to grid resolution and alignment. **[cite-method]** |
| 3 | Penalty values are inherited and unvalidated (9.0, 9.0, density bands). | Results scale directly with these numbers. Run sensitivity cases using `no_penalties` and alternative profiles. **[cite-data]** |
| 4 | Aboriginal Lands are penalized, not excluded. | The model may route through them at a cost. The penalty is not a consultation or legal constraint and should not be presented as one. Wording needs author and stakeholder review. **[confirm]** |
| 5 | Only lakes at or above 50 km² are used in the sample. | Smaller lakes, rivers and wetlands have no effect. Watercourse crossings need a separate width/crossing model (stated in the registry). **[cite-data]** |
| 6 | Population density applies only within 2021 population centres. | Density is based on 2021 DA area/counts, with no projection to 2050. **[cite-data]** |
| 7 | Intersection lengths are computed in EPSG:3347 planar units. | Small distortion relative to geodesic length. **[cite-gis]** |
| 8 | Invalid geometries are repaired with `make_valid` without logging counts. | Repairs can slightly change area. **[confirm]** whether repair counts are reported. **[cite-gis]** |
| 9 | One multiplier per undirected pair applies to both directions. | Impedance is symmetric. |

## 6.6 Repository text out of date

[registry/pipeline_impedance_sources.yaml](../../../registry/pipeline_impedance_sources.yaml)
states `status: research_only_not_consumed` and says impedance is "not implemented"
for each source. The code, `model_workflow.md` and the penalties file show that
impedance is implemented and consumed by Gold. **[stale]**

## 6.7 Outputs and consumers

| Product | Path | Consumer |
|---|---|---|
| Evidence polygons | `data_files/processed/pipeline_impedance/{build-id}/evidence/*.gpkg` | Edge aggregation |
| Edge multipliers | `data_files/processed/pipeline_impedance/{build-id}/edges/{basemap_stem}_pipeline_impedance_edges.csv` | Gold (`cost_distance_multiplier`) |

## 6.8 Validation

Required-field and CRS checks in each normalizer, source-identity checks for NHN, one-to-one DA join
validation, unique-edge-ID checks, and a final check that every directed edge has a result.
Audit columns (`*_intersection_km`, `*_penalty_km`) let any edge be recomputed by hand.
