# MISO MTEP24 transmission exploration inputs

Used by `notebooks/notebook24_miso_transmission_capacity_costs.ipynb`.

The user-provided January redline draft motivated this analysis. Numerical inputs
come from the **clean May 1, 2024 MTEP24 guide**, saved here as
`MISO_MTEP24_2024-05-01.pdf`; old/replacement prices in the draft are not combined.
Source URL, checksum, extraction method and input-table checksums are recorded in
`source_provenance.json`. The source is a fixed local model input, not a new Bronze
workflow. No download occurs when running the notebook.

## Tables

| File | Source locator (printed pages) | Records |
| --- | --- | ---: |
| `ac_line_costs.csv` | Tables 4.1-1 / 4.1-2, pp. 38–39 | 210 |
| `ac_design_ratings.csv` | Table 3.1-5, p. 33; ROW from 3.1-1, p. 31 | 8 |
| `state_land_terrain.csv` | Table 3.1-7, p. 34 | 15 |
| `ac_station_costs.csv` | Tables 4.2-1 / 4.2-2, p. 41 | 96 |
| `terrain_adders.csv` | Table 2.2-9, p. 18 | 4 |

Line scenarios are 15 states × (8 single-circuit + 6 double-circuit voltages).
Default high-voltage analysis retains 115 kV and higher, giving 180 scenarios and
12 unique voltage/circuit designs. State rows are engineering scenarios, not actual
independent project observations. Descriptive state ranges are not prediction intervals.

## Basis and boundaries

- The clean guide has contradictory price-year wording on printed p. 1: it states
  2023 dollars, then says costs were escalated from 2023 to 2024 by 5%. We select
  **2024 USD as a documented working interpretation** supported by MISO's January
  update presentation, and calculate a 2023 price-year sensitivity. Changing the
  interpretation changes currency rebasing, not the quoted source values.
- Quoted exploratory costs include 30% contingency and 7.5% AFUDC. The guide defines
  AFUDC on project cost plus contingency, so removing these factors divides by
  `(1.30 * 1.075)`, not by `1.375`. The resulting subtotal is not automatically
  scope-equivalent to AESO's reported line components or overnight model CAPEX.
- $/mile for each single/double-circuit line is interpreted as route-mile. Divide
  by 1.609344 to obtain $/route-km. Do not multiply double-circuit line costs by two.
- Table 3.1-5 current/MVA ratings are **per circuit**, already reflecting the bundle.
  Do not apply its conductor quantity again. Double-circuit capacity is an explicit
  sum of two circuit ratings; it is not firm/N-1 transfer capability. Temperature
  and weather conditions are not specified alongside the indicative ratings.
- The scenario adds matching line positions at both existing terminals. This is
  not a complete station/transformer design or a promise of network feasibility.
- State exploratory costs already reflect indicative land/terrain assumptions.
  `terrain_adders.csv` is contextual; do not add it wholesale to those state costs.
- GSC and Ontario estimates are not pooled with MISO. AESO conductor ratings are
  not populated using MISO's voltage-only assumptions.

## Running and refreshing

Run notebook 24 in the repository `.venv`; it uses numpy, pandas, matplotlib and
the existing audited currency/GDP conversion module. Inputs persist here; outputs
go to ignored `data_files/processed/transmission/miso/notebook24/`.
Edit `analysis_assumptions.json` for scenarios. It remains explicitly exploratory.

The optional `notebooks/support/extract_miso_mtep24.py` regenerates the five source
tables and source provenance from the pinned clean PDF using `pypdf`. It is not
required for notebook execution. Run it from the repository root with a Python
runtime containing pypdf. Review any regenerated differences against the PDF.

## References

- Clean guide: https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf
- Official update presentation supporting 2024 price interpretation:
  https://cdn.misoenergy.org/20240131%20PSC%20Item%2005%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24631525.pdf
