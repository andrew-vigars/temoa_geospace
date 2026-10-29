# Glossary

| Term | Meaning in GeoCANOE |
|---|---|
| **Bronze** | Preserved external inputs and acquisition provenance |
| **Silver** | Reusable processed spatial, resource, demand and cost evidence |
| **Gold** | A scenario-specific, validated Temoa SQLite database |
| **Build profile** | TOML selecting study area, spatial resolution and Silver processing |
| **Scenario** | TOML overlay changing approved Gold-model assumptions without rebuilding Silver |
| **Model region** | A retained basemap cell encoded as a Temoa region, usually `R<number>` |
| **Exchange region** | Directed connection between two model regions, written `Ri-Rj` |
| **Artifact identity** | Hashes and normalized configuration used to distinguish generated products |
| **Capacity factor** | Available annual or hourly energy divided by rated capacity and time |
| **OCC** | Overnight capital cost before financing effects |
| **GCC** | Grid connection cost associated with a generation technology assumption |
| **EOS curve** | Economies-of-scale investment-cost representation using piecewise capacity segments |
| **Structural sensitivity** | A comparison that changes the model representation, not merely a parameter value |
| **Decision record** | Durable explanation of an observed problem, evaluated alternative and resulting modelling choice |
