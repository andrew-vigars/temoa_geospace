"""Regression coverage for map units and non-intercepting local context."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geocanoe.analysis import maps


@pytest.mark.parametrize("value,layer,expected", [
    (2500000, "H2", "2.5 Mt"),
    (2500000, "Electricity transmission", "2.5 TWh"),
    (1200, "Gasoline demand", "1.2 kt/year"),
    (0.00001, "H2", "<0.001 t"),
    (0, "H2", "0 t"),
    (1000000, "CO2", "1 Mt CO2e/year capacity"),
])
def test_display_units(value, layer, expected):
    assert maps.format_map_value(value, layer) == expected


def test_export_embeds_grid_below_hoverable_outputs(monkeypatch):
    monkeypatch.setattr(maps, "FOLIUM_BASEMAP_PATH", None)
    grid = gpd.GeoDataFrame(geometry=[box(-80, 45, -79, 46)], crs=4326)
    geo = maps.GeospatialData(grid, pd.DataFrame(), grid, None)
    points = pd.DataFrame([dict(region="A", lon=-79.5, lat=45.5, flow=2500000)])
    layers = maps.PlotLayers({"H2": (points, "#009E73")}, {},
                            pd.DataFrame(columns=["lon", "lat", "demand"]), pd.Series(dtype=float))
    spacing = maps.PlotSpacing(True, 1, 0, 0, 0)
    model_map = maps.create_folium_base_map(geo)
    maps.add_context_layers_folium(model_map, geo)
    maps.add_process_layers_folium(model_map, layers, spacing)
    maps.add_map_legend(model_map, layers)
    maps.folium.LayerControl().add_to(model_map)
    html = model_map.get_root().render()
    assert "L.tileLayer(" not in html
    assert '"pane": "context"' in html
    assert '"interactive": false' in html
    assert ".style.pointerEvents = 'none'" in html
    assert '2.5 Mt' in html
    assert 'Map legend' in html
    assert 'Model grid (1 regions)' in html


def test_export_embeds_processed_context_as_toggleable_reference_layers(monkeypatch):
    monkeypatch.setattr(maps, "FOLIUM_BASEMAP_PATH", None)
    grid = gpd.GeoDataFrame(geometry=[box(-80, 45, -79, 46)], crs=4326)
    lake = gpd.GeoDataFrame(geometry=[box(-79.9, 45.1, -79.7, 45.3)], crs=4326)
    lands = gpd.GeoDataFrame(geometry=[box(-79.6, 45.3, -79.4, 45.5)], crs=4326)
    urban = gpd.GeoDataFrame(geometry=[box(-79.3, 45.6, -79.1, 45.8)], crs=4326)
    geo = maps.GeospatialData(
        grid,
        pd.DataFrame(),
        grid,
        None,
        lakes=lake,
        aboriginal_lands=lands,
        urban_centres=urban,
    )
    model_map = maps.create_folium_base_map(geo)
    maps.add_context_layers_folium(model_map, geo)
    maps.folium.LayerControl().add_to(model_map)
    html = model_map.get_root().render()

    assert 'Lakes (1)' in html
    assert 'Aboriginal Lands (1)' in html
    assert 'Urban centres (1)' in html
    assert '"pane": "reference"' in html
    assert '#9ecae1' in html
