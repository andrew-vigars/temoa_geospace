# 0. Documentation conventions

These markers flag places where the documentation needs a human decision or a
reference. Search the docs for the tag to produce a to-do list.

| Marker | Meaning | Resolve by |
|---|---|---|
| **[confirm]** | Behaviour or rationale inferred from code and not recorded anywhere. | Author confirms or corrects the statement. |
| **[cite-method]** | A modelling choice (representation strategy) that should be supported by methodology literature, e.g. why a regular grid represents regions, or why additive distance penalties approximate routing cost. | Add a reference, or state the choice is a simplification. |
| **[cite-gis]** | A GIS operation or data convention that should be supported by a GIS reference or product documentation, e.g. centroid-in-polygon retention, projection choice, geodesic vs planar distance, polygon validity repair. | Add a reference, or the dataset user guide. |
| **[cite-data]** | A parameter value or dataset whose origin must be cited (threshold, factor, release/version). | Add the source, or mark as an unvalidated assumption. |
| **[stale]** | Repository text that contradicts the code. | Fix the repository text, then remove the marker. |

## Evidence status vocabulary

`registry/pipeline_impedance_penalties.yaml` already records an
`evidence.status` for each penalty (for example `legacy_model_assumption_unvalidated`
or `control_case`). Use the same vocabulary in the docs:

- **validated**: backed by a cited source.
- **legacy_model_assumption_unvalidated**: inherited from the earlier
  `Hydrogen_Project_Final` model without a literature basis.
- **control_case**: deliberately neutral setting used for comparison.

## Chapter template

1. Purpose and role in the model
2. Sources
3. Configuration
4. Silver transformation (numbered steps)
5. Assumptions and limitations (table)
6. Outputs and consumers
7. Validation

## Reference list

Collect cited works in a single `references.md` once the first **[cite-*]**
markers are resolved, and link from each chapter.
