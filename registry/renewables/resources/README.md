# Wind reference evidence

Current production conversion reads `turbine-models==0.2.2` via its `Turbines`
API. The published PyPI wheel includes onshore data/specifications for
`2023NREL_Bespoke_6MW_170` (T1), `2023NREL_Bespoke_8.3MW_196` (T2), and
`2023NREL_Bespoke_3.3MW_148` (T3). Package version and each original CSV/YAML
hash are recorded in run identities. No adjacent checkout or runtime network
access is required. A wheel built directly from the historical commit below
omitted package data during verification, so the published release is pinned.
The local T1 files below remain historical evidence for existing bundles.

NREL turbine-models reference `2023NREL_Bespoke_6MW_170`, T1:
6 MW, 170 m rotor, 115 m hub. Snapshotted from the Notebook 21 source evidence.
Upstream: https://github.com/NatLabRockies/turbine-models
Pinned source commit: e9e4ccc6a87f50c0c65d236154a2897e752c47c9.
CSV units are m/s and kW. Atlite conversion uses MW, clips at 6 MW, and appends
zero output at the final wind speed as the pilot's explicit shutdown assumption.
This curve is a physical reference scenario, not proof of an exact match to
every ATB 2025 projection year. No ATB CF uplift is applied.
