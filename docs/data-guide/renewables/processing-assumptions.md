# Renewable Electricity Processing & Assumptions

The complete implementation record is
[Renewable Silver and Gold representation](../../renewables_silver.md). This page
summarizes the assumptions that most strongly affect results.

## Physical conversion

- ERA5 2018 supplies hourly wind and solar weather on the configured national grid.
- Wind uses pinned NREL T1–T4 physical power curves through Atlite.
- Solar uses Atlite's CSi panel model with configured tracking, ILR, inverter efficiency and losses.
- Plant losses and clipping are applied before regional aggregation.

## Spatial representation

- Weather-cell output is weighted by eligible land within each model region.
- NALCMS land-cover codes and selected CPCAD statuses define exclusions.
- Uniform technology-specific deployment densities convert eligible area to MW bounds.
- The workflow does not include parcel ownership, transmission interconnection,
  slope, elevation, offshore depth or a project-development feasibility model.

## Wind design mapping

The national resource-assigned representation maps annual mean wind at the
reference height to T1, T2, T3 or T4. These are representative turbine designs,
not IEC safety classes. The low-resource T4 mapping is retained because a
T1/T2/T3-only structure can remove or underrepresent western wind regions.

## Economics

Selected ATB OCC, optional GCC and fixed O&M rows are converted from their
declared native basis to CAD2020. No ATB capacity-factor uplift or LCOE charge is
added to the ERA5/Atlite profiles.

## Temporal limitation

Silver retains 8,760 hourly values and monthly diagnostics, but current Gold
uses annual capacity factors. The solve therefore does not represent hourly
adequacy, ramps, storage dispatch or demand coincidence.
