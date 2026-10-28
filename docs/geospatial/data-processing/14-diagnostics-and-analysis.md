# 14. Diagnostics and post-solve analysis

*Code: `geocanoe.diagnostics` (CLI `geocanoe-diagnostics`), `geocanoe.analysis` (CLI `geocanoe-export`, `geocanoe-map`).*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 14.1 Purpose

Two checks bracket the solve, and two tools turn results into something readable. None of them alters
upstream products.

## 14.2 Input diagnostics ("pre-solve gate")

`geocanoe-diagnostics inputs` audits the Silver artifacts and the encoded Gold schema **before** a solve. It does not modify any
database. By default it interactively selects a build profile and a basemap. Checks include:

| Group | Checks |
|---|---|
| Files | Required preprocessing artifacts exist |
| Topology | Basemap, graph and road-connectivity metrics are sane (`audit_topology`, `check_topology_summary`) |
| Snapping | Site and CO₂ points snap within warning, audit and critical distance thresholds (`check_snap_distances`) |
| Demand | Silver regional gasoline demand equals the demand encoded in Gold (`check_gasoline_demand_conservation`) |
| Schema | Required tables present and non-empty; primary keys unique; referential integrity against regions, commodities and technologies; graph nodes equal schema node regions; numeric columns finite and in range; EOS curves ordered, bounded and contiguous |
| Readiness | A technology coverage matrix with minimum readiness (`check_technology_readiness`) |
| Units | Units present, parseable and category-compatible (`check_table_units`), conservative |

Each run writes CSV evidence. A final check fails the gate if any declared diagnostic did not execute (`check_gate_coverage`).

## 14.3 Output diagnostics ("post-solve gate")

`geocanoe-diagnostics outputs` runs on a solved database and is deliberately **independent of solver status**: a MILP can report
"optimal" while the equations being solved are wrong. Checks:

1. **Commodity balance** at each node, reinterpreting edge pseudo-regions: production + imports = consumption + exports + demand,
   for conserved commodities only.
2. **Edge technologies:** positive flows on EOS-defined edges must have capacity (`check_eos_invest_defined_edge_flow_capacity`).
3. **Objective consistency:** `total_system_cost` equals the sum of the cost outputs.

`geocanoe-run --diagnostics strict` runs the pre- and post-solve gates automatically.

## 14.4 Export (`geocanoe.analysis.exports`)

Writes each `Output*` table of a solved database to a separate worksheet in one Excel workbook, validating Excel dimension limits and
creating unique valid sheet names. It can be run interactively or after a solve.

## 14.5 Interactive map (`geocanoe.analysis.maps`)

Decodes a solved database into a Leaflet/Folium HTML map: regions, graph edges, pipeline and truck flows, with layer controls, tooltips
and popups. Edge pseudo-regions are split into endpoints, and parallel corridors are drawn resolution-aware. It reads the processed graph and
basemap again (not the Gold database) for geometry, with road-connectivity geometry as optional context. Folium's JavaScript and CSS load from CDNs.

`analysis/pipeline_capacity.py` derives **pseudo-parallel pipeline counts**. Because each corridor has an EOS capacity curve (possibly stacked),
the module infers the one-curve terminal capacity from encoded segment widths and expresses installed capacity as a **fractional number of
pipelines**.

## 14.6 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Diagnostics check **internal consistency** and plausibility, not validity against real data. | A model can pass every gate and still rest on unvalidated parameters (chapter 12). |
| 2 | Snap-distance thresholds (warning/audit/critical) are fixed in the gate. | **[confirm]** their values and rationale. |
| 3 | Commodity balance excludes source and emission commodities. | These are not conserved quantities in the model. |
| 4 | Fractional pipeline counts are a post-processing interpretation. | Temoa itself does not model a count of parallel pipelines. **[cite-method]** |

## 14.7 Tests

`tests/test_diagnostics_input.py`, `test_diagnostics_output.py`, `test_diagnostics_database.py`, `test_diagnostics_units.py`,
`test_diagnostics_foundation.py`, `test_analysis_storage.py`, `test_pipeline_capacity_analysis.py`, `test_maps_rendering.py`,
`test_maps_basemap_resolution.py`.
