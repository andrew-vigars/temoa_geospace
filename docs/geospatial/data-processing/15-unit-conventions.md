# 15. Unit conventions

*Code: unit strings written by [schema/build.py](../../../src/geocanoe/schema/build.py) and the cost modules;
`geocanoe.diagnostics.input.units` (the unit gate); shared economic conversion in
`geocanoe.costs.economics`. Temoa setting: `check_units` in the selected solver TOML.*

> **Draft status.** Conventions are in [conventions](../../conventions.md). This chapter lists the units **as encoded**. Where the encoded label and the
> data disagree, that is flagged rather than corrected. It is the chapter most in need of author input.

## 15.1 Why this chapter exists

The model combines quantities from many sources: tonnes per year of fuel and CO₂, MWh-scale electricity, costs in several currencies
and bases, and distances in kilometres. GeoCANOE's generated starter solver
configuration sets `check_units = false`,
so nothing in the solve enforces consistency. The only automated check is the conservative diagnostic gate in [chapter 14](14-diagnostics-and-analysis.md),
which tests that units are present, parseable and in a plausible category.

## 15.2 Spatial and temporal conventions

| Quantity | Convention | Where |
|---|---|---|
| Distances | kilometres. Graph edge length is geodesic between centroids ([4](04-basemap-and-graph.md)); impedance lengths are planar in EPSG:3347 ([6](06-pipeline-impedance.md)) | `distance_km` columns |
| Areas | km² for density and thresholds, m² inside overlays | Silver |
| Projected CRS | EPSG:3347 (Statistics Canada Lambert) for all metric work. EPSG:4326 for stored lon/lat and road output | Silver |
| Time | One optimisation period 2025–2050 (25 years) and one annual representative year. Renewable Silver retains 8,760-hour source profiles and monthly diagnostics, but Gold uses annual capacity factors. Annual quantities are **not** multiplied by period length; cumulative targets are divided by it | [12](12-gold-technology-cost-scenario.md) |
| Region names | Nodes `R0…Rn`; exchange regions `Ri-Rj` (node names cannot contain `-`) | [11](11-gold-spatial-mapping.md) |

## 15.3 Physical quantities as encoded

| Quantity | Encoded unit | Table | Notes |
|---|---|---|---|
| Gasoline demand | `t/year` | `Demand` | Derived from litres × 0.00074 t/L ([8](08-gasoline-demand.md)) |
| CO₂ available for capture | `t CO2e/year` | `LimitCapacity` (`CO2_CAP`) | Source kt CO₂e/year × 1,000 |
| Geological storage target | `t CO2e` over the period, encoded as annual equivalent | `LimitActivity` (`CO2_INJECT`) | Target ÷ period years |
| Electricity potential | **not labelled** (`units` is null) | `LimitCapacity` (`ELC_GEN`) | Source column `max_elec`, median about 300,000. **[confirm]** unit and basis |
| Renewable capacity/activity | MW / MWh per year | `CostInvest`, `CostFixed`, `Efficiency`, `LimitCapacity`, `LimitAnnualCapacityFactor` | Wind and PV contracts require MW, MWh/year and 8,760 MWh/(MW year) |
| Pipeline capacity | t of commodity per year (implied by the H₂-derived curve: t H₂/year) | `cost_invest_eos` | Applied to all pipeline commodities |
| Conversion efficiencies | dimensionless ratios per unit of output; **basis not stated** | `Efficiency` | E.g. `GSL_PLANT` takes 200 of `h2` per unit `gsl`. **[confirm]** |

## 15.4 Cost quantities as encoded

| Cost | Encoded unit label | Representative value | Table | Observation |
|---|---|---|---|---|
| `ELC_GEN` variable cost (LCOE) | `M$/MWh` | median 0.0385, range 0.019 to 0.34 | `CostVariable` | These values look like **$/kWh** (about 3.9 ¢/kWh median). If they were M$/MWh, the median would be $38,500/MWh. **[confirm]** |
| `CO2_CAP` variable cost | `M$/t` | 50 | `CostVariable` | If M$/t, this is 50 million dollars per tonne. Likely a different unit, such as $/t. **[confirm]** |
| `GSL_BACKUP` variable cost | `M$/MWh` | 500,000 | `CostVariable` | A penalty value. Unit as above is inconsistent with a per-tonne demand. **[confirm]** |
| `ELC_GEN`, `CO2_CAP` investment | not labelled | 1,000 | `CostInvest` | Fixed constant |
| Truck variable cost | `M$/unit` | 0.01 per km (+ intercept) | `CostVariable` | Currency and basis unstated |
| Electricity transmission | not labelled | 0.0052 per km + 2.7896 | `CostVariable`, `cost_invest_eos` | Currency and basis unstated |
| Pipeline CAPEX | CAD 2020 per km (in the template), scaled to link cost | from fitted curve | `cost_invest_eos` | Currency year 2020, Canadian dollars |
| Pipeline fixed OPEX | `CAD2020/(t capacity/year)` per km | from linear fit | `CostFixed` | Per unit of installed capacity |
| Pipeline variable OPEX | `CAD2020/t` per km | from linear fit | `CostVariable` | Per unit of activity |
| Plant and transmission EOS CAPEX | not labelled | analytic `a · q^b` curves | `cost_invest_eos` | Parameters inherited |
| Renewable CAPEX and fixed O&M | CAD2020 dollars per MW and CAD2020 dollars per MW-year | selected ATB 2025 rows | `CostInvest`, `CostFixed` | USD2023 is converted by the shared economic policy; selected rows and conversion basis are checksummed |

The labels `M$/MWh`, `M$/t` and `M$/unit` appear to be a **single scale tag copied across rows**, while the pipeline rows use a different convention (CAD 2020, per tonne of capacity or throughput).
Whether the node costs, truck costs and pipeline costs are in the same currency and scale determines whether the objective function adds commensurate quantities.

## 15.5 Currency

- `registry/economics.toml` declares **CAD2020 dollars** as the target for supported conversions: source-year FX first, then the Canadian GDP implicit price index.
- Renewable ATB costs use that converter and preserve source rows, factors and provenance in the Silver contract and Gold manifest.
- Pipeline costs are passed through as already converted **CAD2020** values from the H₂ workbook; the registry explicitly prevents a second conversion, but the workbook's embedded conversion has not been independently reconciled.
- The source of the LCOE currency (CAD or USD, which year) is not recorded ([9](09-emissions-and-site-inputs.md)).
- Discounting uses a 3 % social rate and a 3 % loan rate ([12](12-gold-technology-cost-scenario.md)), treating all cost streams as the same real-dollar basis.
- Legacy LCOE, capture, truck and analytic EOS constants are not automatically converted merely because the shared converter exists.

## 15.6 What would settle this

1. Extend the declared CAD2020 basis and auditable conversion table to every inherited source.
2. A units table in `registry/` that Gold reads and writes into the `units` column, replacing literals in `build.py`.
3. Turning on Temoa's unit checking, once labels are consistent. The existing unit gate already classifies units conservatively.

## 15.7 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Unit labels are metadata only; Temoa does not read them with `check_units = false`. | A wrong label cannot affect results, but it also cannot be caught by the solver. A wrong *value* can. |
| 2 | New renewable costs and the pipeline pass-through declare CAD2020; inherited costs are still treated as if commensurate. | Any remaining mixed bases silently mis-weight technologies in the objective. |
| 3 | LCOE values appear to be in a different unit than labelled. | If the objective is in dollars per kWh-scale units while pipeline costs are CAD 2020 per tonne, relative costs are off by orders of magnitude. **[confirm]** before reporting any cost result. |
