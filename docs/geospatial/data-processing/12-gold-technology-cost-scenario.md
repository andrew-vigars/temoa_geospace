# 12. Gold: technologies, costs and scenarios

*Layer: Gold. Code: [schema/build.py](../../../src/geocanoe/schema/build.py) (`rebuild_technology_table`,
`rebuild_node_efficiency`, `rebuild_input_splits`, `rebuild_node_costs`, `rebuild_cost_invest_eos`,
`rebuild_storage_*`, `rebuild_static_supporting_tables`),
`geocanoe.schema.carbon`, `geocanoe.schema.renewables`,
`geocanoe.config.model_config`. Inputs: [registry/](../../../registry): `techs.csv`, `commodities.csv`,
`generation_efficiency.csv`, `transport_techs.csv`, `model.toml`, `scenarios/*.toml`.*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md). This chapter is the
> **non-spatial half of Gold**. The spatial half is [10-gold-spatial-mapping.md](11-gold-spatial-mapping.md).

## 12.1 Purpose and role in the model

Defines the **energy system structure and economics** that sit on the spatial skeleton: which technologies and
commodities exist, how they convert one to another, what they cost, and what policy limits apply. Together with
chapter 11, it produces the Temoa v4 database (the Gold SQLite).

Almost nothing here comes from a geospatial dataset. It comes from controlled CSV files, TOML settings and
constants written in `build.py`. **This is the model's parameterization, and it is the least documented part of the
repository.** Most values below need citations.

## 12.2 Configuration layering

```text
registry/model.toml (global defaults)
        + registry/scenarios/<scenario>.toml (overrides only)
        + Silver build profile
   → validated effective configuration → Gold SQLite + manifest
```

Allowed scenario sections: `[time]`, `[finance]`, `[emissions]`, `[storage]`, `[basemap]`,
`[conventional_gasoline]`, `[synthetic_gasoline]`,
`[transport_modes]`, `[pipeline_costs]`. Unknown sections or keys fail validation. Silver is never rebuilt by a scenario.

| Setting | Default (`model.toml`) | Notes |
|---|---|---|
| `time.start_year`, `end_year` | 2025, 2050 | One optimisation period of 25 years; 2050 is the terminal boundary |
| `finance.global_discount_rate` | 0.03 | Social discount rate |
| `finance.default_loan_rate` | 0.03 | Annualization of investment |
| `emissions.projection_method` | `constant` | Only supported value |
| `emissions.policy`, `price_per_tonne` | `none`, 0 | Optional facility-origin atmospheric emissions price |
| `storage.requirement` | `none` | Or an absolute cumulative target or included point-source fraction |
| `basemap` | projected, 25 km | Must match a Silver basemap that was built |
| `transport_modes` | roads and pipelines enabled; no individual exclusions | Master switches plus `disabled_technologies` |
| `pipeline_costs.impedance_scope` | `none` | See chapter 6 |
| `pipeline_costs.eos_stack_counts` | 1 per pipeline tech | See chapter 10 |

Committed scenario TOMLs are `baseline` and `net_zero`; run variants, samples,
matrix recipes and batch selections are local and ignored. The current `net_zero` comparison resolves a 75% target from
the positive point-source emissions actually retained after filtering and
snapping; 75% is a run choice, not a default or a claim of whole-system net
zero. Historical CO₂-stack variants were removed. The sweep directories now
retain design documentation rather than active scenario TOMLs. `model.toml`
defaults impedance to `none`, while the baseline uses `all_km_dependent`.

## 12.3 The energy system (what Gold writes)

From `registry/commodities.csv`, `techs.csv`, `generation_efficiency.csv` and `transport_techs.csv`:

**Commodities:** the base registry includes hydrogen, methanol, captured/stored
CO₂, gasoline, electricity, gasoline demand and the `ethos` source. When
`emissions.policy = "emissions_price"`, Gold also activates the conditional
facility-carbon and atmospheric-emission commodities from `commodities.csv`.

**Node technologies:**

