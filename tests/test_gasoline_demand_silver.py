from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, box

from geocanoe.preprocessing.gasoline_demand import (
    PRUID_TO_CODE,
    allocate_demand,
    assign_basemap_region_provinces,
    assign_das_to_hubs,
    map_proxy_demand_to_regions,
    select_hybrid_hubs,
    select_population_threshold_hubs,
    summarize_catchments,
)


def _centres() -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for index, (pruid, province) in enumerate(PRUID_TO_CODE.items()):
        population_class = 4 if province == "ON" else 2
        rows.append(
            {
                "PCUID": f"{index + 1:04d}",
                "PCPUID": f"{pruid}{index + 1:04d}",
                "PCNAME": f"Centre {province}",
                "PCCLASS": str(population_class),
                "PRUID": pruid,
                "LANDAREA": 10.0,
                "province": province,
                "population_class": population_class,
                "hub_id": f"{pruid}{index + 1:04d}",
                "population_centre_id": f"{index + 1:04d}",
                "estimated_population_2021": float((index + 1) * 1_000),
                "geometry": box(index * 10, 0, index * 10 + 2, 2),
            }
        )
    return gpd.GeoDataFrame(rows, crs="EPSG:3347")


def test_population_threshold_guarantees_all_thirteen_jurisdictions() -> None:
    centres = _centres()
    provinces = tuple(PRUID_TO_CODE.values())

    selected = select_population_threshold_hubs(
        centres,
        provinces,
        minimum_population=100_000,
    )

    assert set(selected["province"]) == set(provinces)
    assert len(selected) == 13
    assert selected.loc[
        selected["province"] == "ON", "selection_reason"
    ].item() == "population_threshold"
    assert selected.loc[
        selected["province"] == "NU", "selection_reason"
    ].item() == "jurisdiction_fallback"


def test_hybrid_adds_candidate_with_largest_population_distance_gain(
    tmp_path: Path,
) -> None:
    centres = gpd.GeoDataFrame(
        {
            "PCUID": ["0001", "0002", "0003"],
            "PCPUID": ["350001", "350002", "350003"],
            "PCNAME": ["Anchor", "Near", "Far"],
            "PCCLASS": ["4", "3", "3"],
            "PRUID": ["35", "35", "35"],
            "LANDAREA": [1.0, 1.0, 1.0],
            "province": ["ON", "ON", "ON"],
            "population_class": [4, 3, 3],
            "hub_id": ["350001", "350002", "350003"],
            "population_centre_id": ["0001", "0002", "0003"],
            "estimated_population_2021": [100_000.0, 40_000.0, 35_000.0],
        },
        geometry=[box(-1, -1, 1, 1), box(9, -1, 11, 1), box(99, -1, 101, 1)],
        crs="EPSG:3347",
    )
    das = gpd.GeoDataFrame(
        {
            "DGUID": ["near", "far"],
            "DAUID": ["1", "2"],
            "province": ["ON", "ON"],
            "population_2021": [100.0, 10_000.0],
        },
        geometry=[Point(10, 0), Point(100, 0)],
        crs="EPSG:3347",
    )
    registry = tmp_path / "anchors.csv"
    pd.DataFrame(
        [{
            "anchor_id": "anchor",
            "province": "ON",
            "pcpuid": "350001",
            "name": "Anchor",
            "evidence_basis": "text_named",
            "evidence_url": "https://example.test",
            "notes": "test",
        }]
    ).to_csv(registry, index=False)

    selected = select_hybrid_hubs(
        centres,
        das,
        ("ON",),
        registry,
        candidate_minimum_population=30_000,
        additional_hubs={"ON": 1},
    )

    assert set(selected["hub_id"]) == {"350001", "350003"}
    assert "pcpuid" not in selected.columns


def test_assignment_and_allocation_conserve_each_province() -> None:
    das = gpd.GeoDataFrame(
        {
            "DGUID": ["on-a", "on-b", "yt-a"],
            "DAUID": ["1", "2", "3"],
            "province": ["ON", "ON", "YT"],
            "population_2021": [75.0, 25.0, 10.0],
            "population_available": [True, True, True],
        },
        geometry=[Point(0, 0), Point(10, 0), Point(100, 0)],
        crs="EPSG:3347",
    )
    hubs = gpd.GeoDataFrame(
        {
            "hub_id": ["on-1", "on-2", "yt-1"],
            "PCNAME": ["Ontario A", "Ontario B", "Yukon"],
            "province": ["ON", "ON", "YT"],
            "population_centre_id": ["1", "2", "3"],
            "population_class": [4, 4, 2],
            "estimated_population_2021": [75.0, 25.0, 10.0],
            "selection_reason": ["test", "test", "test"],
            "lon": [-80.0, -79.0, -135.0],
            "lat": [44.0, 45.0, 60.0],
        },
        geometry=[Point(0, 0), Point(10, 0), Point(100, 0)],
        crs="EPSG:3347",
    )
    crosswalk = assign_das_to_hubs(das, hubs)
    catchments = summarize_catchments(crosswalk, hubs)
    sales = pd.DataFrame(
        {
            "province": ["ON", "YT"],
            "province_gasoline_litres": [1_000.0, 50.0],
        }
    )

    demand, audit = allocate_demand(catchments, sales, 0.00074, 2024)

    assert np.isclose(demand.loc[demand["province"] == "ON", "demand"].sum(), 0.74)
    assert demand[["lon", "lat"]].notna().all().all()
    assert np.allclose(audit["allocation_weight_sum"], 1.0)
    assert np.allclose(audit["litre_residual"], 0.0)


def test_basemap_mapping_uses_same_jurisdiction_fallback_and_conserves() -> None:
    regions = gpd.GeoDataFrame(
        {
            "region": ["R1", "R2", "R3"],
            "site_id": ["R1", "R2", "R3"],
            "centroid_x": [5.0, 15.0, 25.0],
            "centroid_y": [5.0, 5.0, 5.0],
            "resolution": [10.0, 10.0, 10.0],
            "resolution_unit": ["km", "km", "km"],
        },
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)],
        crs="EPSG:3347",
    )
    boundaries = gpd.GeoDataFrame(
        {"province_code": ["ON", "QC"]},
        geometry=[box(-1, -1, 19.9, 11), box(20, -1, 31, 11)],
        crs="EPSG:3347",
    )
    classified = assign_basemap_region_provinces(regions, boundaries)
    demand = gpd.GeoDataFrame(
        {
            "hub_id": ["on-contained", "on-fallback", "qc-contained"],
            "province": ["ON", "ON", "QC"],
            "assigned_population": [50.0, 30.0, 20.0],
            "gasoline_litres": [500.0, 300.0, 200.0],
            "demand": [0.37, 0.222, 0.148],
        },
        geometry=[Point(2, 5), Point(25, 5), Point(25, 5)],
        crs="EPSG:3347",
    )

    regional, crosswalk, audit = map_proxy_demand_to_regions(demand, classified)

    fallback = crosswalk.loc[crosswalk["hub_id"] == "on-fallback"].iloc[0]
    assert fallback["region"] == "R2"
    assert fallback["mapping_method"] == "nearest_same_jurisdiction_cell"
    assert np.isclose(regional["demand"].sum(), demand["demand"].sum())
    assert np.allclose(audit["demand_residual"], 0.0)
    assert int(regional["proxy_count"].sum()) == len(demand)
