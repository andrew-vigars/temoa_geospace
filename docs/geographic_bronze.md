# Shared geographic Bronze sources

`registry/geospatial_sources.yaml` is the canonical source inventory. New shared
geographic acquisitions use `data_files/raw/geography/<theme>/<release>/`.
Existing NHN, NRN, boundary and population folders retain their current paths;
moving those requires coordinated migration of their consumers. Source data
can support renewable siting and pipeline costs with different downstream rules.

## NRCan 2020 land cover

The user reviewed and supplied the official catalogue, CKAN JSON and direct
GeoTIFF URLs, now recorded in the shared registry. Publisher: NRCan CCRS/CCMEO;
creator: Dr. Rasim Latifovic; licence: Open Government Licence – Canada.
Record released 2022-08-11, modified 2025-09-23. These catalogue dates are not
the observation year or a guarantee of immutable TIFF bytes.

Run only this source:

```powershell
python scripts/build_bronze.py --stages land_cover
# Standalone, with preview control:
python -m geocanoe.acquisition.land_cover --no-preview
```

The stage preserves `landcover-2020-classification.tif`, the original CKAN
response `catalogue.json`, and `acquisition_manifest.json` under
`data_files/raw/geography/land_cover/2020/`. The manifest records source URLs,
file sizes and SHA-256 hashes, observed CRS, dimensions, transform, resolution,
NoData, tags, overviews and embedded colour table. Catalogue identity and its
TIFF resource URL are checked before acquisition. The shared downloader uses
partial files and resumes interrupted transfers. A completed capture is checked
locally on subsequent runs, without re-querying the catalogue. A mismatch fails;
refresh into a new directory using `--output-dir`, rather than overwrite history.
This stage intentionally ignores the general Bronze `--overwrite` flag.

Initial capture: 2,107,707,760 bytes; EPSG:3979; 190,001 columns by 160,001 rows;
30 m pixels; one uint8 band; NoData 0. Native raster bytes are not reprojected.

`preview/land_cover_2020.png` is a bounded national quicklook using embedded
colours and numeric source codes. Reading may use source overviews. Its manifest
records sampling dimensions and source hash. Sampled codes are neither a full
class inventory nor area statistics. Semantic labels require an official legend
review; no exclusion policy is inferred from colour or code. Preview generation
does not edit the source raster. This stage performs no eligibility, MW-capacity,
cost-raster or Atlite/Silver integration.

Next sources are reviewed one at a time with the user before acquisition.

## CPCAD 2025 protected and conserved areas

The user supplied ECCC's database directory and direct `ProtectedConservedArea_2025.zip`
download, listed 2026-03-25. The release represents December 2025, distinct from
the distribution listing date. Source URLs are recorded under `eccc_cpcad_2025`.

```powershell
python scripts/build_bronze.py --stages protected_areas
python -m geocanoe.acquisition.protected_areas --no-preview
```

Outputs live in `data_files/raw/geography/protected_areas/2025/`: the original
132,159,054-byte ZIP, catalogue JSON, unmodified extracted `source/` contents,
and a SHA-256 acquisition manifest covering all source files. The ZIP contains
the geodatabase, XML metadata and an XLSX data dictionary. Extraction rejects
unsafe paths, symlinks and duplicate members, checks CRCs and stages extraction
before publication. Cached captures verify file hashes offline. Refreshes use
a new `--output-dir`; the general Bronze overwrite flag does not replace history.

The geodatabase contains the main `ProtectedConservedArea_2025` polygon layer
(22,438 source features), a comments table and a separate delisted polygon layer.
All are preserved. The main layer uses Canada Albers Equal Area Conic (ESRI:102001).
The acquisition manifest inventories each layer's CRS, fields and feature count.
Main-layer status/biome counts are feature counts, not unique site counts or
nonoverlapping area statistics.

Status labels are read from the included dictionary's `PA_OECM_DF` domain:
1 protected area; 2 OECM; 3 interim protected area; 4 interim OECM;
5 not applicable. `BIOME` distinguishes T (terrestrial/freshwater) from M (marine).
These are preserved source categories, not a decision about which to exclude.

The two-panel `preview/cpcad_2025.png` shows main-layer statuses separately for
terrestrial/freshwater and marine features. Only display geometry is projected
to EPSG:3347 and simplified by 1 km; Bronze geometry is untouched. The preview
manifest records those choices and the source ZIP hash. Delisted records and
comments are inventoried but not plotted as current areas. There is no Silver,
pipeline impedance or renewable eligibility integration yet.

