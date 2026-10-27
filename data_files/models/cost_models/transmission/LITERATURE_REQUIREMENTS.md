# Evidence needed for overhead AC transmission capacity-cost modelling

This checklist supports notebook 23. The initial target is new overhead high-voltage
AC line construction cost per route-km as a function of a documented nominal thermal
capacity and circuit configuration. Terminal and other project costs are separate.
Alberta observations are a provisional national proxy, not a national sample.

## 1. Highest priority: assign capacities to AESO designs

### Preferred evidence

Utility line-design standards, conductor manufacturer datasheets, engineering reports,
or project documents that identify:

- Exact conductor type/construction and size, with units; do not assume a bare numeric
  conductor code establishes a unique specification.
- Continuous current rating in amperes for a single subconductor, or a directly reported
  line/circuit continuous MVA rating. Distinguish continuous, seasonal and emergency ratings.
- Rating conditions: conductor temperature limit, ambient temperature, wind speed and
  direction, solar assumptions, and other stated conditions affecting the rating.
- Number of subconductors per phase and number of installed/energized circuits.
- Whether the rating belongs to one subconductor, one phase bundle, one circuit or the
  whole line. Avoid applying bundle/circuit multipliers twice.
- The voltage convention and whether the rating is nominal design capability or an
  operating/network transfer limit.

### How it enters the notebook

The existing lookup accepts sourced single-subconductor ampacity. Each reviewed row
needs `ampacity_a_per_subconductor`, `rating_source`, `rating_conditions`, and
`approved_for_exploration`. Review the notebook's bundle-summing approximation against
the source. If the source reports direct MVA instead, preserve that value separately
and adapt the lookup to support direct ratings; do not reverse-engineer an ampacity
without recording the conversion and rating boundary.

The notebook converts balanced three-phase AC ratings using
`MVA = sqrt(3) * kV * A / 1000`, applying bundle/circuit factors only where appropriate.
The selected power-factor scenario converts MVA to MW. It does not establish actual
usable transfer capacity. Choose and document one capacity basis for the cost curve;
do not mix thermal ratings with firm or network-constrained transfer ratings.

### Reject or retain only as context

- A voltage-to-MW table with no conductor/design or capacity-definition explanation.
- A system load-meeting capability increase reported as if it were a line rating.
- An emergency ampacity used as a continuous rating.
- A conductor area used as ampacity without an engineering relationship and assumptions.

Search phrases: `ACSR conductor ampacity continuous rating ambient wind temperature`,
`240 kV overhead line circuit continuous MVA rating Canada`,
`138 kV transmission conductor design standard`, `500 kV bundle conductor thermal rating`.

## 2. Highest priority: confirm AESO's cost denominator and component boundary

AESO's dashboard establishes CAD 2016 and $/km. We still need its data dictionary,
benchmarking methodology, worksheet documentation, or an explicit publisher clarification
covering the following questions:

1. Is the denominator route-km, circuit-km, energized-circuit-km or another facility measure?
2. Are component observations additive within a line, and what do `L.All_O`, `L.Labor_O`
   and `L.Material_O` include? Can categories overlap or substitute for each other?
3. What does an absent component mean: zero, not applicable, unreported, or bundled elsewhere?
4. Which line costs include ROW preparation, land rights, engineering, overheads,
   contingency, financing, escalation and taxes? Which are recorded at project level?
5. Are records estimates, final expenditures or a mix? What estimate stage applies?
6. What do repeated component records and first/second-circuit construction codes mean?
7. Can LINE_IDs be linked to parent projects so shared costs and validation dependence
   can be identified?

These answers inform `cost_assumptions.json`. Set `distance_denominator` to `route_km`
only with evidence. Do not approve `scope_confirmed_additive` merely because component
sums look plausible. A generic engineering paper cannot establish AESO-specific field
semantics; retain those unresolved if publisher documentation cannot be found.

Search phrases: `AESO transmission cost benchmarking methodology constant dollars`,
`AESO transmission facility cost reporting line unit cost circuit kilometre`,
`AESO reasonable assessment transmission cost benchmarking`.

## 3. Backup construction-cost observations

Seek project records or engineering cost tables with **both a defined capacity/design
and a cost boundary**. A useful extracted row contains:

| Field | What to preserve |
| --- | --- |
| Source | Full citation, URL, table/page and access date |
| Cost | Value, currency, dollar reference year and scale |
| Cost boundary | Line-only or package; included/excluded components |
| Cost status | Actual, approved budget, planning estimate or modeled estimate |
| Technology | Overhead/underground and AC/DC |
| Design | Voltage, circuit count, bundle count, conductor and structure |
| Capacity | MW/MVA/A value, rating boundary and operating/rating assumptions |
| Distance | Actual route length and route/circuit convention |
| Construction scope | New corridor, existing ROW, reconductoring or circuit addition |
| Location | Region, route/access/terrain description if available |
| Uncertainty | Stated range, interpretation and source limitations |

One detailed reference design is useful; several comparable capacities are needed to
inform a scale relationship. Prefer several capacities within the same technology and
cost boundary over a broad collection of incompatible $/MW values.

Sources reporting $/MWh alone require their financial assumptions and annual transmitted
energy to recover capital cost. They also require distance and rated capacity to support
$/MW-km comparisons. A renewable generation capacity enabled by a project is not
automatically the transmission facility rating. Keep such sources as contextual checks
unless the underlying quantities can be recovered.

Search phrases: `overhead AC transmission line capital cost MW km voltage`,
`transmission construction cost estimation guide single double circuit`,
`Canada transmission project cost conductor route length capacity`.

## 4. Terminal and other project costs

To compare complete scenarios with Ontario packages, seek station costs with station
configuration: voltage levels, transformer MVA/count, bays/breakers, new station versus
extension, and any reactive compensation. Preserve contingency, owner/management,
financing and other cost categories separately when reported.

Do not add a generic percentage without knowing its denominator and whether the
underlying line estimate already includes that category. A residual between an AESO
line scenario and an Ontario package remains an unexplained package difference, not a
measured terminal cost. HVAC station assumptions must not be replaced with HVDC
converter costs.

Search phrases: `230 kV substation capital cost transformer bays Canada`,
`transmission project owner contingency engineering cost estimate breakdown`.

## 5. Geographic transferability and uncertainty

These are useful refinements, not prerequisites for the first non-spatial design-cost fit.
Seek comparable designs across terrain/access conditions or cost guides that explicitly
separate their adjustments. Preserve the cost component to which each factor applies.
Do not multiply a foundation-only premium across the entire project.

For Canadian national application, prioritize evidence on access/remoteness, foundation
conditions, wetlands/water crossings, vegetation clearing, existing ROW, and regional
construction conditions. The GSC examples change voltage/material/terrain together;
their ratio does not identify a terrain premium. Observe national representativeness
limits even if a model predicts Alberta records well.

Report descriptive observed spread separately from prediction uncertainty, planning
estimate uncertainty and analyst sensitivity cases. An interquartile range or a -50% /
+100% planning range is not automatically a statistical confidence interval.

## 6. Decision sequence for the first model

1. Reconcile AESO units, scope and record identity; retain a clearly defined cost subtotal.
2. Assign documented capacities to the best-supported designs first. Unrated designs
   stay visible but do not enter capacity fitting.
3. Construct discrete design options with sample counts and observed cost spread.
4. Compare a power law with design-class alternatives and a simple baseline. Interpret
   a capacity exponent only within its technology/rating/cost boundary and observed range.
5. Validate on whole parent projects where identifiable; use whole LINE_IDs as an interim
   approach and disclose possible shared-project dependence.
6. Add separately sourced terminal/project costs, normalize dollar years using the
   repository economics policy, and compare scenarios to Ontario packages and GSC context.
7. Use backup engineering/literature tables for unsupported design ranges, especially
   500 kV, and label them as separate evidence rather than invented AESO observations.

The current economics policy ends in 2024. Ontario's CAD 2025 values cannot be rebased
with that policy until an appropriate 2025 index is acquired; do not substitute the 2024
index or silently relabel the values.

## Suggested evidence collection order

First collect a source resolving AESO denominator/scope, then a well-documented rating
source for common 138/144 kV and 240 kV conductor designs. Next collect a construction
cost guide with multiple voltage/circuit options as the independent backup. Finally
collect station/project adders and regional adjustments. This order addresses the inputs
that currently prevent capacity fitting before adding optional model complexity.