| Technology | Conversion (efficiency) | Constraint |
|---|---|---|
| `ELC_GEN` | `ethos → elc` (1.0) | Legacy site potential and LCOE; removed when renewable replacement is enabled |
| `WIND_GEN`, `SOLAR_GEN` | renewable resource → `elc` | Optional checksum-validated Silver replacement with annual CFs and land-derived bounds |
| `H2_PLANT` | `elc → h2` (0.01953) | |
| `CO2_CAP` | `ethos → co2` (1.0) | Capacity limit from facility emissions; cost 50 |
| `CO2_INJECT` | `co2 → co2_stored` (1.0) | Only in storage-eligible regions |
| `METOH_PLANT` | `co2` 0.714, `h2` 5.208, `elc` 5.714 `→ ch3oh` | Input split (below) |
| `GSL_PLANT` | `ch3oh` 0.444, `h2` 200 `→ gsl` | Input split (below) |
| `GSL_DEMAND` | `gsl → d_gsl` | Regions with demand |
| `GSL_BACKUP` | `ethos → d_gsl` (1.0) | Silver conventional price; regions with demand; unlimited-capacity flag |

**Transport technologies** (on edges): `H2_PIPE`, `CO2_PIPE`, `METOH_PIPE`, `GSL_PIPE`, `ELC_TRANS` (all
edges); `H2_TRUCK`, `CO2_TRUCK`, `METOH_TRUCK`, `GSL_TRUCK` (road-enabled edges). Efficiency is 1.0 on every edge.

**Input splits** (`LimitTechInputSplitAnnual`, operator `ge`): `GSL_PLANT` takes `ch3oh` 0.99778 and `h2` 0.00222;
`METOH_PLANT` takes `co2` 0.7923, `h2` 0.1087 and `elc` 0.0990.

## 12.4 Cost encoding

| Cost | Where | Value or rule |
|---|---|---|
| `ELC_GEN` variable cost | `CostVariable`, per node | Node mean LCOE (units written "M$/MWh") |
| `CO2_CAP` variable cost | `CostVariable`, per node | 50 ("M$/t") |
| `GSL_BACKUP` variable cost | `CostVariable`, demand nodes | Validated Silver province-period price, CAD2020/t gasoline (current common proxy 957.54) |
| `ELC_GEN`, `CO2_CAP` investment | `CostInvest`, per node | Fixed 1,000 each |
| `GSL_PLANT`, `METOH_PLANT`, `ELC_TRANS` investment | `cost_invest_eos`, analytic curves | Marginal cost per unit capacity `a · q^b` (b < 0, a learning/scale curve), so cumulative cost is `a/(b+1) · q^(b+1)` at each breakpoint |
| Pipeline investment | `cost_invest_eos` | H2-derived curve, per-km, distance-scaled (chapter 10) |
| Pipeline fixed and variable OPEX | `CostFixed`, `CostVariable` | H2-derived slopes × distance (chapter 10) |
| Truck variable cost | `CostVariable` | `intercept_cost_per_km + cost_per_km × distance` (0.01 per km) |
| Truck investment | `CostInvest` | **Zero** on road-enabled edges |
| Pipeline variable (legacy table) | `transport_techs.csv` | 0.0196 per km (pipelines now use the H2 layer instead) |
| `ELC_TRANS` | `transport_techs.csv` | 0.0052 per km + 2.7896 intercept |
| Renewable investment and fixed O&M | selected ATB 2025 rows | USD2023 inputs converted to CAD2020; wind may include configured grid-connection cost |

Analytic EOS curve parameters (`EOS_INVESTMENT_COST_PARAMETERS`; 5 breakpoints with log spacing from 0 to an upper volume of 10⁶). The curve mirrors the earlier `model_rules.invest_costs` logic. **[cite-method]** **[cite-data]**

| Technology | a | b |
|---|---|---|
| `GSL_PLANT` | 23,334 | −0.4 |
| `METOH_PLANT` | 4,500 | −0.3663 |
| `ELC_TRANS` | 2,000 | −0.3 |

The transmission curve is assigned per edge and scaled by the ratio of the edge's distance to the mean pipeline-link
distance.

## 12.5 Policy and limit encoding

