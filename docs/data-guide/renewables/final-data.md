# Renewable Electricity Final Data

## Silver bundle

A completed immutable run under `data_files/processed/renewables/` contains:

- hourly monthly NetCDF profiles;
- `annual_capacity_factors.csv` and a spatial GeoPackage;
- land-exclusion masks, eligible-area summaries and capacity bounds;
- wind-resource assignment and regional T1–T4 mix audits;
- selected ATB rows and the technology crosswalk;
- currency-conversion evidence;
- Temoa-ready parameter tables;
- validation reports, checksums and a run manifest; and
- annual and monthly preview maps.

`selected_run.json` points to the immutable bundle selected for a build/grid.
The pointer is not sufficient evidence by itself; consumers validate the
bundle's contract and checksums.

## Gold representation

When renewable replacement is enabled, Gold removes legacy `ELC_GEN` rows and
adds the validated wind and solar technologies, including:

- investment and fixed costs in CAD2020;
- annual capacity-factor limits;
- MW capacity bounds derived from eligible land;
- complete regional technology coverage; and
- the renewable bundle identity in the Gold manifest.

## Post-solve products

Successful renewable solves create deployment-weighted monthly profiles and
plots under the run directory. These use the exact Silver bundle recorded by
Gold but remain diagnostics; they do not add intra-annual constraints to the
optimization.