## Canadian Wind Turbine Database

The user reviewed and supplied NRCan's English geodatabase ZIP and catalogue JSON
URLs. Credit: CanmetENERGY-Ottawa and University of Alberta CABREE, under contract
from NRCan. The source has irregular updates and a stable mutable download URL.
The supplied publication/modification dates are 2024-09-20 and 2025-11-28, with
temporal coverage 1993-01-01 through 2025-09-16. The downloaded layer is named
`WTD_en_Feb02_2026`, so the capture is labelled by retrieval date, not assumed
to be a November 2025 release.

```powershell
python scripts/build_bronze.py --stages wind_turbines
python -m geocanoe.acquisition.wind_turbines --no-preview
```

Bronze root: `data_files/raw/geography/wind_turbines/snapshot-2026-10-23/`.
Preserved files: original 306,948-byte ZIP, catalogue JSON, extracted geodatabase,
and acquisition manifest with file hashes, sizes, source metadata, layer inventory,
province counts and project-name group diagnostics. Cached captures verify source
hashes offline. Refreshes use a new `--output-dir`; the general overwrite flag
does not replace a completed capture. Extraction reuses the CPCAD safe ZIP helper.

Initial inventory: 7,841 point features in EPSG:3978. Fields include project name,
turbine identifier, project/turbine capacity, rotor diameter, hub height,
manufacturer/model, commissioning, latitude and longitude. Several numerical
attributes are stored as source strings; Bronze preserves their native types.
Project capacity is repeated across turbine rows and must not be summed as if
it were individual turbine capacity. Source project capacity may differ from
summed turbine ratings, as described by NRCan.

`preview/wind_turbines.png` shows individual locations by source province or
territory, transformed to EPSG:3347 for display only. The preview manifest records
the source layer and ZIP hash. Project grouping, hulls, buffers, footprint polygons
and eligibility rules are deferred to Silver.

## TransitionZero Solar Asset Mapper, Q1 2024

The user selected Zenodo record 11368204 (version 1.0), analysis GeoPackage,
analysis CSV, lineage CSV and linked methodology PDF. These five original files
are preserved under `data_files/raw/geography/solar_facilities/tz-sam-2024q1/`,
including the Zenodo JSON snapshot. Attribution: TransitionZero Solar Asset
Mapper, TransitionZero, May 2024 release. Licence: CC BY-NC 4.0.

```powershell
python scripts/build_bronze.py --stages solar_facilities
python -m geocanoe.acquisition.solar_facilities --no-preview
```

Acquisition validates Zenodo record identity and the three dataset files against
published MD5 checksums and byte sizes, then records SHA-256 hashes for every
captured file. Subsequent runs validate locally without network access. Refresh
into a new `--output-dir`; completed captures are not overwritten by the general
Bronze overwrite flag. Original global polygons are not clipped, repaired,
dissolved or filtered in Bronze. The PDF is preserved and its file signature is
checked; acquisition does not constitute methodological validation.

Observed GeoPackage layer: `analysis_polygons.no_ukraine`, EPSG:4326, 63,096
features. The analysis CSV contains the same unique cluster IDs and capacities;
286,330 lineage rows cover those IDs. The source description advertises 63,616
assets, so the observed file count is recorded separately. The supplied lineage
CSV references raw source polygons; the optional raw-polygons GeoPackage has not
been acquired. Estimated capacities are not treated as observed plant capacities.

There are 257 assets labelled `Canada` in the analysis CSV. They are joined by
`cluster_id` for preview selection only; this is not evidence of complete Canadian
coverage. `preview/solar_canada.png` contains a centroid overview and the largest
mapped footprint as a polygon detail, displayed in EPSG:3347. When the existing
Statistics Canada provincial boundaries are available, the preview also shows
national boundaries and a provincial locator with a star at the detailed asset.
The preview manifest records source hash, boundary component hashes and display
selection. These displays do not constitute a Silver
eligibility layer.

Important date caveat: the downloaded CSV construction-date fields include
time-like strings such as `23:48.5`, while the GeoPackage retains full timestamps.
The lineage CSV's `source_date` has similar time-like strings. Preserve these
values unchanged; do not use the CSV strings for model-year filtering. Future
Silver work should review GeoPackage construction bounds (not commissioning
dates), assess Canadian completeness against CODERS and apply explicit overlap
and exclusion rules.
