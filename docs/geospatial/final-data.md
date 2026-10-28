# Geospatial Final Data

GeoCANOE's geospatial workflow produces reusable Silver evidence and one
scenario-specific Gold database. Exact paths include build identities derived
from the selected profile and source configuration.

## Silver products

| Product family | Typical location | Gold use |
|---|---|---|
| Basemaps | `data_files/processed/basemaps/` | Model nodes, province membership and point assignment |
| Graph | `data_files/processed/graph/` | Candidate exchange-region topology and physical distance |
| Roads and connectivity | `data_files/processed/nrn/`, `road_connectivity/` | Truck-eligible links |
| Pipeline impedance | `data_files/processed/pipeline_impedance/` | Effective pipeline cost distance |
| Gasoline demand | `data_files/processed/gasoline_demand/` | Regional annual demand rows |
| Facility emissions | `data_files/processed/emissions/` | Regional CO₂ capture availability |
| Geological storage | `data_files/processed/co2_storage/` | Injection eligibility and optional P50 bounds |
| Cost templates | `data_files/processed/costs/` | Pipeline EOS and operating-cost rows |

Silver manifests and validation summaries are part of the model evidence. A
file without its compatible profile, source identity and manifest should not be
treated as a reproducible input.

## Gold database

Gold is written under `data_files/processed/schema/` as a fingerprinted SQLite
database with a sidecar manifest. It contains:

- Temoa regions and exchange regions;
- commodities, technologies and efficiencies;
- demand, capacity and activity limits;
- investment, fixed and variable costs;
- scenario-selected policy and transport structure; and
- optional renewable and carbon-accounting tables.

`verify_exported_sqlite` reopens the file and validates the installed Temoa
schema contract before the build is reported as successful.

## Solved outputs

Solved databases, logs, manifests, diagnostics, Excel exports and maps belong
under `output_files/<timestamped-run>/`. See
[Temoa execution and provenance](data-processing/13-temoa-execution.md) and
[Diagnostics and analysis](data-processing/14-diagnostics-and-analysis.md).
