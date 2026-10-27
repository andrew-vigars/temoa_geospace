# Raw ERA5 previews without rebuilding Silver

From the repository root, using the existing Canada run:

```powershell
.venv/Scripts/python.exe -m geocanoe.preprocessing.renewables_preview data_files/processed/renewables/canada-vre/25km_centroid/36965ff9a6517031 --raw-era5 --bronze-dir data_files/raw/era5/canada_2018
```

This verifies the cached wind and radiation payloads and produces twelve monthly
PNG maps and matching NetCDF means under
`data_files/processed/renewables/preview/canada-vre_25km_centroid_36965ff9a6517031/era5/`.
It uses the year and weather extent of the existing run's manifest and
`wind_resource/resource.nc`. No CDS requests, land exclusion processing, Atlite
conversion, or model exports are performed. `--preview-root` overrides the
preview parent folder. Verified unchanged previews are reused on subsequent runs.

Wind panels average hourly scalar speeds at 10 m and 100 m, calculated as
`sqrt(u**2 + v**2)` before averaging. Solar is the all-hour mean downward surface
radiation: hourly ERA5 `ssrd` in J/m2 divided by 3600 to yield W/m2. Values retain
the native ERA5 grid in the NetCDF files. PNG maps use the associated CF production
polygons from `annual_capacity_factors.gpkg`, projected to EPSG:3347, with
overlap-area-weighted weather means. No eligibility masks, turbine curves or
plant losses are applied. Verified monthly means are reused when rerendering. Color
scales are fixed across months (0-15 m/s and 0-400 W/m2), with an overflow marker.
