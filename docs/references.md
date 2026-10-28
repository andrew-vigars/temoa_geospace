# References

> Skeleton. Each entry below corresponds to a `[cite-*]` marker in the chapters. Replace the prompts with
> full citations as they are resolved, then remove the markers. Data products are listed with their publishers, since those are known
> from `registry/geospatial_sources.yaml`. Literature entries are intentionally left unfilled rather than guessed.

## Data products

| Product | Publisher | Used in |
|---|---|---|
| 2021 Census provincial and territorial boundaries, `lpr_000b21a_e` | Statistics Canada | Chapters 4, 8, 9 |
| 2021 Census dissemination-area boundaries `lda_000b21a_e` and table 98-10-0015-01 | Statistics Canada | Chapters 6, 8 |
| 2021 population-centre boundaries `lpc_000b21a_e` | Statistics Canada | Chapters 6, 8 |
| Table 23-10-0066-01, fuel sales | Statistics Canada | Chapter 8 |
| National Road Network | Statistics Canada | Chapter 5 |
| Aboriginal Lands of Canada legislative boundaries | Natural Resources Canada | Chapter 6 |
| National Hydro Network, hydrographic features | Natural Resources Canada | Chapter 6 |
| Greenhouse gas emissions from large facilities, 2024 | Environment and Climate Change Canada | Chapter 9 |
| Unified geological storage release | CanCO2 Storage project | Chapter 7 |
| NRCan refining and petroleum-product distribution pages | Natural Resources Canada | Chapter 8 |

## Software

| Item | Where used |
|---|---|
| Temoa v4 and the economies-of-scale extension | Chapters 1, 12, 13 |
| GeoPandas, Shapely, PyProj, GeoPy | Chapters 4–11 |
| Gurobi | Chapter 13 |

## Methodology references still needed

Chapter numbers refer to [Part A](geospatial/data-processing/01-overview-and-architecture.md).

| Topic | Marker | Chapters |
|---|---|---|
| Spatial regionalisation of energy-system models (grid vs administrative regions) and resolution effects | `cite-method` | 4, 16 |
| Modifiable areal unit problem in aggregation of points to cells | `cite-method` | 11 |
| Least-cost corridor / pipeline siting and cost-surface methods | `cite-method` | 6 |
| Penalty factors for Indigenous lands, waterbodies and populated areas in routing | `cite-data` | 6 |
| Hydrogen, CO₂ and liquid-fuel pipeline cost studies | `cite-data` | 10 |
| Economies of scale and piecewise-linear cost approximation in energy models | `cite-method` | 10, 12 |
| Spatial disaggregation of transport-fuel demand | `cite-method` | 8 |
| Geological CO₂ storage assessment methods (NATCARB, BC atlas, Atlantic assessment) | `cite-data` | 7 |
| Point-in-polygon, nearest-feature, overlay and projection definitions (EPSG:3347) | `cite-gis` | 4, 5, 6, 7, 11 |
| NRN, NHN and Aboriginal Lands product specifications | `cite-gis`, `cite-data` | 5, 6 |
| Source of legacy LCOE and `max_elec` site values | `cite-data` | 9 |
| Source of electrolyser, methanol and gasoline plant efficiencies and cost curves | `cite-data` | 12 |
| Basis for choosing a point-source storage fraction in a reported scenario | `cite-data` | 12 |
| Currency basis and conversion for all cost lines | `cite-data` | 15 |
