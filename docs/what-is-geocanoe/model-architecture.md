# Model Architecture

GeoCANOE is organized as a staged pipeline so expensive acquisition and spatial
processing can be reused across scenarios.

```text
External sources and controlled inputs
                ↓
             Bronze
                ↓
              Silver
                ↓
   model defaults + scenario overlay
                ↓
               Gold
                ↓
        isolated Temoa run
                ↓
 diagnostics, exports and maps
```

## Bronze

Bronze captures source bytes, metadata, acquisition manifests and checksums.
It avoids modelling transformations. Rolling and immutable sources have
different refresh rules, documented in the source catalogue.

## Silver

Silver creates model-ready evidence while remaining reusable across Gold
scenarios. Products include basemaps, adjacency, roads, impedance, demand,
storage mappings, renewable profiles, capacity bounds and normalized costs.

## Gold

Gold chooses one compatible set of Silver products, merges global defaults with
a scenario overlay, and writes a validated Temoa SQLite database. The sidecar
manifest records the resolved configuration, source identities and
implementation fingerprint.

## Solve and analysis

The run wrapper copies Gold into an isolated run directory, invokes Temoa,
archives provenance, and produces diagnostics, table exports and maps. The
original Gold database is not modified.

See the complete [data-processing guide](../geospatial/data-processing/index.md)
for stage-level methodology and assumptions.
