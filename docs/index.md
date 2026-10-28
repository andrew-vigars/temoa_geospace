# GeoCANOE documentation

Documentation for the Geospatial-CANOE model, organized around the same user
journeys as the CANOE documentation hub: start running the model, understand
its architecture, inspect domain data and assumptions, or follow the evidence
behind model-development decisions.

> **Draft.** Written from the code on the `geo-dev` branch. Open questions are flagged with markers defined in [conventions](conventions.md) and collected in [open items](open-items.md).

## New here?

Start with [What is GeoCANOE?](what-is-geocanoe/overview.md) for a conceptual
overview and [GeoCANOE, CANOE and Temoa](what-is-geocanoe/canoe-and-temoa.md)
for the model boundary.

## Ready to run it?

- [Quickstart](quickstart.md) gives the shortest Bronze-to-solve path.
- [Get Started](get-started.md) explains installation, configuration layers and artifact locations.

## Data Guide

| Domain | Contents | Status |
|---|---|---|
| [Geospatial pre-processing](geospatial/index.md) | How public geospatial data becomes a Temoa database: Bronze, Silver, Gold, and the hand-off to Temoa | Drafted |
| [Renewable electricity](data-guide/renewables/index.md) | ERA5/Atlite profiles, physical wind and solar assumptions, land eligibility, ATB costs and Gold replacement | Active |
| [CCS and CCU cost databases](ccs-ccu-costs/index.md) | Cost models for carbon capture, transport, utilisation and storage | Planned |

The [Data Guide overview](data-guide/index.md) provides the common
Data Sources / Processing & Assumptions / Final Data structure.

## Detailed implementation notes

- [Renewable Silver and Gold representation](renewables_silver.md)
- [Economic conversion and provenance](economics.md)
- [Facility-carbon accounting](emissions-accounting.md)
- [Geographic Bronze additions](geographic_bronze.md)
- [ATB Bronze acquisition](atb_bronze.md) and [CANOE_CLOUD source preference](canoe_cloud_source.md)
- [ERA5 preview generation](era5_previews.md)

## Model development

- [Model development records](model-runs/index.md)
- [Structural sensitivity runs](model-runs/structural-sensitivity/index.md)
- [Decision records](model-runs/decision-records/index.md)

## Site-wide pages

- [Conventions](conventions.md): markers for open questions and citations
- [Open items](open-items.md): every marker, grouped by chapter (generated)
- [References](references.md)
- [Glossary](what-is-geocanoe/glossary.md)
- [Contributing](contributing.md)

## Regenerating the generated pages

```bash
python docs/tools/gen_sources.py       # Part B source profiles from the registry
python docs/tools/gen_open_items.py    # open-items.md
```
