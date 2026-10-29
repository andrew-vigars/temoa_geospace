# Controlled project inputs

Not every input comes from a public dataset. These files are **maintained in the repository** (or inherited from an
earlier model) and are not in `registry/geospatial_sources.yaml`. Their provenance is therefore the weakest in the model, and
each needs an owner and a citation. Processing logic is in [Part A](../data-processing/01-overview-and-architecture.md).

## Summary

| Input | Location | Used in | Provenance status |
|---|---|---|---|
| Legacy site table (LCOE, maximum electricity) | `data_files/old_files/sites_full.csv` | [Ch. 9](../data-processing/09-emissions-and-site-inputs.md) | Origin undocumented |
| H2 pipeline cost workbook | `data_files/models/cost_models/transport/master_files/h2_pipeline_costs_master_v2.xlsx` | [Ch. 10](../data-processing/10-pipeline-cost-models.md) | Underlying studies not recorded |
| Gasoline supply-anchor registry | `registry/gasoline_supply_anchors.csv` | [Ch. 8](../data-processing/08-gasoline-demand.md) | NRCan web pages; partly map-inferred |
| Pipeline impedance penalty profiles | `registry/pipeline_impedance_penalties.yaml` | [Ch. 6](../data-processing/06-pipeline-impedance.md) | Inherited, unvalidated |
| Technology, commodity and efficiency tables | `registry/techs.csv`, `commodities.csv`, `generation_efficiency.csv`, `transport_techs.csv` | [Ch. 12](../data-processing/12-gold-technology-cost-scenario.md) | Inherited; derivation not recorded |
| Global model settings and scenarios | `registry/model.toml`, `registry/scenarios/*.toml` | [Ch. 2](../data-processing/02-configuration-and-setup.md), [Ch. 12](../data-processing/12-gold-technology-cost-scenario.md) | Authored in this project |
| Analytic cost-curve constants and fixed costs | `EOS_INVESTMENT_COST_PARAMETERS` and literals in `schema/build.py` | [Ch. 12](../data-processing/12-gold-technology-cost-scenario.md) | Inherited from an earlier model |
| Temoa v4 database schema and EOS extension | Installed `temoa` package | [Ch. 1](../data-processing/01-overview-and-architecture.md), [Ch. 12](../data-processing/12-gold-technology-cost-scenario.md) | Software dependency |

## Legacy site table

- **What:** about 26,750 rows with columns `lon`, `lat`, `LCOE`, `max_elec`. Province codes were assigned by the retired legacy workflow.
- **Role:** electricity generation cost (mean per cell) and maximum electricity potential (sum per cell).
- **Provenance:** **[confirm]** the generating study, year, resource assumptions, units of `max_elec`, and spatial coverage (the first rows are in British Columbia).
  **[cite-data]**
- **How to update:** replace the file, rerun province assignment (`geocanoe-build-silver`), and rebuild Gold. Add the file to the source registry once its origin is documented.

## H2 pipeline cost workbook

- **What:** worksheets *Normalized CAPEX*, *Normalized Variable OPEX*, *Normalized Fixed OPEX*, with pipeline inner diameter (inch), annual capacity (t H₂/year) and cost in CAD 2020 per km.
- **Role:** fitted to produce the economies-of-scale curve and OPEX slopes applied to all four pipeline technologies.
- **Provenance:** **[cite-data]** the engineering study or studies, standards and cost basis behind the workbook.
- **How to update:** replace the workbook, then rerun the cost stages (`C1`, `C2`) and Gold. A commodity-specific workbook for CO₂, methanol or gasoline is the planned replacement for the generalized assumption.

## Gasoline supply-anchor registry

- **What:** named market centres per province with `evidence_basis` of `text_named` (NRCan text names the market) or `map_inferred` (read from a qualitative supply-orbit map).
- **Sources:** Natural Resources Canada pages on petroleum-product distribution networks and the refining sector in Canada (URLs are in the file).
- **Caveat:** not a terminal inventory. The file says so in its notes.
- **How to update:** edit the CSV, keeping `pcpuid` consistent with the 2021 population-centre identifiers, then rebuild gasoline demand.

## Pipeline impedance penalty profiles

- **What:** named assumption sets: `no_penalties` (control) and `legacy_reference_v1`. Each layer records its penalty method, value and an `evidence` block (status, citation, note).
- **Provenance:** all non-control values are `legacy_model_assumption_unvalidated`, inherited from the earlier `Hydrogen_Project_Final` model.
- **How to update:** add a new named profile and select it with `[pipeline_impedance].penalty_profile`. Do not edit an existing profile, because the profile name appears in the Silver build ID.

## Technology and cost tables

- **What:** commodities, technology flags, conversion efficiencies, transport technology cost slopes, plus literal constants in the schema builder (fixed investment 1,000, CO₂ capture cost 50, backup gasoline cost 500,000, input-split proportions, analytic EOS parameters).
- **Provenance:** inherited. **[cite-data]** Each constant needs a source or an explicit "legacy, unvalidated" label. See the list in [Ch. 12](../data-processing/12-gold-technology-cost-scenario.md) and [Ch. 15](../data-processing/15-unit-conventions.md).
- **How to update:** edit the CSVs. Constants in `schema/build.py` should move into `registry/` so they can be versioned and documented alongside the rest.
