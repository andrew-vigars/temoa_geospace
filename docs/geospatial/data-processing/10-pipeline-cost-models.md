# 10. Pipeline cost models

*Layer: Silver (cost templates) → Gold (EOS curves and OPEX rows). Code: `geocanoe.costs.pipelines.h2.capacity_costs`
(stage `C1`), `geocanoe.costs.pipelines.h2.cost_models` (stage `C2`); Gold `build_generalized_pipeline_eos_investment_curves`,
`build_generalized_pipeline_opex_rows`.*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 10.1 Purpose and role in the model

Pipeline cost depends on both **distance** and **capacity**. Larger pipelines cost more in total but less per unit of
capacity. This chapter fits cost functions to engineering data for hydrogen pipelines and converts them to a
**piecewise-linear capacity-cost curve per kilometre** (economies of scale, "EOS") that Temoa can use.

Currently this **one hydrogen-derived cost layer is applied to all four pipeline technologies** (H2, CO₂, methanol,
gasoline). The code labels this a "temporary generalized pipeline cost-and-capacity assumption".

## 10.2 Source

The H2 pipeline cost workbook is a [controlled project input](../data-sources/controlled-inputs.md), not a public dataset: worksheets *Normalized CAPEX*, *Normalized Variable OPEX*
and *Normalized Fixed OPEX* with pipeline inner diameter (inch), annual capacity (t H₂/year) and cost in CAD 2020 per km. **[cite-data]** The engineering sources behind it are not recorded.

## 10.3 Silver transformation

### C1. Normalization (`capacity_costs`)

Validate the three worksheets, rename columns to canonical names, merge them into one dataset with model metadata, and
export `h2_pipeline_normalized_capacity_costs.csv` to `data_files/processed/costs/transport/pipelines/h2_pipeline/`.

### C2. Fit and select (`cost_models`)

1. **Fit** linear and power-law functions of capacity to each cost component (CAPEX, fixed OPEX, variable OPEX) with
   regression diagnostics (R², RMSE, MAE) computed in the original cost units.
2. **Select** a model form per component. Defaults:

   | Component | Form |
   |---|---|
   | CAPEX | **power** (`cost = coefficient × capacity^exponent`, per km) |
   | Fixed OPEX | linear |
   | Variable OPEX | linear |

3. **EOS template** (`build_h2_eos_template`): convert the CAPEX power model to a piecewise-linear cumulative
   cost curve.
   - Default **5 segments**, **log** spacing (`DEFAULT_EOS_SEGMENT_COUNT`, `DEFAULT_EOS_SPACING`).
   - The first segment runs from zero capacity at zero cost to the smallest observed capacity, so a corridor can
     remain unbuilt. Temoa's EOS formulation requires one segment to be active, so a zero-build segment is required.
   - The remaining breakpoints span the observed capacity range, and the cost at each breakpoint is the power-model
     value (per km). Contiguity and monotonicity are validated.
4. **OPEX template** (`build_h2_pipeline_opex_coefficient_template`): export the fixed and variable OPEX
   slope and intercept as a two-row coefficient table.

Outputs: `h2_pipeline_cost_model_selection.csv`, `h2_pipeline_eos_template.csv`, `h2_pipeline_opex_coefficients.csv`.

## 10.4 How Gold uses the templates

(See also [11-gold-technology-cost-scenario.md](12-gold-technology-cost-scenario.md).)

1. **Stack** the curve `eos_stack_counts[tech]` times, end to end (`stack_pipeline_eos_template`), to raise the
   capacity ceiling without adding an integer "pipeline count" decision. The sample baseline uses 1 curve for every
   pipeline technology; CO₂ scenarios use 2 or 3 for `CO2_PIPE`.
2. **Scale by distance.** For each candidate corridor and pipeline technology, per-km CAPEX bounds are multiplied by
   the edge's CAPEX cost distance (physical or impedance-weighted, per `impedance_scope`; chapter 6).
3. **Write** `cost_invest_eos` rows. Ordinary `cost_invest` rows for pipelines are removed to avoid double counting.
4. **OPEX:** the linear slopes times the scope-selected distance become `CostFixed` and `CostVariable` rows. The
   regression **intercepts are not encoded**, because Temoa multiplies `CostFixed` by installed capacity and `CostVariable` by
   activity. The intercepts are retained in row notes.

## 10.5 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Hydrogen pipeline costs apply to CO₂, methanol and gasoline pipelines. | CO₂ (dense-phase, different materials and pressures), liquid fuels (different pumping) and H₂ differ greatly in real cost. Results for non-hydrogen pipelines are indicative only. **[cite-method]** **[cite-data]** |
| 2 | Cost scales as a power law in capacity (CAPEX) and linearly (OPEX). | Fit quality should be reported per component (R², RMSE). **[cite-method]** |
| 3 | The EOS curve is a 5-segment piecewise-linear approximation with log-spaced breakpoints. | Approximation error between breakpoints. Segment count trades fidelity against solve difficulty (more integer variables). **[cite-method]** |
| 4 | Capacity beyond the observed range is only reachable by stacking whole curves. | Extrapolates by repetition, not by modeled cost. |
| 5 | CAPEX is scaled by distance linearly (per-km cost × km). | No terrain, diameter-dependent routing cost, or compression stations as separate items. |
| 6 | OPEX intercepts are dropped. | The fixed-per-km offset is lost; only the slope remains. |
| 7 | Costs are in CAD 2020 per km. | No escalation to the model year. Currency basis versus the other Temoa costs is not reconciled (chapter 9, assumption 8). **[confirm]** |
| 8 | Truck technologies have zero investment cost on road-enabled edges. | Road construction is not modeled, and truck fleet capital is not represented. The README lists truck costs as incomplete. |

## 10.6 Validation

Column and sheet checks in C1, regression-array and monotonicity checks in C2, and Gold's
`validate_h2_eos_template`, `validate_h2_opex_coefficients` and `validate_generalized_pipeline_cost_layer`.
