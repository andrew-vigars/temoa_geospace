# 5. Road network and road connectivity

*Layer: Bronze → Silver → (consumed in Gold). Code: `geocanoe.acquisition.nrn`,
`geocanoe.geospatial.roads`, `geocanoe.geospatial.road_connectivity`.
Silver stages `N1` and `M1`.*

> **Draft status.** Taken from code on `geo-dev`. Items marked **[confirm]**
> need a decision or rationale from the author.

## 5.1 Purpose and role in the model

Roads decide **where truck technologies are allowed**. Pipelines and
electricity transmission can use any graph edge, but truck links are restricted
to edges classified as *road-enabled*. The road network itself never enters
Temoa. Only the resulting set of enabled edges does.

## 5.2 Sources

[Statistics Canada National Road Network](../data-sources/statcan-nrn.md): English GeoPackages per jurisdiction, a rolling release. The NRN edition used is not pinned in code, so record it when citing a build (`[cite-data]` in the profile).

## 5.3 Configuration

`[roads]`, `[roads.classes]` and `[road_connectivity]` in the build profile.

| Parameter | Sample value |
|---|---|
| `roads.networks` | `backbone`, `primary_freight`, `freight_access` |
| `roads.output_crs` | `EPSG:4326` |
| `roads.export_individual_provinces` | `false` |
| `road_connectivity.road_layer` | `freight_access` |
| `road_connectivity.methods` | `["strong"]` (`weak` available) |
| `schema.road_connection_method` | `strong` |

Nested road-class definitions (NRN `ROADCLASS` values):

| Network | Classes |
|---|---|
| `backbone` | Freeway, Expressway / Highway, Ramp |
| `primary_freight` | backbone + Arterial |
| `freight_access` | primary_freight + Collector, Local / Street, Local / Unknown |

## 5.4 Silver transformation

### Road networks (`geocanoe.geospatial.roads`)

1. **Locate source** (`get_nrn_gpkg_path`). Require exactly one `*_en.gpkg` per
   selected jurisdiction.
2. **Find the segment layer** (`get_roadseg_table`). The single table ending
   `_ROADSEG`.
3. **Count source classes** (`get_roadclass_counts`) for audit.
4. **Filter** (`load_filtered_provincial_networks`). Read the broadest network
   (`freight_access`) from each GeoPackage, then derive the narrower networks by
   `ROADCLASS` subset, giving `backbone ⊆ primary_freight ⊆ freight_access`.
   `freight_access` must be enabled. A jurisdiction may legitimately have an
   empty narrow network, e.g. Nunavut. All GeoPackages must share one CRS.
5. **Merge and deduplicate** (`build_study_area_networks`,
   `deduplicate_boundary_segments`). Concatenate jurisdictions. Segments with an
   *identical geometry (WKT)* are collapsed to one row, preferring the class that
   appears earliest in that network's class list.
6. **Reproject** to `roads.output_crs` (EPSG:4326) and export
   `{study_area}_filtered_road_networks.gpkg` plus a summary CSV.

### Road → graph connectivity (`geocanoe.geospatial.road_connectivity`)

1. **Load** the chosen `road_layer`, the graph nodes/edges and the boundary.
   Reproject roads to the graph CRS. Validate inputs (`validate_graph_inputs`).
2. **Overlay** (`build_road_region_overlay`). Spatial join of road segments to
   cell polygons with predicate `intersects`. A segment crossing several cells
   appears once per cell.
3. **Region road presence** (`build_region_road_presence`). Unique segment count
   per region, and `has_road = count > 0`.
4. **Edge classification.** For each directed graph edge:
   - **Weak** (`build_weak_road_connections`): `has_road_connection` if *both*
     endpoint regions contain any road segment.
   - **Strong** (`build_strong_road_connections`): `has_road_connection` if the
     two endpoint regions share at least one *same* `road_id`, i.e. one NRN
     segment intersects both cells.
5. **Export** (`export_road_connectivity_outputs`): per-method region presence,
   edge connection tables, road-enabled edge geometries, optional diagnostic
   PNGs, and a summary CSV.

## 5.5 Handoff to Gold

[schema/build.py](../../../src/geocanoe/schema/build.py) loads
`*_road_edge_connections.csv` and `*_road_edges.gpkg` for the method named in
`schema.road_connection_method`, and builds `valid_road_edge_regions` (around
line 1951). Truck efficiency, variable-cost and investment rows are written
only for those edges, and assertions check that they match this set.

Nothing about road *class, length or quality* is carried into Temoa. A road-enabled edge is
binary. **[confirm]** that truck distance cost uses the graph edge length
(centroid-to-centroid) rather than road length.

## 5.6 Assumptions and limitations

| # | Assumption | Where | Consequence |
|---|---|---|---|
| 1 | Nested class hierarchy, with `freight_access` as the sample connectivity layer. **[cite-data]** (NRN `ROADCLASS` definitions in the product specification) | `[roads.classes]` | Includes Local/Street and Local/Unknown roads, so truck access is very permissive: nearly any populated cell has a road. Trucks do not distinguish highway from local road capacity or cost. |
| 2 | Only the English NRN is used. | `get_nrn_gpkg_path` | Attributes assumed English-named (`ROADCLASS` values). |
| 3 | Duplicate roads are identified by exact geometry only. | `deduplicate_boundary_segments` | Near-duplicates at jurisdiction borders with different vertices survive and can inflate shared-segment counts. |
| 4 | Strong connectivity needs one NRN segment to intersect both cells. **[cite-gis]** (overlay/`intersects` predicate semantics; NRN segmentation rules) | `build_strong_road_connections` | NRN segments are split at intersections, so a short segment rarely spans two cells. On fine grids, strong connectivity can understate connections that real multi-segment routes provide. The result is sensitive to cell size. **[confirm]** by comparing weak vs strong edge shares at 25 km and 50 km. |
| 5 | Weak connectivity treats any road in both cells as a link. | `build_weak_road_connections` | Overstates connectivity because the two roads need not connect. Available but disabled in the sample. |
| 6 | Connectivity is evaluated on the same graph used for pipelines. | design | Truck links follow the rook grid, not actual road geometry. |
| 7 | Roads are not used to route or cost, only to enable. | Gold | Road quality, terrain, seasonal/winter roads and ferries are not represented. **[cite-method]** (road-network representation in freight and hydrogen/CO₂ logistics models) |
| 8 | Both edge directions are classified identically. | `edge classification` | Road links are symmetric. |

## 5.7 Outputs and consumers

| Product | Path | Consumer |
|---|---|---|
| Filtered road networks | `data_files/processed/nrn/` | `road_connectivity` |
| Edge connections, road edges | `data_files/processed/road_connectivity/` | Gold (truck links), analysis map (context) |

## 5.8 Validation

`validate_road_network`, `validate_graph_inputs`, the per-class source counts
and connectivity summary CSV, and diagnostic PNGs (`plot_outputs = true`).

## 5.9 Tests

No dedicated unit tests exist for `roads.py` or `road_connectivity.py`. Adding fixtures for weak vs strong connectivity on a small synthetic grid would pin down assumption 4 above.
