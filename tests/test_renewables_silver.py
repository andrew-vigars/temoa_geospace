import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from geocanoe.preprocessing import renewables as stage
from geocanoe.preprocessing import renewables_weather as physical
from geocanoe.execution.silver import SILVER_STAGE_DEPENDENCIES
from geocanoe.schema.renewables import apply_bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("resolutions", [[], [10], [24.99], [25, 5], [np.nan]])
def test_no_unvalidated_subgrid_resolution(resolutions):
    with pytest.raises(ValueError, match=">=25"):
        stage.validate_resolutions(resolutions)
    stage.validate_resolutions([25, 50, 100])


def test_native_temperature_and_spatial_subset_precede_loading():
    ds = xr.Dataset(
        {"t2m": (("latitude", "longitude"), np.full((3, 3), 288.15))},
        coords={"latitude": [44.0, 43.5, 43.0], "longitude": [-81.0, -80.5, -80.0]},
    )
    selected = physical.spatial_subset(ds, [-80.75, 43.25, -80.25, 43.75])
    assert selected.t2m.shape == (1, 1)
    assert selected.t2m.item() == 288.15


def test_annual_temoa_tables_have_valid_capacity_and_currency_contract(tmp_path):
    crosswalk = pd.read_csv(
        ROOT / "registry/renewables/technology_crosswalk.csv", keep_default_na=False
    )
    costs = pd.DataFrame(
        [
            {"tech": t, "core_metric_parameter": m, "value_per_MW": v}
            for t in crosswalk.tech
            for m, v in [("OCC", 1_000_000.0), ("Fixed O&M", 25_000.0)]
        ]
    )
    annual = pd.DataFrame(
        {"wind_cf": [0.4, 0.5], "solar_cf": [0.15, 0.2]}, index=["R1", "R2"]
    )
    settings = {
        "model_period": 2025,
        "vintage": 2025,
        "cost_projection_year": 2030,
        "weather_year": 2018,
    }
    tables = stage.gold_tables(annual, crosswalk, costs, settings, tmp_path)
    schema = ROOT.parent / "temoa/temoa/db_schema/temoa_schema_v4.sql"
    result = stage.validate_temoa_tables(tables, schema)
    assert result["foreign_key_violations"] == 0
    assert result["table_counts"]["limit_annual_capacity_factor"] == 4
    assert {r["c2a"] for r in tables["capacity_to_activity"]} == {8760.0}
    assert all(r["units"] == "USD2023/MW" for r in tables["cost_invest"])
    assert {r["factor"] for r in tables["limit_annual_capacity_factor"]} == {
        0.4,
        0.5,
        0.15,
        0.2,
    }
    assert not json.loads((tmp_path / "contract.json").read_text())["merge_ready"]
    assert SILVER_STAGE_DEPENDENCIES["renewables_atlite"] == ("basemaps",)


def test_monthly_weather_cache_restores_atlite_module_and_checks_temporal_coverage(
    tmp_path, monkeypatch
):
    import atlite

    bronze = tmp_path / "bronze"
    bronze.mkdir()
    items = [
        {"feature": f, "request_id": f, "month": 1}
        for f in ["wind", "temperature", "influx"]
    ]
    for item in items:
        (bronze / f"{item['request_id']}.payload").write_bytes(b"fixture")
    time = pd.date_range("2018-01-01", periods=744, freq="h")
    weather = xr.Dataset(
        coords={"time": time, "x": [-80.0, -79.75], "y": [43.0, 43.25]}
    )
    monkeypatch.setattr(physical, "prepare_weather", lambda *args: weather.copy())
    ds, path = stage.monthly_weather(
        items, bronze, tmp_path / "cache", [-81, 42, -79, 44], 2018
    )
    assert ds.attrs["module"] == "era5"
    cutout = atlite.Cutout(
        path.with_name("descriptor.nc"),
        module="era5",
        x=slice(-80, -79.75),
        y=slice(43, 43.25),
        time=slice(time[0], time[-1]),
    )
    cutout.data = ds
    assert len(cutout.grid) == 4
    monkeypatch.setattr(
        physical,
        "prepare_weather",
        lambda *args: pytest.fail("Expected weather cache reuse"),
    )
    cached, _ = stage.monthly_weather(
        items, bronze, tmp_path / "cache", [-81, 42, -79, 44], 2018
    )
    assert len(cached.time) == 744
    ds.close()
    cached.close()
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        stage.monthly_weather(
            items, bronze, tmp_path / "cache", [-81, 42, -79, 44], 2018
        )


