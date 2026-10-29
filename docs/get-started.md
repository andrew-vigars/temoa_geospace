# Get Started

## Installation choices

The root `pyproject.toml` is the canonical dependency declaration.

```powershell
python -m pip install -e .
```

This installs the research workflow and pinned Temoa dependency. Add the
development extra for tests, notebooks, linting and type checking:

```powershell
python -m pip install -e ".[dev]"
```

For a reproducible environment, use the lock file as a constraint:

```powershell
python -m pip install -c requirements-lock.txt -e ".[dev]"
python -m pip check
```

The repository README documents the temporary editable sibling override for
Temoa development. Record the installed Temoa version and commit for any model
result; run manifests capture this automatically.

## Understand the configuration layers

GeoCANOE deliberately separates:

| Configuration | Purpose |
|---|---|
| `config/build_profiles/*.toml` | Study area, basemap, Silver processing and optional renewable selection |
| `config/renewables/*.toml` | ERA5/Atlite conversion, physical technologies, land exclusions and ATB selection |
| `registry/model.toml` | Global Gold-model defaults |
| `registry/scenarios/*.toml` | Scenario-specific Gold overrides |
| generated/local Temoa TOML | Solver and execution settings |
| `config/batch_profiles/*.toml` | Ordered collections of database/config runs |

See [Configuration and setup](geospatial/data-processing/02-configuration-and-setup.md)
for the complete contract and precedence rules.

## Generated data are not source code

- Bronze inputs belong under `data_files/raw/`.
- Silver and Gold products belong under `data_files/processed/`.
- Solved runs and provenance belong under `output_files/`.
- Durable explanations of modelling choices belong under `docs/model-runs/`
  or the relevant Data Guide page.

Do not commit large generated products merely to explain a result. Record the
configuration, immutable manifest identity, finding and resulting decision in a
development record instead.

## Verify the installation

```powershell
python -c "import geocanoe; print(geocanoe.__version__)"
geocanoe-build-bronze --help
geocanoe-build-silver --help
geocanoe-build-schema --help
geocanoe-run --help
```

Continue with the [Quickstart](quickstart.md) or read
[What is GeoCANOE?](what-is-geocanoe/overview.md) first.