| Constraint | Table | Rule |
|---|---|---|
| CO₂ available for capture | `LimitCapacity` (`CO2_CAP`, `≤`) | Annual facility emissions (t/yr), constant |
| Electricity potential | `LimitCapacity` (`ELC_GEN`, `≤`) | Site potential per node |
| Storage target (net-zero scenarios) | `LimitActivity` (`CO2_INJECT`, `≥`) | `minimum_cumulative_activity / (end_year − start_year)` per year |
| Point-source-fraction target | `LimitActivity` (`CO2_INJECT`, `≥`) | Fraction × included post-snap annual facility emissions; manifest records the resolved cumulative target |
| Geological storage capacity | `LimitActivity` (`CO2_INJECT`, `≤`) | Optional P50 `equal_weighted` regional budgets or one `shared` system pool, annualized over the period |
| Atmospheric emissions price | standard Temoa emission-accounting tables | Charges released facility-origin carbon; optional fallback-gasoline factor is separate |

Temoa optimises **one representative year** and repeats it across the period. Annual quantities are therefore not multiplied
by the period length. Cumulative targets are divided by it.

## 12.6 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | One 25-year period and one annual representative year. Renewable Silver retains hourly and monthly diagnostics, but Gold encodes annual capacity factors only. | Weather variability informs annual energy availability, not hourly adequacy, ramps, storage dispatch or resource-demand coincidence. **[cite-method]** |
| 2 | Conversion efficiencies and input splits are fixed constants in a CSV and in code. | Derivation (stoichiometry, plant data) is not recorded. E.g. `GSL_PLANT` hydrogen input of 200 per unit output and `H2_PLANT` efficiency of 0.0195. **[cite-data]** **[confirm]** units of each efficiency. |
| 3 | Cost constants (`a`, `b`, 1,000, 50, 500,000, 0.01, 0.0052, 2.7896) are inherited from an earlier model and are mostly unsourced. | **[cite-data]** Each needs a source or an "unvalidated legacy" label. |
| 4 | Renewable costs have an audited CAD2020 conversion and the H₂ workbook is explicitly treated as already converted, but inherited legacy node, truck and analytic EOS costs remain incompletely labelled. | The shared converter fixes new renewable inputs, not every historical cost line. **[confirm]** the basis of inherited constants before system-cost comparisons. |
| 5 | The discount rate and loan rate are both 3 %. | No sensitivity analysis is built in. **[cite-data]** |
| 6 | `GSL_BACKUP` costs 500,000 per unit, as a penalty. | It serves as a slack technology, so demand is always satisfiable. Reported backup use indicates unmet synthetic supply. **[confirm]** |
| 7 | Trucks have zero investment and no fleet capacity limits. | Truck transport capacity is unconstrained apart from per-km variable cost. |
| 8 | All four pipeline commodities share one cost curve (chapter 10). | |
| 9 | The named `net_zero` scenario currently requires storage of 75% of included point-source emissions. | It is not a whole-system emissions cap, does not include prospective DAC, and does not by itself guarantee feasibility or net zero. |
| 10 | `model.toml` and the committed baseline scenario disagree on `impedance_scope` (`none` vs `all_km_dependent`). | Baseline results include impedance on every km-dependent cost. Make this explicit wherever results are reported. |

## 12.7 Outputs

`data_files/processed/schema/gold_<build-id>_<scenario-id>_<fingerprint>_<barcode>.sqlite` and `.manifest.json`.
Tables are conformed to the installed Temoa v4 schema and the economies-of-scale extension
(`conform_tables_to_temoa_v4`), then verified after export (`verify_exported_sqlite`).

## 12.8 Validation

`validate_h2_eos_template`, `validate_h2_opex_coefficients`, `validate_generalized_pipeline_cost_layer`,
`validate_encoded_region_coverage`, renewable contract/checksum validation,
carbon-conservation validation, and `verify_exported_sqlite`. Tests include
`tests/test_schema_build_contracts.py`, `tests/test_model_config.py`,
`tests/test_carbon_policy.py`, `tests/test_renewable_output_profiles.py` and
`tests/test_renewables_silver.py`.
