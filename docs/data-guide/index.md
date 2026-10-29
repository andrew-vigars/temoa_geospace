# Data Guide

The Data Guide describes what enters GeoCANOE, how it is transformed, which
assumptions affect the model, and what each domain contributes to the final
Temoa database.

Each mature domain follows the same pattern:

1. **Data Sources** — publisher, licence, release identity and fields consumed.
2. **Processing & Assumptions** — transformations and modelling choices.
3. **Final Data** — durable Silver products and the Gold tables they affect.

## Domains

- [Geospatial](../geospatial/index.md): study area, graph, roads, impedance,
  demand, emissions, storage and spatial-to-model mapping.
- [Renewable Electricity](renewables/index.md): ERA5/Atlite profiles, physical
  wind and solar assumptions, land eligibility, ATB costs and Gold replacement.
- [CCS and CCU Costs](../ccs-ccu-costs/index.md): planned consolidated guide for
  capture, transport, utilization and storage costs.

Cross-domain assumptions are consolidated in
[Known assumptions and limitations](../geospatial/data-processing/16-known-assumptions-and-limitations.md).
