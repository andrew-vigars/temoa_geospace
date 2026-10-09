# Emissions accounting and CANOE alignment

Reviewed 2026-10-08. The `emissions_price` policy uses standard Temoa v4
input tables and does not change the optimizer.

## Evidence checked

The public CANOE provincial fuel-sector
[emission builder](https://github.com/CANOE-main/canoe-fuel/blob/main/emissionactivity.py)
creates `EmissionActivity` records indexed by region, emissions commodity,
input commodity, technology, vintage and output commodity. It reads direct
and upstream emissions factors and assigns them to matching fuel-output routes
for each province and vintage. This is the same process indexing used by
GeoCANOE's v4 `emission_activity` table.

The [fuel-sector documentation](https://canoe-main.github.io/canoe-fuel/)
describes provincial fuel delivery, unit-efficiency transfers, separate direct
and upstream emissions inputs, and a v3.1 schema. Thus the naming and schema
version differ from the installed v4 backend; emissions factors also require
unit conversion before reuse.

The installed Temoa v4 source was checked directly:

- `components/costs.py`: annual process emissions are output flow multiplied
  by `emission_activity`, then priced by `cost_emission`. Period length and
  discounting are applied to the cost.
- `components/limits.py`: `limit_emission` uses the same process emissions
  expression, alongside embodied and end-of-life components when supplied.
- `components/emissions.py`: `linked_tech` can connect captured emissions to
  a physical CO2 flow. This is an available backend mechanism, not evidence
  that a particular provincial CANOE dataset uses it.

The local `data_files/CANOE_geospatial.sqlite` has empty `EmissionActivity`,
`CostEmission`, `LimitEmission` and `LinkedTech` tables. It is a template and
cannot establish how an actual provincial scenario balances carbon, prices
emissions, or represents capture. No populated provincial scenario database
was available for this review.

## Alignment and intentional differences

GeoCANOE aligns with the process-indexed emissions interface and v4 objective
calculation. Its four small integration solves verify fixed baseline production,
price response, storage saturation, carbon conservation and cost accounting
through the unchanged backend. This verifies schema and solver behavior, not
numerical equivalence to a provincial CANOE scenario.

GeoCANOE currently models an exogenous facility baseline, rather than the
sectoral activities that generated it. A fixed `CO2_BASELINE` bookkeeping process
therefore makes uncaptured emissions unavoidable. Exact physical balances route
carbon through capture, transport, atmospheric release and geological storage.
No negative emissions coefficient is assigned to fossil CO2 storage.

Facility carbon used in fuel is counted once as eventual atmospheric release at
the CO2-consuming production process, including conversion losses. This is a
horizon-level accounting assumption, not a resolved combustion-sector model.
CANOE's direct/upstream separation should be retained when integrating sectoral
data, with an explicit allocation of each emission to avoid counting the same
carbon at both supply and combustion.

Fallback and legacy gasoline are reported separately but remain unpriced by
default, as requested. Electricity and upstream fuel emissions are outside the
initial facility-carbon boundary. The inherited facility CO2e-to-physical-CO2
proxy is also retained. These choices prevent a claim of full provincial or
whole-system emissions equivalence.

## Remaining provincial verification

A populated provincial database and its release/scenario identity are needed
to compare emissions commodity flags, direct/upstream/capture coefficients,
their units, linked capture flows, emissions prices and caps, and the resulting
annual emissions totals. In particular, the provincial treatment of synthetic
fuel combustion and fossil fallback gasoline must be checked before expanding
the present boundary.
