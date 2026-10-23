# Shared cost conversion policy

`registry/economics.toml` defines CAD2020 dollars as the target. Like CANOE's
`conv_curr`, look up CAD per source currency by the original cost dollar year,
then rebase using the Canadian GDP implicit price index:

`cost * exchange[source_year, source_currency] * GDP[2020] / GDP[source_year]`

Monetary scales are converted separately; physical units must be normalized
by the calling adapter. Model vintage and technology projection year do not
select the exchange rate. CAD exchange entries are 1.## Bronze acquisition and explicit refresh`config/economics/sources.toml` declares source locations, pinned checksums,release directory and exact GDP series selectors. The economics Bronze stagepreserves the complete unmodified StatCan ZIP and curated CANOE exchange CSV,each with an acquisition manifest under `data_files/raw/economics/<release>/`.Verified caches are reused offline. Corruption or upstream revisions failchecksum validation; adopt revisions deliberately with a new pin and directory.The global `--overwrite` flag does not replace verified economics releases.From the repository root:```powershellgeocanoe-build-bronze --stages economicspython -m geocanoe.preprocessing.economics```The second command explicitly refreshes the configured reviewable registrytables and their provenance from verified Bronze, without network access.Acquisition alone never changes model conversion inputs. Review the resultingCSV/config diff before committing, then regenerate affected model costs.Current GDP selectors and years reproduce the existing committed observations.Future sources can use a pinned `url` instead of `local_path`; adding anotherpayload format requires its own validator/adapter. Do not substitute a web FXseries for the CANOE snapshot without explicitly choosing that policy change.

The annual 2000â€“2024 series is Statistics Canada table 36-10-0223-01, Canada,
Implicit price indexes, Gross domestic product at market prices, 2017=100.
The index reference base cancels in the ratio. Source price year is independent
of technology projection year and model vintage. Missing years, unsupported
currencies, invalid factors and altered index data cause errors. Builds use
the committed snapshot without downloading changing observations.

The exchange table is a self-contained snapshot of CANOE's
`input_files/currency_exchange.csv` for 2000–2024, with CAD, USD, EUR, GBP and AUD.
Provenance and checksums are versioned alongside it. Its historical market FX
provenance has not been independently verified; 2023–2024 repeat 2022 values.
There is no runtime dependency on CANOE. This matches CANOE's lookup logic and
exchange inputs, while retaining our separately versioned StatCan GDP series;
revised index observations can therefore differ from CANOE's inflation table.
Changing either table requires explicitly regenerating affected costs.

## H2 workbook exception

The current H2 workbook embeds currency conversion and inflation adjustments.
Its exported normalized costs are already CAD2020 dollars. The source entry
`sources.h2_pipeline_workbook` explicitly records this exception. The importer
validates CAD/2020 and passes values through unchanged, rejecting attempts to
relabel them with another currency/year. Do not apply the original USD year's
conversion again. Embedded factors have not been independently reconciled.
When replacing Excel calculations in Python, recover native source costs and
their original currency/year before applying the shared converter.

## Migration scope

Renewable exports use the shared converter and retain its index and policy
provenance in their contract. Existing immutable bundles are not rewritten;
refresh output parameters explicitly to adopt the GDP method.

This does not certify legacy capture, transport, backup-fuel, inherited template
or carbon-price costs. Their source metadata and physical units still require
reconciliation before a framework-wide Gold consistency gate can be enabled.
Do not assume an unlabeled cost is CAD2020. The current unit diagnostics do not
verify economic basis. The H2 pass-through exception is not an independent
verification of workbook conversions.
