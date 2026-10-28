# GeoCANOE, CANOE and Temoa

The three names refer to different responsibilities.

| Component | Responsibility |
|---|---|
| **CANOE** | The broader Canadian energy-system modelling framework and sector data ecosystem |
| **GeoCANOE** | Spatial acquisition, preprocessing, model-region construction, controlled assumptions and Gold database encoding |
| **Temoa** | The optimization model, constraints, objective, solver interface and result tables |

The operational boundary is explicit: Temoa begins when
`geocanoe.execution.temoa_backend.run_temoa` creates a `TemoaConfig` and calls
`TemoaSequencer(config).start()`.

Everything before that call—including construction of a SQLite database in the
Temoa schema—is GeoCANOE preprocessing. Everything after it is Temoa execution
wrapped by GeoCANOE provenance, diagnostics and analysis.

This distinction matters when interpreting results. Spatial connectivity,
resource availability, storage eligibility, capacity limits, policy settings
and cost curves are selected before Temoa runs. Temoa chooses investments and
operations within the encoded structure; it does not discover missing spatial
or physical alternatives.

The detailed technical boundary is documented in
[Architecture and the Temoa boundary](../geospatial/data-processing/01-overview-and-architecture.md).
