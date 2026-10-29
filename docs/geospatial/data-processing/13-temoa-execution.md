# 13. Temoa execution and provenance

*Layer: Solve. Code: [execution/run.py](../../../src/geocanoe/execution/run.py), [execution/batch.py](../../../src/geocanoe/execution/batch.py),
[execution/temoa_backend.py](../../../src/geocanoe/execution/temoa_backend.py), `execution/temoa_config.py`.
CLI: `geocanoe-init-temoa-config`, `geocanoe-run`, `geocanoe-batch`. Configuration: generated/local solver TOMLs and `config/batch_profiles/*.toml`.*

> **Draft status.** Conventions are in [00-conventions.md](../../conventions.md).

## 13.1 Purpose and role

This is the only stage in which **Temoa itself runs**. Everything GeoCANOE does here is wrap the solve in
reproducibility and bookkeeping. The model is never changed after Gold.

## 13.2 Single run (`geocanoe.execution.run`)

1. **Resolve inputs.** A Gold SQLite and a Temoa TOML are selected interactively
   or supplied with `--database` and `--config` (`--non-interactive` requires both).
2. **Create an isolated run directory** `output_files/<timestamp>_<schema-tag>/`.
3. **Archive inputs.** Copy the effective TOML, the immutable input database (`input_<name>.sqlite`) and its schema manifest.
   A separate working copy is what Temoa writes into.
4. **Write the manifest** (`manifest.json`): timestamp, entry point, execution mode, environment, Git record
   (`get_git_record`), SHA-256 file records (`sha256_file`), and the installed Temoa distribution record
   (`distribution_record`: version, source URL, VCS commit, editable flag).
5. **Pre-solve diagnostics** (`--diagnostics off|report|strict`): see chapter 14. In `strict` mode a failed gate stops the run
   with status `validation_failed`.
6. **Solve** via `run_temoa`, which builds a `TemoaConfig` from the TOML and calls `TemoaSequencer(config).start()`. Temoa's console and file
   logging are configured explicitly because the CLI that normally does so is bypassed.
7. **Archive the solved database** (`solved_<name>.sqlite`), export `Output*` tables to Excel, extract objective values, run
   post-solve diagnostics, and finish the manifest (status, return code, wall time, errors).

## 13.3 Temoa configuration

`geocanoe-init-temoa-config` materializes the tutorial configuration owned by
the installed Temoa package, then applies GeoCANOE's explicit run contract:
the EOS extension, Gurobi, a 1% MIP gap, a 0.5 GB node-file threshold,
`save_duals = false`, `check_units = false`, and placeholder database paths.
Its default destination is `config/temoav4/config_sample.toml`; that directory
is generated locally and is not present in the updated checkout.

| Setting | Value | Meaning |
|---|---|---|
| `extensions` | `["eos"]` | Enables the economies-of-scale extension |
| `solver` | `gurobi`, `MIPGap = 0.01`, `NodefileStart = 0.5` | Commercial MILP solver; run-local node-file directory is added when needed |
| `check_units` | `false` | Unit checking disabled (see chapter 12, assumption 4) |
| `input_database`, `output_database` | placeholders | The run wrapper rewrites both to its isolated working database |

Other sections and defaults remain those of the installed Temoa tutorial asset.
Gold scenarios and solver configurations are independent: a scenario TOML
controls Gold construction, while a solver TOML controls execution. A generated
solver config can be paired explicitly with a compatible Gold database; a Gold
rebuild does not require editing the config when `--database` is supplied.

## 13.4 Batch runs (`geocanoe.execution.batch`)

A batch TOML lists `[[runs]]` (config path, `enabled`) and options `continue_on_failure`, `skip_completed`, `retry_failed`.
Each run is a **separate subprocess** calling `python -m geocanoe.execution.run`, so one failure cannot corrupt another. Status is written
incrementally to `output_files/batches/<name>_<timestamp>/batch_manifest.json`, `batch_results.csv` and `batch.log`.
The checkout currently retains two historical batch profiles under
`config/batch_profiles/archive/`; there is no active `batch_run.toml`. Create or
restore a batch only after its database and solver-config paths are verified.
`skip_completed` and `retry_failed` control how an individual batch handles
existing results and failures.

## 13.5 What Temoa does and does not do here

| Temoa does | GeoCANOE decided beforehand |
|---|---|
| Builds the LP/MILP from the database tables | Which regions, edges, technologies and parameters exist |
| Chooses build and flow decisions, including the piecewise-EOS segment per corridor | The cost curves, distances, limits and demands |
| Applies discounting and annualization with the configured rates | The rates themselves (`model.toml`) |
| Returns `Output*` tables and logs | |

## 13.6 Assumptions and limitations

| # | Assumption | Consequence |
|---|---|---|
| 1 | Perfect foresight with a single period. | No investment staging or path dependence. |
| 2 | MIP gap of 1 %. | Reported objective may be up to ~1 % from the true optimum. The README advises documenting solver, gap, time limit and termination status. |
| 3 | Temoa writes to a run-local working database. | `geocanoe.execution.run` archives the immutable input, copies it to `working_<name>.sqlite`, and rewrites the archived config to that path. The source Gold file is not written. |
| 4 | Temoa is the installed v4 package, currently from a personal fork pending an upstream PR. | The manifest records version and commit. State this in any publication. |
| 5 | Gurobi is a commercial solver. | Reproduction requires a licence, or a different solver with different tie-breaking. |

## 13.7 Tests

`tests/test_temoa_backend.py`, `tests/test_temoa_configs.py`,
`tests/test_execution_contracts.py`, `tests/test_run_archives.py` and
`tests/test_cli_contracts.py`.
