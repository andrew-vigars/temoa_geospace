# 4. Study area, basemap grid and region graph

*Layer: Silver. Code: `geocanoe.geospatial.basemaps`, `geocanoe.geospatial.adjacency`.
Silver orchestration: [execution/silver.py](../../../src/geocanoe/execution/silver.py) (stages `B1`, `G1`).*

> **Draft status.** Statements below are taken from the code as read on the
> `geo-dev` branch. Items marked **[confirm]** are behaviours inferred from code
> where the modelling rationale is not recorded in the repository.

## 4.1 Purpose and role in the model

This chapter produces the spatial skeleton of the model:

- **Basemap**: a regular square grid over the study area. Each retained cell is
  a Temoa *region* (`R0`, `R1`, …).
- **Graph**: the set of directed edges between neighbouring regions. Each edge is
  a candidate corridor on which pipeline, transmission and (if road-enabled)
  truck technologies can be built.

Every later Silver product (roads, impedance, storage, gasoline demand) is
mapped onto these regions or edges, and Gold encodes them as Temoa regions and
`region-region` pairs.

**[cite-method]** Representing a national energy system as a regular square grid of
regions, with neighbour links as transport corridors, needs support from spatially
explicit energy-system modelling literature (grid vs administrative vs
resource-based regionalization, and the effect of spatial resolution on results).

## 4.2 Sources

[Statistics Canada 2021 provincial and territorial boundaries](../data-sources/statcan-boundaries.md): a fixed 2021 release in EPSG:3347, the only external input to this chapter.

## 4.3 Configuration

Set in the `[study_area]`, `[basemaps]` and `[adjacency]` blocks of the build
profile (e.g. [sample_build_profile.toml](../../../config/build_profiles/sample_build_profile.toml)).

| Parameter | Sample value | Meaning |
|---|---|---|
| `study_area.provinces` | NL … BC (10 provinces) | Jurisdictions dissolved into the study area |
| `basemaps.keep_method` | `centroid` | Only supported retention rule |
| `basemaps.projected.resolutions_km` | `[50]` | Grid cell size (EPSG:3347) |
| `basemaps.geographic.resolutions` | `[]` | Degree grids (EPSG:4326), unused in sample |
| `basemaps.coordinate_precision` | 6 | Decimals for stored lon/lat |
| `adjacency.method` | `rook` | Only supported neighbourhood |
| `adjacency.no_neighbor_id` | `R-999` | Sentinel for "no neighbour" |

## 4.4 Silver transformation

### Basemap (`geocanoe.geospatial.basemaps`)

1. **Load boundaries** (`load_province_boundaries`). Read the shapefile, require a
   defined CRS, identify the English name column, map names to two-letter codes
   (`geocanoe.regions`), fail if any name is unmapped, reproject to WGS84.
2. **Build study area** (`build_study_area_boundary`). Select the configured
   codes, fail if any are missing, and dissolve them (`union_all`) into one
   polygon. Output: `{study_area}_boundary_wgs84.gpkg` and `_epsg3347.gpkg`.
3. **Align extent** (`align_bounds_to_grid`). Project the boundary to the grid CRS
   and snap its bounding box *outward* to multiples of the cell size
   (`floor`/`ceil`). The grid origin is therefore anchored to absolute
   coordinates, not to the study area, so grids of the same resolution from
   different study areas share cell edges.
4. **Generate candidate grid** (`build_candidate_grid`). Square cells of
   `resolution × native_scale` across the aligned box.
5. **Retain cells** (`retain_centroid_cells`). Keep a cell only if its centroid
   lies *within* the dissolved boundary (spatial join, `within`).
6. **Assign identifiers** (`add_coordinate_metadata`). Compute WGS84 `lon`/`lat`
   of centroids, sort by native `centroid_y` then `centroid_x`, and number cells
   `R0…Rn` in that order. `site_id` equals `region`.

Outputs: `data_files/processed/basemaps/{study_area}_basemap_{res}{km|deg}_centroid.gpkg`,
boundary files, previews and `basemap_summary.csv`.

### Graph (`geocanoe.geospatial.adjacency`)

1. **Validate basemap** (`validate_regions`).
2. **Neighbour lookup.** Build a dictionary from rounded native centroid
   coordinates to region ID. For each region, look up the cell at
   `±cell_size` in x and y (up, down, left, right). Missing neighbours get
   `no_neighbor_id`. Only the four cardinal (rook) neighbours are considered.
3. **Edge distance.** For every neighbour pair, compute the geodesic distance in
   km between the two centroids' WGS84 lat/lon (`geopy.distance.distance`).
4. **Directed edge table.** Emit one row per valid (region, direction), so each
   physical link appears twice, once per direction. Edge ID is
   `{region_from}-{region_to}`. Geometry is a straight `LineString` between native centroids.

Outputs: `data_files/processed/graph/{stem}_graph_nodes.{gpkg,csv}`,
`{stem}_graph_edges.{gpkg,csv}`, a summary CSV and a preview PNG.

## 4.5 Assumptions and limitations

| # | Assumption | Where | Consequence |
|---|---|---|---|
| 1 | A region is a square cell whose centroid lies inside the study-area polygon. | `retain_centroid_cells` | Coastal/border cells with centroids outside are dropped, so the grid under- or over-covers the true boundary. Land in dropped cells is unrepresented, and large cells can include non-study-area land. **[cite-gis]** (centroid-in-polygon vs area-majority vs any-intersection retention) |
| 2 | Grid origin is anchored to absolute EPSG:3347 coordinates. | `align_bounds_to_grid` | Region IDs and cell shapes are reproducible per resolution. Region IDs (`R{i}`) are ordered by position within the retained set, so they change when the study area changes. Do not compare IDs across profiles. |
| 3 | Only rook adjacency (4-neighbour). | `NEIGHBOR_MAP` | No diagonal links. Route length is Manhattan-like, and diagonal flows must be staircased. **[cite-method]** (grid-network adjacency and directional bias) |
| 4 | Any two adjacent retained cells are linked, regardless of physical barriers or jurisdiction. | `build_region_adjacency` | The graph may connect cells across water or between disjoint areas if they are adjacent. **[confirm]** whether any islands or disconnected components arise at the sample 50 km grid. |
| 5 | Edge length is the geodesic distance between centroid lat/lons, not the nominal cell size. | `build_region_adjacency` | Lengths are ≈ cell size but vary slightly with projection distortion. These lengths feed Gold's distance-dependent pipeline and truck costs. **[cite-gis]** (geodesic vs projected distance, EPSG:3347 definition) |
| 6 | Docstrings describe EPSG:3347 as an "equal-distance" grid. | `basemaps.py` header | EPSG:3347 is Statistics Canada Lambert, a conformal conic projection, not equidistant. Reword in public docs. |
| 7 | Cell size is a single scalar per build. | config | No variable-resolution or hierarchical grid. |

## 4.6 Outputs and downstream consumers

| Product | Consumed by |
|---|---|
| Basemap GeoPackage | Graph, road connectivity, impedance, storage, gasoline demand, Gold (regions, snapping) |
| Graph nodes/edges | Edge impedance, road connectivity, Gold (`canonical links`), analysis map |

## 4.7 Validation

`validate_regions` (graph stage) and the summary CSVs. The 50 km validation
an explicit Gold build and solver configuration can exercise any selected grid end to end; the former committed `provinces_validate_50km.toml` solver example was removed.
There are no dedicated unit tests for `basemaps.py` or `adjacency.py` in `tests/`. They are referenced only indirectly by the config and Silver-workflow tests. Adding tests for cell-ID determinism, neighbour counts and edge symmetry would document the grid contract.
