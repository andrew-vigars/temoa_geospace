# Western Ontario H2 pipeline diagnosis

Run: `2026-10-29_111053_gold_canada-vre_vre-baseline_e30f3654_44b3d28b`.
Selection: `Western_ON_H2_Pipe_Anomoly`. The input evidence comes from the
diagnostic SQLite export; solver termination comes from the original run log.
No inputs, constraints, costs or solved values were changed in this analysis.

## The square is a split route, not circulation

All these flows belong to period 2025, season S, time of day D. Let
`q = 53,979.525993 t` in the model's representative year.

| Route | Direction | Flow |
| --- | --- | --- |
| Direct side | R1297 → R1296 | q |
| Other three sides | R1297 → R1435 → R1434 → R1296 | q on every side |

The truck route supplies approximately `2q = 107,959.051986` to R1297 and
removes the same quantity at R1296. The detour is carrying an additional parallel
share between the same endpoints. Nothing flows back around the square in this
solution. No H2 production occurs at these intermediate nodes in the extract.

## The northern pipes also split one transfer

The local northern transfer is `3q = 161,938.577979` from R1713 to R1569:

| Branch | Route | Flow |
| --- | --- | --- |
| Upper pipeline | R1713 → R1572 → R1571 → R1570 → R1569 | q |
| Middle pipeline, then truck | R1713 → R1712 → R1711 → R1710 → R1569 | q |
| Lower detour | R1713 → R1855 → R1854 → R1853 → R1852 → R1710 → R1569 | q |

The first link and last two links of the lower detour are trucks; its three intervening links
are pipelines. The R1710 → R1569 truck link carries `2q` after branches merge.
The pipes occupy different graph corridors, not duplicate installations on the
same corridor. Reverse `output_net_capacity` records describe shared exchange
capacity and must be collapsed with MAX, not added as two pipes.

## EoS is enabled and permits larger pipes

The archived effective configuration enables `eos`. For R1296–R1297 the capacity
breakpoints are approximately 0, 53,979.53, 128,385.67, 305,354.31, 726,259.04 and
1,727,344.83. Every active pipeline in this selected area is at the first nonzero
breakpoint. That is not a hard maximum or a minimum build size: the first
segment starts at zero. Both a 2q pipe and a 3q pipe fit within the existing curve.
EoS applies separately to each corridor; it does not impose a network-wide rule
that neighbouring routes must consolidate.

H2 pipeline efficiency is 1. No H2 pipeline capacity bound, existing capacity,
new-capacity limit or relevant activity/resource/link constraint appears in the
extract. Capacity factors and capacity-to-activity have no H2 pipeline overrides.
The relevant default values still need to be respected in an actual re-solve.

## A consolidation check favours larger, fewer pipes

Evaluate each edge's piecewise-linear `cost_invest_eos` curve at its installed
capacity. Use the actual local curve for every edge, including its distance
scaling. The following are raw CAPEX curve amounts in millions of model cost
units, before loan annualization, discounting, fixed OPEX and variable OPEX:

| Local comparison | Existing routes | Consolidated candidate | Raw CAPEX reduction |
| --- | ---: | ---: | ---: |
| Square: four q pipes versus one direct 2q pipe | 130.609 | 41.846 | 88.762 |
| Northern transfer: ten q pipes versus three middle-route 3q pipes | 327.148 | 146.363 | 180.786 |

The square candidate preserves every node's net H2 balance. The northern
candidate routes all 3q through R1713 → R1712 → R1711 → R1710, followed by a 3q
truck transfer to R1569, and also preserves net balances. It removes the upper
and lower branches.

Square pipeline variable cost also falls from about 978,147 to 488,875 per
representative year. Northern pipeline variable cost falls from about 2,450,056
to 2,205,134. Fixed pipeline cost is positive and the candidates reduce weighted
installed capacity. Northern trucking changes reduce the *declared model*
variable cost by about 13,603 per year. Legacy trucking monetary units remain
unreconciled, so this last comparison is not a validated CAD economic estimate.
The pipeline-only CAPEX comparison is sufficient to identify a consolidation
opportunity; these figures are not the discounted objective improvement.

## Why the solver could retain this pattern

The effective configuration requests `MIPGap = 0.01` (1%). The solver log records:

- incumbent objective: 861,325,422,177;
- best bound: 857,885,853,904;
- final relative gap: 0.3993%;
- one branch-and-bound node explored; the accepted incumbent was found by a heuristic.

The remaining objective uncertainty is about 3.440 billion model cost units.
Small regional improvements can fit within that gap. Gurobi's “optimal” status
here means optimal within the specified tolerance, not proof that each regional
route is the cheapest possible one.

The combination of a heuristic incumbent, identical first-breakpoint builds and
cheaper local consolidation candidates strongly suggests an incumbent-quality
artifact. This is an evidence-based explanation, not a confirmed counterfactual
re-solve. A complete original-model feasibility check and re-solve would be the
confirmation. There is no evidence here that the EoS curve prohibits larger pipes.

## Repeatable manual workflow

1. Open the SQLite extract and run `diagnostics/sql/h2_pipeline_routes.sql`.
2. Filter one commodity, period, season and time slice. Trace FROM → TO, separating
   trucks from pipelines. A closed drawing need not be a directed cycle.
3. Check node inflows, outflows, production and consumption. Include the connections
   crossing the selection boundary. Identify branches with the same source/sink.
4. Match flows to physical corridor capacity. Collapse reverse orientations with
   MAX for each vintage, then sum vintages if necessary.
5. Inspect all EoS breakpoints, not just the occupied one. Check efficiency, capacity
   conversions/factors, bounds, minimum builds, existing assets and group constraints.
6. Compare a candidate reroute using the actual curve and OPEX on each changed
   corridor. Preserve endpoint and intermediate-node balances. Check units first.
7. Read the original effective configuration and solver log. Translate MIPGap into
   an absolute cost scale; a good global gap can hide a poor local route.
8. Confirm with the full original model: fix decisions outside the affected area
   to the incumbent, retain original boundary flows, and reoptimize local routing
   and capacity with tighter tolerances. Alternatively rerun the complete model
   with a tighter gap, e.g. 0.0001 (0.01%), allowing for longer solve time. Do not
   solve the extracted network as though it were the complete original system.

For easier visual diagnosis, direction arrows and a period/time selector would
help. Those presentation improvements do not replace the table-based checks.
