# GeoCANOE documentation

Documentation for the Geospatial-CANOE model, laid out like the CANOE documentation hub: one site, with each area of work in its own section,
and within each section a **data processing** guide and a **data sources** catalogue.

> **Draft.** Written from the code on the `geo-dev` branch. Open questions are flagged with markers defined in [conventions](conventions.md) and collected in [open items](open-items.md).

## Sections

| Section | Contents | Status |
|---|---|---|
| [Geospatial pre-processing](geospatial/index.md) | How public geospatial data becomes a Temoa database: Bronze, Silver, Gold, and the hand-off to Temoa | Drafted |
| [CCS and CCU cost databases](ccs-ccu-costs/index.md) | Cost models for carbon capture, transport, utilisation and storage | Planned |

Recent cross-cutting implementation notes supplement the chapter sequence:

- [Renewable Silver and Gold representation](renewables_silver.md)
- [Economic conversion and provenance](economics.md)
- [Facility-carbon accounting](emissions-accounting.md)
- [Geographic Bronze additions](geographic_bronze.md)
- [ATB Bronze acquisition](atb_bronze.md) and [CANOE_CLOUD source preference](canoe_cloud_source.md)
- [ERA5 preview generation](era5_previews.md)

## Site-wide pages

- [Conventions](conventions.md): markers for open questions and citations
- [Open items](open-items.md): every marker, grouped by chapter (generated)
- [References](references.md)

## Regenerating the generated pages

```bash
python docs/tools/gen_sources.py       # Part B source profiles from the registry
python docs/tools/gen_open_items.py    # open-items.md
```
