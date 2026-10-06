"""Regression coverage for map units and non-intercepting local context."""
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geocanoe.analysis import maps


@pytest.mark.parametrize(
    ("flow_table", "demand_table", "capacity_table"),
    [
        ("OutputFlowOut", "Demand", "LimitCapacity"),
        ("output_flow_out", "demand", "limit_capacity"),
    ],
)
def test_load_model_tables_accepts_legacy_and_v4_names(
    tmp_path: Path,
    flow_table: str,
    demand_table: str,
    capacity_table: str,
) -> None:
    database_path = tmp_path / "solved.sqlite"
    with sqlite3.connect(database_path) as connection:
        pd.DataFrame(
            [{"region": "R0", "tech": "ELC_GEN", "flow": 1.0}]
        ).to_sql(flow_table, connection, index=False)
        pd.DataFrame(
            [{"region": "R0", "commodity": "d_gsl", "demand": 2.0}]
        ).to_sql(demand_table, connection, index=False)
        pd.DataFrame(
            [{"region": "R0", "tech_or_group": "CO2_CAP", "capacity": 3.0}]
        ).to_sql(capacity_table, connection, index=False)

    tables = maps.load_model_tables(database_path)

    assert tables.flow_out.loc[0, "flow"] == 1.0
    assert tables.demand.loc[0, "demand"] == 2.0
    assert tables.limit_capacity.loc[0, "capacity"] == 3.0


def test_load_model_tables_reports_missing_required_table(tmp_path: Path) -> None:
    database_path = tmp_path / "solved.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE demand (region TEXT)")

    with pytest.raises(KeyError, match="OutputFlowOut"):
        maps.load_model_tables(database_path)


def test_pipeline_count_is_loaded_and_attached_to_map_links(tmp_path: Path) -> None:
    database_path = tmp_path / "solved.sqlite"
    with sqlite3.connect(database_path) as connection:
        pd.DataFrame(
            [{"region": "A-B", "tech": "CO2_PIPE", "flow": 10.0}]
        ).to_sql("output_flow_out", connection, index=False)
        pd.DataFrame(
            [{"region": "A", "commodity": "d_gsl", "demand": 2.0}]
        ).to_sql("demand", connection, index=False)
        pd.DataFrame(
            [{"region": "A", "tech_or_group": "CO2_CAP", "capacity": 3.0}]
        ).to_sql("limit_capacity", connection, index=False)
        pd.DataFrame(
            [
                {
                    "scenario": "S",
                    "period": 2030,
                    "region": region,
                    "tech": "CO2_PIPE",
                    "vintage": 2030,
                    "capacity": 81.0,
                    "units": "kt/year",
                }
                for region in ["A-B", "B-A"]
            ]
        ).to_sql("output_net_capacity", connection, index=False)
        pd.DataFrame(
            [
                {
                    "tech_or_group": "CO2_PIPE",
                    "segment": segment,
                    "capacity_lower": lower,
                    "capacity_upper": upper,
                }
                for segment, (lower, upper) in enumerate(
                    [(0, 10), (10, 30), (30, 40), (40, 60), (60, 70), (70, 90)]
                )
            ]
        ).to_sql("cost_invest_eos", connection, index=False)

    tables = maps.load_model_tables(database_path)
    edges = pd.DataFrame(
        [
            {
                "edge_region": "A-B",
                "region_from": "A",
                "region_to": "B",
                "lon_from": -80.0,
                "lat_from": 45.0,
                "lon_to": -79.0,
                "lat_to": 46.0,
            }
        ]
    )
    links = maps.build_transport_layers(
        tables.flow_out,
        edges,
        tables.pipeline_capacity,
    )["CO2 pipeline"][0]

    assert tables.pipeline_capacity.loc[0, "eos_pipeline_count"] == pytest.approx(2.7)
    assert links.loc[0, "eos_pipeline_count"] == pytest.approx(2.7)


def test_folium_pipeline_popup_displays_fractional_eos_count() -> None:
    link = pd.DataFrame(
        [
            {
                "region": "A-B",
                "tech": "CO2_PIPE",
                "region_from": "A",
                "region_to": "B",
                "lon_from": -80.0,
                "lat_from": 45.0,
                "lon_to": -79.0,
                "lat_to": 46.0,
                "flow": 10.0,
                "pipeline_capacity": 81.0,
                "eos_base_capacity": 30.0,
                "eos_pipeline_count": 2.7,
            }
        ]
    )
    layers = maps.PlotLayers(
        {},
        {"CO2 pipeline": (link, "#777777", "-", 2.5)},
        pd.DataFrame(columns=["lon", "lat", "demand"]),
        pd.Series(dtype=float),
    )
    model_map = maps.folium.Map(location=[45.5, -79.5])

    maps.add_transport_layers_folium(
        model_map,
        layers,
        maps.PlotSpacing(True, 1.0, 0.0, 100.0, 500.0),
    )
    html = model_map.get_root().render()

    assert "EoS pipeline count" in html
    assert "EoS pipelines: 2.7" in html


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
