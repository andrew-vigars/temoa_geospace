# Quickstart

This is the shortest path through the GeoCANOE workflow. It assumes the
required raw inputs are already available or will be acquired explicitly for
the selected build profile.

## 1. Install GeoCANOE

From the repository root:

```powershell
python -m pip install -e .
```

Use `python -m pip install -e ".[dev]"` when running tests, notebooks, linting,
or type checking.

## 2. Choose a build profile

Start with `config/build_profiles/sample_build_profile.toml`, or use
`config/build_profiles/sample_renewables.toml` for the Ontario renewable
example. A build profile fixes the study area, spatial resolution, Silver
processing choices, and optional renewable representation.

## 3. Acquire the required Bronze inputs

Bronze stages can involve large or controlled sources. Select only the inputs
required by the chosen profile:

```powershell
geocanoe-build-bronze --help
geocanoe-build-bronze --stages <stage> [<stage> ...]
```

Review [Bronze acquisition](geospatial/data-processing/03-bronze-acquisition.md)
and the [data-source catalogue](geospatial/data-sources/index.md) before running
large ERA5, ATB, NHN, or snapshot acquisitions.

## 4. Build Silver

```powershell
geocanoe-build-silver --config config/build_profiles/sample_build_profile.toml
```

Silver writes durable spatial and tabular products under
`data_files/processed/`. Renewable profiles can be built independently with
`--stages renewables_atlite` when enabled by the selected profile.

## 5. Build Gold

```powershell
geocanoe-build-schema --config config/build_profiles/sample_build_profile.toml --scenario registry/scenarios/baseline.toml
```

Use the exact database path printed by the build. Gold filenames include the
resolved configuration identity and an invocation barcode.

## 6. Configure and run Temoa

```powershell
geocanoe-init-temoa-config
geocanoe-run --database <printed-gold.sqlite> --config config/temoav4/config_sample.toml --non-interactive
```

The run wrapper archives the input database, manifest, effective solver
configuration, environment and solved outputs in a timestamped directory under
`output_files/`.

## Next steps

- [Get Started](get-started.md) explains installation and configuration choices.
- [Model architecture](what-is-geocanoe/model-architecture.md) explains what each layer owns.
- [Data Guide](data-guide/index.md) documents sources, assumptions and final products.
- [Structural sensitivity runs](model-runs/structural-sensitivity/index.md) records why alternative model structures were tested.
