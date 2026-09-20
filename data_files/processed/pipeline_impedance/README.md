# Pipeline impedance Silver products

This directory is reserved for standardized terrain and siting layers used to
calculate effective pipeline-edge distance. The source datasets belong in the
Bronze raw-data layer; reproducible Silver transformations will write normalized
GeoPackages and manifests here.

Expected products are:

```text
waterbody.gpkg
first_nation_reserve.gpkg
residential_density.gpkg
pipeline_impedance_sources.manifest.json
pipeline_edge_impedance_<build-id>.csv
pipeline_edge_impedance_<build-id>.gpkg
```

GeoPackage is preferred over shapefile for Silver products because it preserves
field names, types, CRS metadata, and multiple related layers without shapefile's
field-name truncation. If compatibility shapefiles are later required, they should
be derived exports rather than canonical Silver inputs.

No scalar factors are defined in this directory or in the spatial source registry.
Factors are scenario/build assumptions and must be supplied by a validated build
profile. Silver products preserve the measured intersection distances so alternate
weight sets can be evaluated without repeating spatial overlays.

Source identities and current lineage confidence are recorded in
`registry/pipeline_impedance_sources.yaml`. The copied reference files from
`Hydrogen_Project_Final` are evidence for reconstructing lineage, not authoritative
Bronze inputs.
