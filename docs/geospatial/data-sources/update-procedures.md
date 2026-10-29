# Update procedures

Practical checklists for changing inputs. The commands are those documented in the repository README and the workflow reference
(`scripts/model_workflow.md`). Each procedure ends with a Gold rebuild because Gold is fingerprinted: any upstream change produces a new Gold file.

## Rebuild order

```bash
geocanoe-build-bronze --stages <stage> --overwrite     # refresh raw data (only what changed)
geocanoe-build-silver --config config/build_profiles/<profile>.toml
geocanoe-build-schema --config config/build_profiles/<profile>.toml --scenario registry/scenarios/<scenario>.toml
geocanoe-diagnostics inputs                            # pre-solve gate
```

After a Gold rebuild, pass the exact new database with `geocanoe-run
--database ... --config ...`. A local solver TOML may retain placeholder paths
because the run wrapper rewrites them to its isolated working database; update
batch profiles that pin database paths.

## 1. Periodic refresh of rolling sources

Applies to NRN, NHN, Aboriginal Lands and the StatCan fuel-sales table.

1. Re-run the Bronze stage with `--overwrite` (`nrn`, `nhn`, `aboriginal_lands`, `gasoline_demand`).
2. Record the retrieval date, and the product version where the manifest provides one, in the source's profile (`[cite-data]` slot).
3. Rebuild Silver and Gold, run the input diagnostics, and compare summary tables (edge counts, road-enabled share, impedance totals, regional demand) against the previous build.

## 2. Change the study area (add or remove a province)

1. Edit `[study_area].provinces` in a copy of the build profile and choose a new `[profile].id` and `study_area.label`.
2. No new acquisition is needed for NRN (every jurisdiction is downloaded). Check that the 2021 boundary name maps to a code in `geocanoe.regions`.
3. Rebuild Silver from scratch for the new profile. Region IDs (`R0…Rn`) change, so Gold databases from other profiles are not comparable cell by cell.
4. Check gasoline demand: every jurisdiction needs fuel-sales rows for `sales_year`, and the proxy strategy needs at least one hub per jurisdiction.
5. Check storage coverage: Gold requires exactly one storage-evidence row per graph node.

## 3. Add a basemap resolution

1. Add the value to `[basemaps.projected].resolutions_km` (or geographic `resolutions`).
2. Rebuild Silver. Impedance, road connectivity, storage and gasoline demand are all built per basemap.
3. Set `[basemap].resolution` in the scenario (or `model.toml`). Gold fails with a list of available basemaps if none matches.
4. Re-run any snap and impedance sensitivity checks: results depend on resolution (see [Ch. 16](../data-processing/16-known-assumptions-and-limitations.md)).

## 4. New fuel-sales year

1. Re-run the `gasoline_demand` Bronze stage with `--overwrite`.
2. Set `[gasoline_demand].sales_year`.
3. Rebuild Silver gasoline demand and Gold. Silver fails if the year or any configured jurisdiction is missing from the table.

## 5. New CanCO₂ storage release

1. Produce the release in the `canco2-storage` repository.
2. Acquire it (see the CanCO₂ profile). The selected file is recorded in `selected_release.txt`.
3. Rebuild the storage mapping and Gold. Record the release version in the profile.

## 6. New emissions year

1. Add a registry entry for the new ECCC release and acquire it.
2. Update the dataset-year constants in `emissions/facilities.py` and the matching Gold path constants in `schema/build.py` (both are currently hard-coded to 2024). **[confirm]** whether to parameterize these.
3. Rebuild Silver emissions and Gold.

## 7. Add a pipeline commodity cost layer (planned)

1. Provide a commodity-specific workbook with the same normalized worksheets.
2. Add cost-stage outputs for that commodity (the code currently generates only the H2 layer).
3. Extend Gold so `build_generalized_pipeline_*` selects the template by technology instead of applying the H2 layer to all pipeline technologies.
4. Update [Ch. 10](../data-processing/10-pipeline-cost-models.md) and remove the generalized-assumption note.

## 8. Change impedance assumptions

1. Add a new named profile to `registry/pipeline_impedance_penalties.yaml`, with an `evidence` block for each layer.
2. Select it with `[pipeline_impedance].penalty_profile`. Silver impedance outputs are written per build ID.
3. Choose the application scope in the scenario (`impedance_scope`).

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Gold stops with "does not exactly cover graph edges" | Impedance built for a different basemap | Rebuild Silver impedance for the selected basemap |
| Gold stops on storage coverage | Storage evidence built for another basemap | Rebuild the storage mapping |
| Snap step asks to exclude outliers | Points beyond `max_snap_distance_factor × resolution` | Review the audit CSV in `data_files/old_files/legacy_inputs/` before answering |
| Requested basemap not found | `[basemap]` in scenario does not match a built resolution | Build that resolution in Silver, or change the scenario |
| Temoa cannot find the database | TOML names an older Gold file | Update `input_database` to the new Gold filename |
| Silver stage reports a missing upstream product | Stage dependency not run | Run the full `geocanoe-build-silver` for the profile |
