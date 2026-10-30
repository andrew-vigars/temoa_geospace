# Conventional gasoline price mapping: draft plan

Status: Bronze, Silver, and Gold price mapping are implemented. Gold validates
the Silver price contract, maps prices by demand-region province, replaces the
backup penalty, fingerprints price evidence, and exports a regional price audit
with national blend bounds. See `docs/canoe_fuel_silver.md` and
`docs/synthetic-gasoline-share.md` for current commands and assumptions.

Completed preparation: the temporary cache-sync helper has been replaced by a
pinned GeoCANOE Bronze import. Canonical Silver uses native EIA observations
and shared economics, records a controlled HHV assumption, and covers all 13
jurisdictions under an explicit common-US-price proxy policy. Actual upstream
function calls remain in a separate pinned reference command. The investigation
below is historical context; its preparation and Gold implementation steps
have been carried out.

## How the initial Silver reference data was produced

The GeoCANOE adapter calls the actual sibling `../canoe-fuel` repository;
it does not contain copied implementations of its pricing functions.

1. `tmp/sync_canoe_eia.py` used CANOE_CLOUD acquisition and transformation
   functions to obtain EIA AEO 2025 Reference Table 3. It verified the response
   row count and scenario and checked that the shared Silver Parquet matched
   the acquired records. It exported those data to canoe-fuel's expected
   `cache/dataframes.pkl`, with checksums and provenance in
   `cache/dataframes.source.json`. This copied data, not pricing code.
2. `src/geocanoe/preprocessing/fuel_supply.py` imports
   `canoe_fuel.common`, `canoe_fuel.setup`, and `canoe_fuel.costvariable`
   from the configured sibling repository. It validates the upstream TOML,
   calls `build_cost_frame`, selects transportation gasoline (`T_gsl`), and
   calls the upstream private `_calc_cost` function.
3. GeoCANOE writes its own CSV, Parquet, source-observation extract, and
   provenance manifest. It does not run the upstream database aggregator.
   The adapter is a standalone command, not part of every Silver build.

The current output records upstream commit
`89a07cfd3925ed20f82a7d804a142af6ce82e183`. Inspection on 2026-10-30
confirmed that HEAD, all three recorded module hashes, the upstream TOML hash,
and the cache hash match the manifest. The sibling checkout has modified
cache data and additional cache provenance/backup files, but no tracked code
modifications reported by Git.

Upstream uses five-year period ends. GeoCANOE explicitly selects its terminal
year, 2050, for the whole representative [2025, 2050) period. The resulting
price is 24.689867964860024 in the adapter's labelled `2020 MCAD/PJ` units,
derived from a source observation of 21.855738 in `2024 $/MMBtu`.
Ten province rows repeat this common US-derived series; these are not
independent provincial price observations. NLLAB and PEI are upstream codes;
NT, NU, and YT are missing. All rows are marked `review_required`.

## Implementation plan and remaining Gold work

1. **Resolve and document the price basis.** Inspect the original series
   metadata for currency, dollar year, taxes, delivery boundary, and fuel
   energy basis. Confirm a gasoline heating value consistent with that basis
   and with the model's gasoline commodity. Keep the explicit 2050 endpoint
   assumption visible; do not describe it as an average over 25 years.

2. **Produce a reviewed Silver price suitable for GeoCANOE.** Preserve the
   upstream reference output. The upstream calculation multiplies the
   USD/MMBtu observation by 1.055, whereas converting to USD/GJ requires
   division by GJ/MMBtu. Its fixed FX/deflation factors also differ from
   GeoCANOE's shared source-year conversion policy. Recommend a separately
   labelled GeoCANOE-normalized price derived from the verified native EIA
   observation, using the shared economics converter for 2024 USD to CAD2020.
   Record both results and explain their difference; never reconvert an
   already converted CAD2020 price. Decide whether an upstream correction
   should also be proposed separately. Do not modify the sibling repo as an
   implicit part of this integration.

3. **Normalize energy to mass.** Store the reviewed heating value and its
   source as controlled inputs. For a reviewed price P in MCAD/PJ and heating
   value H in GJ/tonne, the numerical CAD/tonne price is P × H: the million
   dollars and million GJ factors cancel. Alternatively, convert the native
   USD/MMBtu observation to USD/tonne before the shared currency conversion.
   Export explicit units, conversion factors, source dollar year, price year,
   period, source checksums, and review status. Do not multiply unit prices
   or annual demand by period duration.

4. **Map prices to demand regions.** Normalize NLLAB to NL and PEI to PE.
   Join the province-period price to each positive-demand basemap region
   using the province assigned by the demand workflow. Retain that province
   in Gold site attributes, which currently keep only region and demand
   from the regional gasoline layer. Require one unambiguous price for every
   demand region. For a build including territories, explicitly configure
   and label a common-series proxy or obtain territorial prices; fail on
   uncovered demand rather than assigning zero or silently dropping it.

5. **Replace the backup penalty in Gold.** Replace the hardcoded 500000
   `GSL_BACKUP` variable cost and its incorrect `M$/MWh` label with the
   mapped `CAD2020/t gasoline` cost at the active period and vintage. Keep
   `ethos -> d_gsl` efficiency at one and restrict the route to demand nodes.
   Use the delivered-price boundary without adding conventional transport
   costs unless source review establishes a different boundary. Document
   taxes and embedded policy costs before combining them with emissions
   charges. The synthetic-share policy continues to cap backup activity;
   no separate existing-gasoline technology is introduced.

6. **Make builds reproducible.** Register the reviewed price artifact and
   conversion inputs as Gold dependencies, validate their status and hashes,
   include them in the schema fingerprint, and record region-price coverage
   and provenance in an audit/manifest. Gold should consume the reviewed
   artifact offline; it should not need to import canoe-fuel at build time.
   Replace the temporary acquisition helper with a maintained refresh path
   when making the upstream acquisition workflow reusable.

7. **Verify mapping and optimization.** Test conversion arithmetic and
   energy basis, currency scale, province aliases, missing/duplicate prices,
   period mismatch, and complete demand-node coverage. Verify Gold has exactly
   one backup cost per applicable efficiency index and no remaining penalty.
   Run small solver cases with conventional supply cheaper than synthesis at
   0%, 20%, and 100% synthetic requirements; check delivered shares and objective
   cost sensitivity. Build and inspect a representative spatial Gold database
   after these checks pass.

## Preparation decisions now recorded

- Canonical Silver uses GeoCANOE-normalized prices and retains the original
  upstream calculation in a separate pinned reference command.
- The controlled mass-price assumption is gasoline HHV of 45.8 GJ/t from ECCC
  (June 2024). Applying this common HHV and a gross-energy interpretation to
  EIA's projected blend remains an explicit assumption; the energy price is
  available independently. It does not change the demand density.
- Prices retain embedded US taxes and assume sector delivery is included;
  there is no tax-neutral Canadian wholesale interpretation. Gold scenario
  comparisons and additional charges must respect this declared boundary.
- Canada-wide coverage uses the explicitly configured common US series for
  territories as well as provinces. Territorial rows are flagged as proxies.

The shared economic target is CAD2020 dollars, not millions. Some inherited
Gold costs remain incompletely labelled, as documented in `docs/economics.md`
and the unit-conventions chapter. This integration must use the declared
target consistently and identify unresolved comparisons when reporting model
economics; it does not imply that every inherited cost has been audited.
