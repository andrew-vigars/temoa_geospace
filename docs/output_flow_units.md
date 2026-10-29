# Output flow unit audit — 2026-10-29

The baseline run `2026-10-29_111053_gold_canada-vre_vre-baseline_e30f3654_44b3d28b`
contains 2,354 `output_flow_out` rows, all with NULL units. This includes 40
H2 pipeline rows and 984 H2 truck rows. The sample selection workbook is
`region_selections/Ontario_West_H2Cycle/excel_outputs/output_tables_subset.xlsx`.

## Where the metadata was lost

Silver prepares regional demand, production inputs, road distances and pipeline
cost products. Commodity physical units do not pass through a dedicated Silver
transformation: Gold reads `registry/commodities.csv` directly in
`rebuild_static_supporting_tables`. That registry left all core commodity units
blank, and both the archived input and solved database inherited the blanks.

TEMOA's `_internal/table_writer.py::_get_flow_units` uses the input commodity
for `output_flow_in`, and the output commodity for `output_flow_out` and
curtailment. Its `UnitPropagator` reads `commodity.units`. The prior exporter
copied the solved tables without repairing or explaining the missing metadata.

## Physical unit contract

| Commodities | Native unit |
| --- | --- |
| h2, ch3oh, co2, co2_stored, gsl, d_gsl | t (metric tonnes) |
| elc | MWh |
| ethos (dummy source) | dimensionless |

These labels describe the model's native physical quantity. The annual demand
input still uses `t/year`; the model's representative annual time structure
supplies that time basis. A flow's `units` column does not independently state
its annual/seasonal aggregation, and quantities are not multiplied by the
planning-period duration when adding labels.

The registry now declares these units. Gold rejects missing/blank commodity
units, and input diagnostics include the commodity table in the unit inventory.
Tests confirm that TEMOA's actual commodity-unit parser recognizes the physical
definitions, so new builds/solves can propagate them into SQL output tables.
Existing archived inputs and solved databases are retained as run evidence.

## Historical export handling

For flow input, output, output summary and curtailment exports, the exporter now:

1. Preserves nonempty units already present in solved output rows.
2. Fills missing labels from the archived database's commodity table.
3. If that is also blank, uses the current registry and marks the label as
   `current_registry_inference`.
4. Leaves unknown commodities unresolved, labelled `missing`.

Every affected row has `units_source`; `FlowUnitAudit` counts rows by table,
commodity, units and provenance. These are metadata repairs, with no numerical
conversion and no writes to the archived database. Current-registry inference
is explicitly distinct from metadata present when the run was solved.

The corrected sample `output_tables_subset_with_units.xlsx` contains 44 H2 input
flow rows and 44 H2 output flow rows, each labelled `t` with source
`current_registry_inference`. All original non-unit columns were compared
against the first export and are unchanged.

## Remaining cost-unit limitation

In this run, generalized pipeline variable costs declare `CAD2020/t`, while
truck and electricity transmission costs declare the generic `M$/unit` inherited
from legacy transport coefficients. The source currency/year and dollar-versus-
million-dollar basis of those legacy coefficients are not established by this
flow-unit fix. They must be reconciled before claiming consistent transport
economics or interpreting the loop as economically justified. See `economics.md`.
No cost coefficients or currency labels were changed in this repair.

Rebuild Gold and solve again to populate original SQL outputs with the corrected
commodity metadata. Restart the map helper to use the updated exporter for an
existing solved run.
