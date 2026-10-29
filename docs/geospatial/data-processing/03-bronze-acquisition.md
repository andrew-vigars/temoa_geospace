# 3. Bronze: raw data acquisition

*Layer: Bronze. Code: [execution/bronze.py](../../../src/geocanoe/execution/bronze.py) (orchestration),
`geocanoe.acquisition.*` (one module per source), [registry/geospatial_sources.yaml](../../../registry/geospatial_sources.yaml)
(inventory). CLI: `geocanoe-build-bronze`.*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md). The per-source
> table is generated in [sources.md](../data-sources/index.md).

## 3.1 Purpose and role in the model

Bronze holds the **unmodified external data** that every later layer depends on. It
does no filtering, joining or reprojection. Its job is to make acquisition repeatable,
resumable and traceable.

Bronze stages are independent of each other and need no build profile. None reads
another stage's output, so `bronze.py` runs them without a dependency graph. A subset can be
selected with `--stages`, for example `geocanoe-build-bronze --stages aboriginal_lands nhn`.

## 3.2 Stages

| Stage | Module | Acquires | Raw location |
|---|---|---|---|
| `land_cover` | `acquisition/land_cover.py` | NRCan 2020 national land-cover raster and catalogue metadata | `data_files/raw/geography/land_cover/2020/` |
| `protected_areas` | `acquisition/protected_areas.py` | ECCC CPCAD 2025 archive, geodatabase, dictionary and metadata | `data_files/raw/geography/protected_areas/2025/` |
| `wind_turbines` | `acquisition/wind_turbines.py` | Hash-pinned Canadian Wind Turbine Database capture | `data_files/raw/geography/wind_turbines/snapshot-2026-10-23/` |
| `solar_facilities` | `acquisition/solar_facilities.py` | TransitionZero Solar Asset Mapper Q1 2024 bundle | `data_files/raw/geography/solar_facilities/tz-sam-2024q1/` |
| `basemaps` | `acquisition/basemaps.py` | StatCan 2021 provincial/territorial boundaries | `data_files/raw/basemaps/` |
| `residential` | `acquisition/residential.py` | StatCan 2021 dissemination-area boundaries and table 98-10-0015-01 population/dwelling counts | `data_files/raw/residential/` |
| `gasoline_demand` | `acquisition/gasoline_demand.py` | StatCan table 23-10-0066-01 (motor-fuel sales) and 2021 population-centre boundaries | `data_files/raw/gasoline_demand/` |
| `aboriginal_lands` | `acquisition/aboriginal_lands.py` | NRCan Aboriginal Lands of Canada boundaries, WMS metadata, manifest | `data_files/raw/aboriginal_lands/` |
| `nrn` | `acquisition/nrn.py` | StatCan National Road Network GeoPackages, per jurisdiction | `data_files/raw/nrn/{PROVINCE}/` |
| `nhn` | `acquisition/nhn.py` | NRCan National Hydro Network hydrographic features, WMS metadata, manifest | `data_files/raw/nhn/` |
| `emissions` | `acquisition/emissions.py` | ECCC 2024 large-facility GHG emissions (CSV and GeoJSON) | `data_files/raw/emissions/` |
| `co2_storage` | `acquisition/co2_storage.py` | A selected CanCO₂ unified-storage release, copied from a local sibling repository | `data_files/raw/canco2_storage/` |
| `era5_renewables` | `acquisition/era5_renewables.py` | Monthly hourly ERA5 wind and radiation payloads for the configured 2018 Canadian extent | `data_files/raw/era5/canada_2018/` |
| `atb` | `acquisition/atb.py` | Complete ATB Electricity 2025 v1.0.0 summary, preferring a compatible verified cache with official fallback | `data_files/raw/atb/2025/v1.0.0/` |
| `economics` | `acquisition/economics.py` | Pinned StatCan GDP index and CANOE currency-exchange source snapshots | `data_files/raw/economics/` |

Two inputs are **not** acquired by Bronze and are controlled files in the repository:
the H2 pipeline cost workbook (`data_files/models/cost_models/…/h2_pipeline_costs_master_v2.xlsx`,
chapter 10) and the legacy site table `data_files/old_files/sites_full.csv` (chapter 9).

## 3.3 Acquisition mechanics

