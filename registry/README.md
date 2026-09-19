# Registry for Bronze Inputs

This directory contains user-managed model inputs and the declarative metadata
used to identify and describe raw bronze-layer datasets in Geospatial-CANOE.

The user-managed CSVs in this directory are source inputs to the
schema-building (gold-layer) workflow. Add or update registry data here, and
keep each entry in `bronze_registry.yaml` synchronized with its file. Registry
entries may also point to source inputs elsewhere in the repository.

## Quick start

1. Inspect `bronze_registry.yaml` to see available dataset IDs and their
   registered source paths.

2. Use the helper from your scripts:

```python
from geocanoe.registry import Registry

registry = Registry()

print(registry.list_ids())
print(registry.resolve_path("commodities"))
```
