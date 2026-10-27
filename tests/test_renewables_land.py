import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from scipy import sparse
from shapely.geometry import Point, box

from geocanoe.preprocessing import renewables_land as land
from geocanoe.preprocessing.renewables_weather import weighted_profile


def test_technology_specific_weights_and_zero_capacity_regions():
    cells = xr.DataArray(
        np.array([[[0.2, 0.8]], [[0.4, 0.6]]]),
        dims=("time", "y", "x"),
        coords={"time": [0, 1], "y": [0], "x": [0, 1]},
    )
    weights = sparse.csr_matrix([[1, 0], [0, 3], [0, 0]])
    result = weighted_profile(cells, weights, ["A", "B", "C"])
    np.testing.assert_allclose(result.values, [[0.2, 0.4], [0.8, 0.6], [0, 0]])


def test_wind_grouping_never_connects_different_projects():
    points = gpd.GeoDataFrame(
        {"Province_Territory": ["ON"] * 3, "Project_Name": ["A", "A", "B"]},
        geometry=[Point(0, 0), Point(100, 0), Point(10000, 0)],
        crs=3347,
    )
    footprints = land.wind_footprints(points, 100)
    assert len(footprints) == 2
    assert not footprints.geometry.iloc[0].intersects(footprints.geometry.iloc[1])
    assert (footprints.area > 0).all()


def test_disabled_policy_and_tracking_validation():
    assert land.policy({"land_exclusion": {"enabled": False}}) == {"enabled": False}
    with pytest.raises(ValueError, match="single-axis"):
        land.policy(
            {
                "land_exclusion": {"enabled": True},
                "technology": {"pv_tracking": "vertical"},
            }
        )


def test_physical_conversion_uses_distinct_masks_and_clips_pv_first(monkeypatch):
    from geocanoe.preprocessing import renewables_weather as physical

    cells = xr.DataArray(
        [[[0.2, 0.8]]],
        dims=("time", "y", "x"),
        coords={"time": [0], "y": [0], "x": [0, 1]},
    )

    class Cutout:
        def wind(self, **kwargs):
            return cells

        def pv(self, **kwargs):
            return cells

    regions = pd.DataFrame({"region": ["A", "B"]})
    layout = xr.DataArray([[1.0, 1.0]], dims=("y", "x"))
    monkeypatch.setattr(
        physical,
        "aggregation_matrix",
        lambda *args: (sparse.eye(2, format="csr"), layout, np.ones(2)),
    )
    monkeypatch.setattr(physical, "t1_config", lambda *args: {})
    weights = {
        "wind": sparse.csr_matrix([[1, 0], [0, 0]]),
        "solar": sparse.csr_matrix([[0, 1], [0, 0]]),
    }
    result, _ = physical.convert(
        Cutout(),
        regions,
        None,
        dict(
            wind_plant_loss=0.0,
            pv_additional_loss=0.0,
            pv_ilr=2.0,
            inverter_efficiency=1.0,
        ),
        land_weights=weights,
    )
    np.testing.assert_allclose(result.wind_cf, [[0.2], [0.0]])
    np.testing.assert_allclose(result.solar_cf, [[1.0], [0.0]])


def test_native_atlite_exclusions_reduce_capacity_weights(tmp_path):
    import atlite
    from atlite.gis import ExclusionContainer

    path = tmp_path / "classes.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=1,
        dtype="uint8",
        crs=4326,
        transform=from_origin(-80.125, 44.125, 0.0025, 0.0025),
        nodata=0,
    ) as dst:
        values = np.ones((100, 100), dtype="uint8")
        values[:, :50] = 18
        dst.write(values, 1)
    cutout = atlite.Cutout(
        tmp_path / "cutout.nc",
        module="era5",
        x=slice(-80, -79.75),
        y=slice(44, 44.25),
        time="2018-01-01",
    )
    regions = gpd.GeoDataFrame(
        {"region": ["A"]}, geometry=[box(-80.12, 43.88, -79.88, 44.12)], crs=4326
    )
    excluded = ExclusionContainer(crs=3347, res=100)
    excluded.add_raster(str(path), codes=[18], nodata=0)
    baseline = ExclusionContainer(crs=3347, res=100)
    baseline.add_raster(str(path), codes=[99], nodata=0)
    actual = land.eligible_weights(cutout, regions, excluded, 1)
    full = land.eligible_weights(cutout, regions, baseline, 1)
    assert 0.35 < actual.sum() / full.sum() < 0.65