Shared logic is in `acquisition/_download.py`:
- Downloads are sequential and "polite" by default.
- Large files use `.part` files and HTTP range requests where the server supports them, so interrupted
  transfers are resumable and incomplete files are not mistaken for finished inputs.
- Archives are extracted, flattened into the raw folder, validated, and (for NRN, basemaps) deleted.
- The NHN hydrographic-feature archive is approximately 15 GB. An existing validated GeoPackage is reused
  before any download is attempted.
- Several stages write a JSON **acquisition manifest** and snapshot the source's WMS capabilities XML
  (NHN, Aboriginal Lands, gasoline demand). The manifests are the best record of what was downloaded and when.
- New geographic, ATB, ERA5 and economics stages use immutable or explicitly
  versioned capture directories, content hashes, atomic publication and cache
  validation. Their general `--overwrite` behaviour intentionally does not
  replace a verified historical capture.
- ERA5 acquisition is request-plan driven and resumable by month. ATB preserves
  the complete source before a separate preprocessing step selects renewable
  families. Economics acquisition never silently rewrites the reviewed registry
  tables; `python -m geocanoe.preprocessing.economics` is the explicit refresh.

CanCO₂ storage is different: the unified GeoPackage is produced by a separate repository
(`canco2-storage`, expected as a sibling of the `repos` directory, or set with the
`CANCO2_STORAGE_REPO` or `CANCO2_STORAGE_GPKG` environment variables). GeoCANOE treats the newest
timestamped `*_CanadaGeologicalStorageUnified*_v*.gpkg` as an external raw input and copies the complete release
family, writing `selected_release.txt` to record the selection.

## 3.4 The source registry

[registry/geospatial_sources.yaml](../../../registry/geospatial_sources.yaml) (schema v2) is the canonical inventory of Bronze sources. Each source records publisher,
licence, URLs, acquisition module, refresh policy, release model, Bronze root and named artifacts. Silver stages resolve their inputs through it, so the registry is
both documentation and configuration.

**Part B** presents it as a catalogue, one profile per source: see the [data sources summary](../data-sources/index.md), the
[controlled project inputs](../data-sources/controlled-inputs.md) that are not in the registry, and the [update procedures](../data-sources/update-procedures.md).

Release models matter for reproducibility. *Fixed* sources (2021 boundaries and DA data, ECCC 2024 emissions) do not change. *Rolling* sources (NRN, NHN,
Aboriginal Lands, StatCan fuel sales) serve the current release at a stable URL. The CanCO₂ release is a user-selected timestamped file.

## 3.5 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Refresh is manual, but release models differ. | Rolling sources can change at stable URLs; newer stages instead require new snapshot/release directories. Record the selected manifest and hash in published results. |
| 2 | The fuel-sales table is requested for a fixed year in the Silver profile (`sales_year`), but the file itself is rolling. | The table at download time must contain that year. |
| 3 | The English-language NRN is the only one used. | The French GeoPackages are catalogued but ignored. |
| 4 | The CanCO₂ release is chosen as the lexicographically newest timestamp unless an exact file is given. | A newer release can change storage evidence without any GeoCANOE change. **[confirm]** which release is used for published results. |
| 5 | Raw data is not committed to the repository (`data_files/raw/`). | Reproducing a build requires the exact cached payload or a verified re-download; rolling distributions may differ. |
| 7 | The wind-turbine and solar-facility captures are inventory/validation inputs only. | They do not currently alter renewable eligibility, profiles, costs or Gold capacity limits. |
| 8 | ERA5 uses 2018 as the shared model weather year. | One year is not a climatology; changing it requires coordinated demand/profile review and a full renewable rebuild. |
| 6 | Licence terms are those of the publishers (Statistics Canada Open Licence, Open Government Licence - Canada). The CanCO₂ licence is "see acquired release metadata". | **[cite-data]** Confirm CanCO₂ terms and attribution before publishing derived products. |

## 3.6 Outputs and consumers

All Bronze output is under `data_files/raw/`. Every Silver stage reads from there (chapters 4–10).

## 3.7 Validation

Acquisition tests cover the shared downloader and the residential, gasoline,
NHN, Aboriginal Lands, ERA5, ATB, economics, land-cover, protected-area,
wind-turbine and solar-facility adapters. The source registry has its own
contract test (`tests/test_geospatial_source_registry.py`). NRN, basemap and
emissions acquisition remain less directly covered.
