"""Basemap cell-retention rules."""

import geopandas as gpd
from shapely.geometry import Point, box

from geocanoe.geospatial.basemaps import (
    retain_centroid_cells,
    retain_intersecting_cells,
)


def _candidate_grid():
    geometries = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    return gpd.GeoDataFrame(
        {
            "centroid_geometry": [Point(0.5, 0.5), Point(1.5, 0.5)],
            "geometry": geometries,
        },
        geometry="geometry",
        crs="EPSG:3347",
    )


def test_legacy_intersects_retains_border_cell_dropped_by_centroid():
    boundary = gpd.GeoDataFrame(
        geometry=[box(0.25, 0.25, 1.25, 0.75)],
        crs="EPSG:3347",
    )

    centroid = retain_centroid_cells(_candidate_grid(), boundary)
    intersects = retain_intersecting_cells(_candidate_grid(), boundary)

    assert list(centroid.index) == [0]
    assert list(intersects.index) == [0, 1]
    assert "centroid_geometry" not in intersects.columns
