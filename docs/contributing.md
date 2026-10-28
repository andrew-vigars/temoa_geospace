# Contributing

## Keep changes reviewable

Separate implementation, generated artifacts and documentation decisions into
coherent commits. Do not commit raw downloads, processed model databases or
solver outputs unless a project policy explicitly requires a small fixture.

## Update documentation with the model

- Update the relevant Data Guide when a source, transformation or final-data contract changes.
- Add or revise a structural-sensitivity record when testing a different representation.
- Add a decision record when that evidence changes the maintained model.
- Regenerate the source catalogue after changing `registry/geospatial_sources.yaml` or its supplements.
- Regenerate open items after adding or resolving documentation markers.

```powershell
python docs/tools/gen_sources.py
python docs/tools/gen_open_items.py
```

## Validate before committing

- run the tests relevant to the changed configuration or module;
- verify local Markdown links;
- run `git diff --check`;
- inspect staged files so unrelated generated products are not included; and
- record any known pre-existing test failure separately from the change.

The repository README remains the authoritative installation, testing and
release reference. This page records the documentation-specific contribution
workflow.
