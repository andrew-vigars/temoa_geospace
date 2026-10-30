# National gasoline scenarios

Run-specific scenario TOMLs, matrix recipes, solver configs and batch profiles
are local and ignored. Only `registry/model.toml` and canonical baseline/net-zero
scenario TOMLs are tracked; the preparation script, tests and this guide are reusable.

Pair `config/build_profiles/canada_renewables_t4_gcc.toml` with
`synthetic_share_baseline.toml`, `synthetic_share_20.toml`, or
`synthetic_share_100.toml` for 0%, 20%, or 100% minimum synthetic delivery.
The geography includes all 13 provinces and territories. These scenarios
inherit the model's conventional-price source and have no additional carbon
price or storage floor unless explicitly configured.

Global defaults in `registry/model.toml` select validated Silver prices through:

```toml
[conventional_gasoline]
silver_config = "config/canoe_fuel/silver.toml"
```

`GSL_BACKUP` represents conventional gasoline delivered at each demand node.
Synthetic gasoline reaches demand through `GSL_DEMAND`. The national policy
limits combined conventional delivery, leaving at least the configured share
to synthetic delivery; it does not require the same blend at each city.

Build each desired Gold database before selecting it in a solver configuration:

```powershell
.venv/Scripts/python.exe -m geocanoe.schema.build --config config/build_profiles/canada_renewables_t4_gcc.toml --scenario registry/scenarios/synthetic_share_20.toml
```

Existing solver configurations still point at their previously built databases;
editing the share or price source does not update those databases in place.
See `docs/synthetic-gasoline-share.md` for policy details and audit outputs.

The combined 18-case gasoline/carbon matrix is defined by
`config/scenario_matrices/national_gasoline.toml`. Its overlays live in
`registry/scenarios/national_gasoline_matrix/` and its manual batch queue is
`config/batch_profiles/national_gasoline_matrix.toml`.
See `docs/national-gasoline-matrix.md` for the six carbon-policy cases and commands.
