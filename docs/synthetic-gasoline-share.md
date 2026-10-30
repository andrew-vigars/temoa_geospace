# National minimum synthetic gasoline share

Scenario overlays can set:

```toml
[synthetic_gasoline]
minimum_national_share = 0.20
```

The setting is a fraction in [0, 1], not a percentage in [0, 100]. The default
and `registry/scenarios/synthetic_share_baseline.toml` set 0.0. This baseline
imposes no minimum and does not prohibit economic synthetic production.
`registry/scenarios/synthetic_share_20.toml` is a 20% example.
`registry/scenarios/synthetic_share_100.toml` requires 100% synthetic supply,
setting conventional backup supply to zero. Other scenario
settings continue to inherit the model defaults unless explicitly overridden.

The policy applies to aggregate **modeled** gasoline demand, not a minimum at
each city. A regional subset build therefore applies to that subset, rather
than inferring demand for the rest of Canada. The optimizer chooses locations.

For the current closed gasoline supply system, the policy caps
GSL_BACKUP output at:

    conventional annual gasoline <= (1 - share) * total annual d_gsl demand

The balance must be supplied by synthesis. The implementation uses a standard
Temoa v4 technology group and global limit_activity row, without changing the
optimizer. It avoids crediting synthetic production lost in transport.
Existing unrelated activity constraints are preserved. New conventional supply
technologies must be included in this accounting when introduced.

Inputs and limits remain annual tonnes/year. Do not multiply by 25 for the
single [2025, 2050) period. Shares are on the common gasoline mass basis;
they are equivalent to energy shares only for identical fuel heating values.
Different fuels or blends require an energy-based extension.

This is a deployment requirement, not a regulatory carbon-intensity standard
or an emissions-reduction claim. Carbon and storage policies remain independent.
High shares can be infeasible if CO2, production or delivery capacity is
insufficient. No automatic relaxation is applied.

The setting enters the Gold configuration and schema fingerprint. Rebuild the
Gold database using the desired scenario before solving. Gold now maps the
validated Silver conventional price to `GSL_BACKUP` by each demand region's
province, replacing the old 500000 penalty. Prices are in CAD2020/t gasoline;
there is no period-length multiplier or additional conventional delivery cost.

Conventional gasoline is supplied only through `GSL_BACKUP`. The former
`GSL_EXISTING` technology and `[legacy_gasoline]` configuration have been
removed. Rebuild Gold databases to apply the removal; previously built
artifacts retain their original technologies and costs.

## National configurations

The overlay paths below describe local, ignored run configurations. Only the
model defaults and canonical baseline/net-zero scenarios are tracked. Create
or modify local overlays as needed; tests generate their own scenario inputs.

Use the all-13-jurisdiction build profile
`config/build_profiles/canada_renewables_t4_gcc.toml` with one of:

| Scenario overlay | Minimum synthetic share | Maximum conventional share |
|---|---:|---:|
| `registry/scenarios/synthetic_share_baseline.toml` | 0% | 100% |
| `registry/scenarios/synthetic_share_20.toml` | 20% | 80% |
| `registry/scenarios/synthetic_share_100.toml` | 100% | 0% |

For example:

```powershell
.venv/Scripts/python.exe -m geocanoe.schema.build --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/synthetic_share_20.toml
```

`[conventional_gasoline].silver_config` in `registry/model.toml` selects
`config/canoe_fuel/silver.toml`; a scenario may override this path to use another
explicitly built Silver price artifact. Gold checks source/output hashes,
normalization status, units, coverage, and the exact 2025–2050 horizon before
using it. Unpriced demand nodes, ambiguous prices, and period mismatches fail.
The price-artifact identity participates in the Gold fingerprint.

Current national annual demand is 31,007,659.82 t across 44 demand nodes. The
20% case therefore requires at least 6,201,531.964 t/year synthetic delivery
and caps conventional supply at 24,806,127.856 t/year. This is an aggregate
accounting requirement, not a physical blending-process technology or a
city-level specification. Economic production may exceed the minimum.

Each Gold database gets a `.gasoline_prices.csv` regional audit. Its manifest
records the audit checksum, Silver evidence, covered provinces, demand nodes,
annual demand and national policy bounds. The current common proxy is
957.54035071 CAD2020/t gasoline; embedded US taxes and the Silver HHV/regional
proxy assumptions remain explicit. See `docs/canoe_fuel_silver.md`.

The first integrated national 20% build is
`gold_canada-vre_syn-share-20_c1c3599b_5ab6b326.sqlite`. Its local solver
configuration is `config/temoav4/national_gasoline/synthetic_share_20.toml`.
The database and configuration have been validated; the full national model
has not been optimized as part of this integration. This rebuild retains the
previous national model's exclusion of the same four offshore CO2 snap
outliers (facility IDs 10142, 10284, 10354, 11599), preserving its annual
point-source baseline of 280,107,240 t CO2e/year.
