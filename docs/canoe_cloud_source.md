# CANOE_CLOUD as a preferred external source

Sister checkout: `../CANOE_CLOUD`, initially cloned at
`78aee5c080b3a31eaca76e69cf2886e5902f5998`. Upstream code is not executed by the
GeoCANOE ATB stage. No fork, credentials, or model compilation is required.

Configuration is local: `config/canoe_cloud/sources.toml`. ATB 2025 is required;
weather-dependent supply/demand remain aligned to 2018. Updating these local
requirements does not alter the contents of published Google Drive archives.
The pinned connector commit documents where the archive identifiers came from;
it is not proof of the release or revision of the mutable external archives.

## Workflow

Install optional dependencies with `pip install -e ".[cloud]"`.
Run `python -m geocanoe.acquisition.canoe_cloud --sync` to fetch the published
Silver archive into GeoCANOE Bronze and extract it locally. An existing cache
can instead be used by setting `cache_dir` to its parent of `silver/`, e.g.
`../CANOE_CLOUD/silver_cache`. This command is separate from normal Bronze runs
so it does not repeatedly download the broader lake. Archive byte hashes,
Drive IDs and extraction locations are recorded. Archive member paths are checked
before extraction. A published ID is mutable: refreshes must use a new configured
raw directory rather than overwriting a captured archive.

Run `python scripts/build_bronze.py --stages atb`. It prefers a compatible local
CANOE_CLOUD ATB Parquet table, validates the actual `atb_year` column, and preserves
original Parquet bytes in a content-addressed GeoCANOE Bronze import. No upstream
cache files are edited. Date folders identify the collection snapshot, not ATB
edition, weather year, or technology vintage. Set `target_date` to choose a snapshot;
otherwise the latest dated snapshot is selected and the imported file hash is pinned.

Missing caches or incompatible editions print an explicit fallback message and
use the pinned official ATB source. A malformed cache fails for inspection.
`data_files/raw/atb/selected_source.json` records the actual provider, source,
manifest, edition and checksum. `python -m geocanoe.preprocessing.atb` uses that
selection record and supports both CSV and Parquet sources.

Imported Silver is an external processed Bronze input, not a claim that we have
reproduced its raw-source transformations. GeoCANOE retains source hashes and the
upstream snapshot alongside the import. The official ATB fallback remains available.
No inferred match between external cache and official revision is made.

The separate renewables archive can be fetched with `--sync --renewables`, but
those profiles are not automatically accepted or used. Actual year, timezone,
spatial coverage, units and technology assumptions require a separate audit.
No upstream `run_all.py`, year-shifting script, or Gold database operation runs.
The current integration is ATB-only; other source years remain future adapters.


## Verified initial archive

The initial sync downloaded `silver.zip` (approximately 2.07 GB). Its actual ATB
column values identify edition 2024. The preferred-source check therefore selects
our already verified official ATB 2025 fallback. No upstream configuration edit
can change this captured archive. To adopt a future 2025 cache, sync it into a new
raw directory and update the configured cache path/snapshot, or import a locally
built compatible upstream Silver table. Existing downloaded archive, metadata
and source selection remain auditable. Updating every upstream source or running
all of its ingestors is not part of ATB acquisition.
