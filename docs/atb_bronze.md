# ATB Bronze acquisition

Run `python scripts/build_bronze.py --stages atb` to acquire the complete official
2025 v1.0.0 electricity summary CSV. Use `--atb-config <path>` for a separate
pinned edition/revision config. Settings are in `config/atb/electricity_2025.toml`.
No CANOE checkout, setup module, or Atlite import is required by this stage.

The source is the OEDI dataset linked from https://atb.nlr.gov/electricity/2025/data.
The release was verified against the official S3 listing (320,606,895 bytes).
Preserve original bytes under `data_files/raw/atb/2025/v1.0.0/ATBe.csv`, alongside
an acquisition manifest containing URL, edition/revision, byte count, SHA-256,
config hash, CSV columns and row count. Verified caches cause no HTTP calls.
Requests use bounded retries, streamed byte progress and atomic promotion after
CSV validation. Interrupted transfers retain a resumable staged partial file.
Do not run concurrent acquisitions into the same release directory. Global
`--overwrite` does not replace this immutable source. Upstream changes require
review and a separate pinned release/config rather than silently accepting them.

Bronze retains all technologies and scenarios. A small renewable subset belongs
in Silver, with explicit ATB selectors and selected-row audits. Do not treat
ATB resource class average CFs as Canadian profiles. ERA5 + Atlite provide those
profiles, using explicitly configured turbine curves and PV assumptions.
ATB does not automatically supply Atlite with matched power curves. Workbook
and turbine specification evidence can be separately pinned as needed; this
stage currently acquires the full summary CSV only. Currency conversions,
2025 financial/cost-driver case selection, and the local technology crosswalk
remain downstream work. Existing ERA5 Bronze CANOE-year validation and pilot
source bundles remain unchanged until their shared configuration is migrated.

Tests cover full-source retention, stage dispatch, cache reuse, corruption,
and error-page rejection without live network calls.

## Renewable and storage family subset

`python -m geocanoe.preprocessing.atb` builds an offline derived subset using
`config/atb/renewable_families_2025.toml`. Match the `technology` column exactly:
`OffShoreWind`, `LandbasedWind`, `UtilityPV`, `Utility-Scale PV-Plus-Battery`,
and `Utility-Scale Battery Storage`. Retain all original columns, classes,
scenarios, cases, recovery periods, metrics, projection years and units.
The original Bronze master remains complete and unchanged. Output is under
`data_files/processed/atb/2025/v1.0.0/renewable_families/`, with a selection audit
and source/config/output hashes. This preprocessing utility is not yet registered
in the Silver orchestrator and its output is not a model-ready parameter table.

CANOE's summary selectors are `display_name`, `scenario`, `core_metric_case`,
`crpyears`, `core_metric_parameter`, and `core_metric_variable`. GeoCANOE first
selects broader families using `technology` and retains all those dimensions,
plus `tax_credit_case`, `units`, `value`, and evidence metadata. `*` rows are
preserved and must be resolved explicitly at later model selection. Do not reuse
CANOE's ATB 2024 `Market` case filter: ATB 2025 includes `R&D`, `Exp`, and
credit-related cases; available cases differ across technology families.

ATB PV-plus-battery is its own reference system, not a ready-made wind-plus-battery
technology. Future paper-2 hybrids should link wind/PV generation with separately
parameterized battery power/energy, efficiency, duration and charging/dispatch
constraints. Avoid adding a standalone battery cost on top of an already bundled
PV-plus-battery cost. Hybrid dispatch profiles are not Atlite weather CFs.


The ATB stage now prefers compatible external CANOE_CLOUD caches via the local
`[cloud]` setting in its acquisition config. Official OEDI remains the explicit
fallback. See `docs/canoe_cloud_source.md` for archive acquisition, provenance,
edition checks and the source-selection record consumed by the family subset.