def test_gold_replacement_uses_cad_dollars_and_requires_all_nodes(tmp_path):
    crosswalk = pd.read_csv(
        ROOT / "registry/renewables/technology_crosswalk.csv", keep_default_na=False
    )
    costs = pd.DataFrame(
        [
            {"tech": t, "core_metric_parameter": m, "value_per_MW": v}
            for t in crosswalk.tech
            for m, v in [("OCC", 1_000_000.0), ("Fixed O&M", 25_000.0)]
        ]
    )
    annual = pd.DataFrame(
        {"wind_cf": [0.4, 0.5], "solar_cf": [0.15, 0.2]}, index=["R1", "R2"]
    )
    settings = {
        "model_period": 2025,
        "vintage": 2025,
        "cost_projection_year": 2030,
        "weather_year": 2018,
        "currency_basis": "registry/renewables/currency_basis.toml",
    }
    tables = stage.gold_tables(annual, crosswalk, costs, settings, tmp_path)
    assert tables["cost_invest"][0]["cost"] == pytest.approx(
        1_000_000 * 1.225042 * 104.3 / 123.2
    )
    encoded = {
        name: pd.DataFrame(rows).iloc[:0].copy() for name, rows in tables.items()
    }
    for name, rows in tables.items():
        frame = pd.DataFrame([rows[0]])
        if "tech" in frame:
            frame["tech"] = "ELC_GEN"
        elif "tech_or_group" in frame:
            frame["tech_or_group"] = "ELC_GEN"
        encoded[name] = frame
    # Production core reconstruction omits optional technology descriptions.
    encoded["technology"] = encoded["technology"].drop(columns="description")
    encoded["sector_label"] = pd.DataFrame(
        {"sector": ["industrial"], "notes": ["industrial sector"]}
    )
    before = {name: frame.copy() for name, frame in encoded.items()}
    with pytest.raises(ValueError, match="every selected Gold node"):
        apply_bundle(encoded, tmp_path, {"R1", "R2", "R3"}, 2025)
    for name in encoded:
        pd.testing.assert_frame_equal(encoded[name], before[name])
    result = apply_bundle(encoded, tmp_path, {"R1", "R2"}, 2025)
    assert result["replaced_legacy"] == "ELC_GEN"
    assert set(encoded["technology"].tech) == {"WIND_GEN", "SOLAR_GEN"}
    assert encoded["technology"].description.notna().all()
    assert set(encoded["sector_label"].sector) == {"industrial", "electricity"}
    assert len(encoded["limit_annual_capacity_factor"]) == 4
    assert (encoded["cost_variable"].cost == 0).all()
    for frame in encoded.values():
        for col in ["tech", "tech_or_group"]:
            if col in frame:
                assert "ELC_GEN" not in set(frame[col])


def test_coarse_overlap_aggregation_preserves_area_weighted_resource(tmp_path):
    import atlite
    import geopandas as gpd

    cutout = atlite.Cutout(
        tmp_path / "grid.nc",
        module="era5",
        x=slice(-80, -79.75),
        y=slice(43, 43.25),
        time="2018-01-01",
    )
    grid = cutout.grid.to_crs(3347)
    fine = grid.copy()
    fine["region"] = ["A", "B", "C", "D"]
    fine["production_area_km2"] = fine.area / 1e6
    coarse = gpd.GeoDataFrame(
        {"region": ["ALL"]}, geometry=[grid.geometry.union_all()], crs=3347
    )
    coarse["production_area_km2"] = coarse.area / 1e6
    fine_matrix, layout, fine_coverage = physical.aggregation_matrix(cutout, fine)
    coarse_matrix, _, coarse_coverage = physical.aggregation_matrix(cutout, coarse)
    values = np.array([0.1, 0.3, 0.4, 0.6])
    areas = layout.values.ravel()
    fine_cf = (
        np.asarray(fine_matrix @ (areas * values)).ravel()
        / np.asarray(fine_matrix @ areas).ravel()
    )
    coarse_cf = (
        np.asarray(coarse_matrix @ (areas * values)).ravel()
        / np.asarray(coarse_matrix @ areas).ravel()
    )
    assert coarse_cf[0] == pytest.approx(np.average(fine_cf, weights=areas))
    np.testing.assert_allclose(fine_coverage, 1.0)
    np.testing.assert_allclose(coarse_coverage, 1.0)


@pytest.mark.parametrize("reuse", [True, False])
def test_production_regions_reuses_only_matching_boundary(tmp_path, capsys, reuse):
    import geopandas as gpd
    from shapely.geometry import box

    basemap = tmp_path / "sample_basemap_25km_centroid.gpkg"
    cells = gpd.GeoDataFrame(
        {
            "region": ["A", "B"],
            "resolution_km": [25.0, 25.0],
            "study_area": ["sample", "sample"],
        },
        geometry=[box(0, 0, 25000, 25000), box(25000, 0, 50000, 25000)],
        crs=3347,
    )
    cells.to_file(basemap, layer="regions")
    clipped = box(0, 0, 40000, 25000)
    dissolved = gpd.GeoDataFrame(
        {"study_area": ["sample"], "province_codes": ["ON" if reuse else "BC"]},
        geometry=[clipped],
        crs=3347,
    )
    dissolved.to_file(
        tmp_path / "sample_boundary_epsg3347.gpkg", layer="study_area_boundary"
    )
    boundary = tmp_path / "census.gpkg"
    if not reuse:
        gpd.GeoDataFrame(
            {"PRENAME": ["Ontario"]}, geometry=[clipped], crs=3347
        ).to_file(boundary)
    # A missing raw boundary proves that the matching dissolved product is reused.
    regions, _ = stage.production_regions(basemap, boundary, ["ON"])
    assert list(regions.region) == ["A", "B"]
    np.testing.assert_allclose(regions.production_area_km2, [625.0, 375.0])
    output = capsys.readouterr().out
    assert ("reused dissolved boundary" in output) == reuse
    assert "clipped 1/1" in output
    assert "area/validity checks" in output
