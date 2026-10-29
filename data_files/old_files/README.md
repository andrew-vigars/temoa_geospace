# Retired inputs

The legacy site inputs and CANOE v3 database/schema were retired on 2026-10-29.
Wind and solar resource potential and capacity factors now come from the renewable workflow.

- `sites.csv` and `sites_full.csv`: original legacy site tables.
- `legacy_inputs/`: archived province-assigned products, historical audits, and preprocessing implementation.
- `CANOE_geospatial.sqlite` and `canoe_dataset_schema.sql`: historical CANOE database and schema.

Bronze and Silver do not consume these files. Gold and the input gate retain an
explicit archived-site fallback for older configurations without a renewable bundle.
The archived Python module is reference material and is not a supported CLI.
