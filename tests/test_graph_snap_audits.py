"""Gold point-mapping diagnostics belong with processed emissions evidence."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geocanoe.schema import build


@pytest.mark.parametrize("locked", [False, True])
def test_snap_outliers_write_processed_audit(tmp_path, monkeypatch, capsys, locked):
    directory = tmp_path / "processed/emissions/graph_snap_audits"
    monkeypatch.setattr(build, "GRAPH_SNAP_AUDIT_DIR", directory)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    nodes = gpd.GeoDataFrame(
        {"region": ["R1"]}, geometry=[box(-80, 44, -79, 45)], crs=4326
    )
    context = build.PointAssignmentContext(nodes, nodes.to_crs(3347), 1000)
    points = pd.DataFrame({"longitude": [-79.5, -40.0], "latitude": [44.5, 10.0]})
    normal = directory / "co2_facilities_graph_snap_distance_audit.csv"
    original_write = pd.DataFrame.to_csv
    writes = []

    def write(frame, path, **kwargs):
        writes.append(path)
        if locked and path == normal:
            raise PermissionError("Audit is open")
        return original_write(frame, path, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", write)
    result = build.snap_points_to_graph_nodes(
        points, context, lon_col="longitude", lat_col="latitude",
        dataset_label="CO2 facilities",
    )
    assert list(result.region) == ["R1"]
    files = list(directory.glob("*.csv"))
    assert len(files) == 1
    assert (files[0] != normal) == locked
    audit = pd.read_csv(files[0])
    assert audit.longitude.tolist() == [-40.0]
    assert audit.snap_distance_km.iloc[0] > 1
    assert len(writes) == (2 if locked else 1)
    assert ("open or locked" in capsys.readouterr().out) == locked
