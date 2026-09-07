# Registry for Bronze Inputs

This directory contains the declarative metadata used to identify and describe
raw bronze-layer datasets in Geospatial-CANOE.

## Quick start

1. Inspect `bronze_registry.yaml` to see available dataset IDs and their
   registered source paths.

2. Use the helper from your scripts:

```python
from geocanoe.registry import Registry

registry = Registry()

print(registry.list_ids())
print(registry.resolve_path("commodities"))
