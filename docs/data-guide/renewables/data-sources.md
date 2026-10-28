# Renewable Electricity Data Sources

| Role | Source | Detailed record |
|---|---|---|
| Hourly weather | Copernicus ERA5 single levels, 2018 | [ERA5](../../geospatial/data-sources/copernicus-era5-renewables.md) |
| Technology costs | Electricity ATB 2025 v1.0.0 | [ATB](../../geospatial/data-sources/atb-electricity-2025.md) |
| Land classification | NRCan 2020 Land Cover of Canada | [Land cover](../../geospatial/data-sources/nrcan-land-cover-2020.md) |
| Protected areas | ECCC CPCAD 2025 | [CPCAD](../../geospatial/data-sources/eccc-cpcad-2025.md) |
| Wind validation inventory | Canadian Wind Turbine Database | [Wind turbines](../../geospatial/data-sources/nrcan-wind-turbines.md) |
| Solar validation inventory | TransitionZero Solar Asset Mapper | [Solar facilities](../../geospatial/data-sources/transitionzero-solar-2024q1.md) |
| Wind power curves | Pinned `turbine-models` package containing NREL reference models | [Renewable processing note](../../renewables_silver.md) |
| Currency conversion | Versioned exchange and Canadian GDP-index registries | [Economic conversion](../../economics.md) |

ATB capacity factors are not used as Canadian generation profiles. ERA5 and
the explicitly pinned physical conversion assumptions produce those profiles.
The wind and solar facility inventories are currently validation evidence; they
do not directly constrain Silver eligibility or Gold capacity.
