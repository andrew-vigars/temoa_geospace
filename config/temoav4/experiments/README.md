# Gold experiments

The Gold configurator creates a new directory under `config/temoav4/experiments/`
for each experiment:

```powershell
.venv/Scripts/python.exe scripts/configure_gold.py
```

Each experiment contains scenario TOMLs, a Markdown report, a JSON plan,
configuration/source snapshots and, after Gold preparation, logs, reviewed
exclusion audits and an inventory. Tab 3 generates named TEMOA run selections
under `temoa_runs/`, each with solver configs, a batch TOML and Markdown/JSON records.

Bundles are local and ignored by Git. Retain them with your research outputs.
Gold databases still use the Gold builder's existing artifact directory; their
paths and fingerprints are recorded in the experiment. The TEMOA batch runner's
working database and output_files behavior is unchanged.

Existing bundles were relocated from the repository-root `experiments/` folder.
`relocation_originals.zip` preserves the original files byte for byte;
`relocation.json` records the move and updated operational paths and hashes.
Frozen source snapshots and build logs retain their original contents.
