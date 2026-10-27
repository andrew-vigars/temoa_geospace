# Transmission cost exploration inputs

Notebook: `notebooks/notebook23_aeso_transmission_capacity_costs.ipynb`.
These are exploratory inputs, not production `ELC_TRANS` costs.

See [LITERATURE_REQUIREMENTS.md](LITERATURE_REQUIREMENTS.md) for the prioritized
evidence checklist, extraction fields, search phrases and model acceptance criteria.

The separate [MISO input bundle](miso/README.md) supports notebook 24 with a clean
MTEP24 PDF, source tables, ratings and explicit scenario assumptions. Its engineering
scenarios are not pooled with the AESO observations.

## Acquisition decision

Use manually acquired sources in this models folder for now. On 2026-10-27,
AESO's public page linked a JavaScript Tableau profile rather than a stable CSV;
a public profile API probe returned 404. The GSC DOI resolved to a JavaScript
repository page without a direct archive link in the returned HTML. These checks
did not establish an easy reproducible download, and do not prove downloads are
unavailable. No Bronze stage or browser/session scraper is registered.

Refresh AESO by downloading the same full-data worksheet from Tableau, preserving
the existing snapshot before replacement. Review schema, filters, row counts,
component coverage, checksum and dollar basis. A dashboard update year is not the
price year. Update `source_provenance.json` and the notebook checksum only after
review; do not concatenate successive exports as independent observations.

## Files

- `AESO_Transmission_2024.csv`: unchanged user-supplied Tableau export, previously
  named `1_Full Data_data.csv`. 1,241 rows / 158 line IDs; observations end in 2023.
- `source_provenance.json`: source URLs, snapshot checksum, acquisition limitations
  and evidence for the units and price basis.
- `cost_assumptions.json`: reviewed cost basis and unresolved scope/status decisions.
- `design_ampacity_review.csv`: editable design rating lookup. Blank ratings mean
  unknown; provide sources and rating conditions before approving a row. The
  notebook preserves edits and reports newly encountered designs separately.
- `context_benchmarks.csv`: manually transcribed GSC examples and Ontario package
  estimates with explicit scope. These are not observations for pooled fitting.

Generated audit tables, figures, discrete options and conditional fit results go
to `data_files/processed/transmission/aeso/notebook23/` (ignored by Git).

## Model boundary

AESO CNST$ is CAD 2016, displayed as $/km in the user-provided dashboard screenshot.
Route-km versus circuit-km, cost status, component additivity and completeness still
require review. Line component sums exclude unverified project-level cost categories.
One repeated-component line is excluded pending clarification; full builds and partial
circuit additions are not mixed.

First construct discrete design options using median reported component sums and
reviewed nominal thermal capacities. The observed interquartile range is descriptive,
not a prediction interval. Then compare a pooled power law and a voltage/circuit
adjusted power law using held-out line IDs. Shared parent projects remain a possible
source of dependence until a project crosswalk is obtained. No economics of scale
is assumed or forced, and 500 kV has only two source lines.

Ontario estimates are reinforcement packages including station work and other
facilities; GSC examples mix voltage, structures and terrain. Do not pool either
with AESO component sums, infer a terrain multiplier from the GSC ratio, or treat
Ontario system load-meeting capability as a line rating. Cross-source comparison
must retain scope distinctions even after dollar rebasing.

## References

- AESO: https://aeso.ca/grid/grid-planning/transmission-costs/
- GSC Open File 9302e, *Datasets to support infrastructure cost modelling*,
  transmission section, printed p. 3:
  https://publications.gc.ca/collections/collection_2026/rncan-nrcan/m183-2/M183-2-9302-eng.pdf
  Dataset DOI: https://doi.org/10.4095/prsanq99u5
- IESO, *Eastern Ontario Bulk Plan*, 2026-08-20, printed pp. 45 and 49,
  user-supplied `ON_E_BulkPlan.pdf`. Costs are CAD 2025 planning estimates;
  plan-wide stated uncertainty is -50% to +100%.