def test_native_area_conservation_and_checkpoint_resume(tmp_path, monkeypatch):
    import atlite
    from atlite.gis import ExclusionContainer

    cutout = atlite.Cutout(
        tmp_path / "national.nc",
        module="era5",
        x=slice(-125, -95),
        y=slice(48, 84),
        time="2018-01-01",
    )
    centres = gpd.GeoSeries([Point(-115, 51.216818), Point(-110, 82)], crs=4326).to_crs(
        3347
    )
    regions = gpd.GeoDataFrame(
        {"region": ["southern", "arctic"]},
        geometry=[
            box(p.x - 12500, p.y - 12500, p.x + 12500, p.y + 12500) for p in centres
        ],
        crs=3347,
    )
    excluder = ExclusionContainer(crs=3347, res=100)
    directory = tmp_path / "batches"
    weights = land.eligible_weights(cutout, regions, excluder, 1, directory)
    np.testing.assert_allclose(
        np.asarray(weights.sum(axis=1)).ravel(), [625, 625], atol=1e-7
    )
    monkeypatch.setattr(
        excluder,
        "compute_shape_availability",
        lambda *args: pytest.fail("Completed batch recomputed"),
    )
    resumed = land.eligible_weights(cutout, regions, excluder, 1, directory)
    np.testing.assert_array_equal(weights.toarray(), resumed.toarray())
    (directory / "000000.npz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        land.eligible_weights(cutout, regions, excluder, 1, directory)


@pytest.mark.parametrize("solar_codes", [[14, 15, 17, 18], [1, 18], list(range(1, 20))])
def test_shared_pass_matches_independent_masks_and_resumes(
    tmp_path, monkeypatch, solar_codes
):
    import atlite
    import atlite.gis as gis
    from atlite.gis import ExclusionContainer
    from unittest.mock import Mock

    path = tmp_path / "shared_classes.tif"
    # Native mask source covers all test geometries; NoData and unknown codes
    # must be excluded by both implementations.
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=1,
        dtype="uint8",
        crs=3347,
        transform=from_origin(7000000, 1100000, 100, 100),
        nodata=0,
    ) as dst:
        values = np.ones((100, 100), dtype="uint8")
        values[:, 25:50] = 15
        values[:, 50:65] = 18
        values[:, 65:70] = 0
        values[:, 70:75] = 99
        dst.write(values, 1)
    regions = gpd.GeoDataFrame(
        {"region": ["A", "B"]},
        geometry=[
            box(7000100, 1090100, 7009900, 1099900),
            box(7000175, 1090175, 7002235, 1092235),
        ],
        crs=3347,
    )
    bounds = regions.to_crs(4326).total_bounds
    cutout = atlite.Cutout(
        tmp_path / "shared_cutout.nc",
        module="era5",
        x=slice(bounds[0] - 0.5, bounds[2] + 0.5),
        y=slice(bounds[1] - 0.5, bounds[3] + 0.5),
        time="2018-01-01",
    )
    exclusions = gpd.GeoSeries([box(7000100, 1090100, 7001000, 1099000)], crs=3347)
    selected = {
        "batch_size": 1,
        "wind_excluded_codes": [14, 17, 18],
        "solar_excluded_codes": solar_codes,
    }
    expected = {}
    for tech in ["wind", "solar"]:
        excluder = ExclusionContainer(crs=3347, res=100)
        excluder.add_geometry(exclusions)
        excluder.add_raster(
            str(path),
            codes=sorted(set(range(1, 20)) - set(selected[f"{tech}_excluded_codes"]))
            or [-1],
            invert=True,
            nodata=0,
            allow_no_overlap=False,
        )
        expected[tech] = land.eligible_weights(cutout, regions, excluder, 1)
    common = ExclusionContainer(crs=3347, res=100)
    common.add_geometry(exclusions)
    compute = Mock(wraps=common.compute_shape_availability)
    monkeypatch.setattr(common, "compute_shape_availability", compute)
    project = Mock(wraps=gis.projected_mask)
    monkeypatch.setattr(gis, "projected_mask", project)
    checkpoint = tmp_path / "shared_batches"
    actual = land.shared_eligible_weights(
        cutout, regions, common, path, selected, checkpoint
    )
    assert compute.call_count == len(regions)
    assert project.call_count == len(regions)
    for tech in expected:
        np.testing.assert_allclose(
            actual[tech].toarray(), expected[tech].toarray(), atol=1e-10
        )
    compute.reset_mock()
    project.reset_mock()
    resumed = land.shared_eligible_weights(
        cutout, regions, common, path, selected, checkpoint
    )
    assert compute.call_count == project.call_count == 0
    for tech in actual:
        np.testing.assert_array_equal(actual[tech].toarray(), resumed[tech].toarray())
    # A partial/uncommitted pair must be recomputed rather than reused.
    (checkpoint / "000000.json").unlink()
    recovered = land.shared_eligible_weights(
        cutout, regions, common, path, selected, checkpoint
    )
    assert compute.call_count == project.call_count == 1
    for tech in actual:
        np.testing.assert_array_equal(actual[tech].toarray(), recovered[tech].toarray())
    (checkpoint / "000000_solar.npz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        land.shared_eligible_weights(
            cutout, regions, common, path, selected, checkpoint
        )
