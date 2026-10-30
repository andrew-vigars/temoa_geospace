# Conventional gasoline: maintained Bronze and Silver workflow

These layers produce a reproducible, checksummed CAD2020-per-tonne input
alongside spatial demand artifacts. Gold now consumes that validated input
for `GSL_BACKUP`; see `docs/synthetic-gasoline-share.md` for national scenarios.

## Run and verify

From the GeoCANOE repository root:

```powershell
.venv/Scripts/python.exe -m geocanoe.execution.bronze --stages fuel_supply
.venv/Scripts/python.exe -m geocanoe.preprocessing.fuel_supply
.venv/Scripts/python.exe -m geocanoe.preprocessing.fuel_supply --validate-only
```

`fuel_supply` is registered in both orchestration stage registries. The Silver
stage is independent of the geospatial build profile. Its direct command
builds prices without validating unrelated map inputs. Economic registry tables
must already be prepared as described in `docs/economics.md`.

Configuration lives in `config/canoe_fuel/bronze.toml` (source pin and release),
`config/canoe_fuel/silver.toml` (series, period, coverage and boundary),
`registry/gasoline_properties.toml` (energy/mass assumptions), and the shared
`registry/economics.toml` (source-year FX/GDP policy). Commands accept `--config`.

## Bronze

The original temporary helper used CANOE_CLOUD acquisition/Silver functions and
exported data to canoe-fuel's pickle cache. It has been removed. The maintained
`geocanoe.acquisition.fuel_supply` stage instead imports the pinned complete
CANOE_CLOUD **raw EIA response**, preserving its bytes in GeoCANOE's own Bronze
directory. It does not write into either sibling repository, import their code,
or deserialize pickle files.

The configured AEO 2025 Reference Table 3 US release was acquired 2026-10-29.
Its 2,673 rows span 2024–2050; the original request began in 2023 but returned
no 2023 observations. Validation rejects changed checksums, truncated responses,
wrong edition/scenario/table/region, missing years, and duplicate keys.

After import, the verified cache works without CANOE_CLOUD present. Corruption
and unmanifested caches fail; global `--overwrite` does not replace a pinned
release. Refresh by acquiring a complete response through CANOE_CLOUD and
configuring its path, hash, scope, and a **new** release directory. This stage
imports an acquisition; it does not run CANOE_CLOUD's network acquisition or
mutate its catalog.

## Canonical Silver

The adapter reads local Bronze and selects exact transportation motor-gasoline
series `prce_real_trn_NA_mgs_NA_NA_y13dlrpmmbtu`, requiring one finite positive
observation per release year in `2024 $/MMBtu`. For [2025, 2050) it selects the
2050 observation, 21.855738 USD2024/MMBtu, and computes:

```text
CAD2020/GJ = USD2024/MMBtu / (GJ/MMBtu) × shared USD2024-to-CAD2020 multiplier
CAD2020/t gasoline = CAD2020/GJ × gasoline HHV in GJ/t
```

The shared currency multiplier is approximately 1.00925656. The controlled
International Table convention is 1.05505585262 GJ/MMBtu; EIA gives the
approximate [Btu-to-joule conversion](https://www.eia.gov/consumption/commercial/terminology.php).
The mass HHV, 45.8 GJ/t, comes from ECCC's
[June 2024 Fuel LCA Model Methodology, Table B.1](https://publications.gc.ca/collections/collection_2024/eccc/en4/En4-418-1-2024-eng.pdf),
PDF page 100. Applying it to EIA's projected US gasoline blend, and treating
that series as gross energy, are explicit model assumptions. They are not a
verified blend-composition match. The energy-price column remains available
independently of the mass HHV assumption.

Current result: **20.90699456 CAD2020/GJ** (numerically MCAD2020/PJ) and
**957.54035071 CAD2020/t gasoline**. There is no million-dollar or period-length
factor in the per-tonne price. This is a 2050 endpoint price for the whole
representative period, not a 25-year average.

Under `data_files/processed/gasoline_demand/fuel_supply/`, outputs include:

- `eia_table3.parquet`: all original response observations in tabular form.
- `eia_gasoline_source_rows.csv`: selected native annual gasoline series.
- `gasoline_supply_prices.csv` and `.parquet`: 13 jurisdiction-period rows,
  conversion factors, source basis, units, and proxy flags.
- `manifest.json`: output checksums, Bronze provenance, configuration/code hash,
  economic evidence, physical properties, coverage, and assumptions.

Schema version 2 changes `cost` from the old upstream MCAD/PJ value to
**CAD2020/t gasoline**. Consumers must check the manifest and units.
`--validate-only` verifies output hashes, current dependencies, coverage,
CSV/Parquet agreement, complete source rows, and conversion arithmetic.

## Geography and boundary

Every jurisdiction uses the same US-derived sector-delivered proxy, not an
independently observed Canadian retail price. NLLAB/PEI aliases normalize to
NL/PE. Territories use the explicitly configured `common_us_series` policy
and are flagged in the output. The alternative `error` policy rejects included
territories. A configured subset reports missing jurisdictions in its manifest.

The declared boundary retains embedded US taxes and policy costs and assumes
delivery is included. There is no Canadian tax replacement, tax stripping,
additional conventional delivery charge, or emissions factor. This is a
scenario proxy, not a tax-neutral Canadian resource-cost estimate. Carry this
boundary into comparisons with synthetic production and added emissions charges.
The output status is `normalized_with_explicit_assumptions`.

Demand and its 0.00074 t/L density are unchanged. The mass HHV neither changes
demand density nor introduces a blend model.

## Actual upstream code remains available as a reference

The initial adapter called the actual sibling repo's configuration validator,
`build_cost_frame`, and `_calc_cost`; their pricing implementations were not
copied. These calls are retained in a separate command:

```powershell
.venv/Scripts/python.exe -m geocanoe.preprocessing.fuel_supply_reference
```

`config/canoe_fuel/reference.toml` pins upstream commit
`89a07cfd3925ed20f82a7d804a142af6ce82e183`, configuration, and module hashes.
The reference command now feeds **GeoCANOE Bronze** to those actual functions,
without using or changing canoe-fuel's cache. It requires that sibling repo and
its pinned canoe-schema dependency; canonical Silver does not. Run it in a
separate Python process to isolate upstream imports.

Outputs under `upstream_reference/` remain `review_required` and reproduce
24.68986796486 MCAD2020/PJ. Its difference from canonical 20.90699455704
reflects the upstream multiplication by 1.055 instead of division and its fixed
FX/deflation factors. GeoCANOE neither changes upstream code nor reconverts
that already converted output. Canonical Silver intentionally uses native EIA
observations and shared economics instead. Gold consumes only canonical Silver,
not this upstream reference.
