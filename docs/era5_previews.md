# Raw ERA5 previews without rebuilding Silver

## Standard renewable evidence gallery

The latest research-update reference is the T4-GCC run. Generate its standalone
Silver gallery without rebuilding weather, Atlite profiles, or model inputs:

```powershell
.venv/Scripts/python.exe -m geocanoe.preprocessing.renewables_progression data_files/processed/renewables/t4-gcc/canada-vre/25km_centroid/757ba5211761066a data_files/processed/renewables/t4-gcc/preview/canada-vre_25km_centroid_757ba5211761066a
```

Open `index.html` in that destination. Standard CF-preview builds also generate
this gallery. It shows existing monthly raw ERA5 maps, wind/solar land eligibility,
the individual T1–T4 area shares, their combined dominant-type map, and final annual
wind/solar CF maps. Each exported map includes its legend. Maps use the production
geometry in EPSG:3347 with fixed fraction/CF scales of 0–1. The combined type map
is a visual summary; final wind CFs preserve mixtures rather than selecting the
dominant type. Missing raw previews are identified explicitly; generate them with
the command below using the desired run directory.

The gallery also exports national and regional-detail T1–T4 pie maps, as PNG and
zoomable SVG. Each production region has an equal-radius pie at a representative
point on its EPSG:3347 polygon. Slices show fractions of represented wind area;
pie size does not encode eligible area or capacity. Regions without represented
wind area retain a grey basemap polygon without a pie. The detail window is
selected around the densest neighbourhood of regions with mixed turbine shares.

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
