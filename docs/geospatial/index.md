# Geospatial pre-processing

How the Geospatial-CANOE model is built: from public geospatial sources, through spatial preprocessing, to the Temoa database that the optimiser solves.
The section has two parts, mirroring the two companion guides in the CANOE electricity documentation.

```text
Bronze (raw data) → Silver (spatial + renewable processing) → Gold (Temoa database) → Temoa solve → Analysis
 chapter 3           chapters 4–10 + renewable note     chapters 11–12          chapter 13    chapter 14
```

## Part A: Data processing

[Contents](data-processing/index.md). What the pipeline does, stage by stage and domain by domain, with the assumptions made at each step and where
the line sits between GeoCANOE pre-processing and Temoa itself.

The current renewable, economic-conversion and carbon-policy implementations
are cross-cutting additions to this original sequence. See
[Renewable Silver and Gold representation](../renewables_silver.md),
[Economic conversion and provenance](../economics.md), and
[Facility-carbon accounting](../emissions-accounting.md).

## Part B: Data sources catalogue

[Contents](data-sources/index.md). Where every input comes from: publisher, licence, URLs, the fields the model uses, which chapters use them, how to update them, and how to cite them.
Includes the [controlled project inputs](data-sources/controlled-inputs.md) that are not public datasets, and [update procedures](data-sources/update-procedures.md).

## Where the line sits between pre-processing and Temoa

Temoa begins at one call, `TemoaSequencer(config).start()`, in [temoa_backend.py](../../src/geocanoe/execution/temoa_backend.py). It receives a Gold SQLite database and a Temoa TOML file.
Everything upstream, including the construction of the Gold database, is GeoCANOE pre-processing. Details in
[chapter 1](data-processing/01-overview-and-architecture.md).
