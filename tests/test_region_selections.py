"""Run-bound selections, scoped exports, and read-only diagnostic SQL."""
import json
import sqlite3
import shutil
import subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import folium
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geocanoe.analysis.exports import export_output_tables
from geocanoe.analysis.selection_ui import add_region_selection
from geocanoe.analysis.selections import (
    load_selection, query_selection, region_matches, save_selection,
    serve_map,
    export_selection,
    MapClients,
)


def selection(database):
    return dict(version=1, name="study_area", database=str(database.resolve()),
                fingerprint=None, regions=["R1", "R2", "R1"])


def test_persistence_is_run_bound_and_never_overwrites(tmp_path):
    database = tmp_path / "model.sqlite"
    path = save_selection(selection(database), database)
    assert path == tmp_path / "region_selections" / "study_area.json"
    assert load_selection(path, database)["regions"] == ["R1", "R2"]
    with pytest.raises(FileExistsError):
        save_selection(selection(database), database)
    with pytest.raises(ValueError, match="different run"):
        load_selection(path, tmp_path / "other.sqlite")
    with pytest.raises(ValueError, match="Name"):
        save_selection({**selection(database), "name": "../escape"}, database)
    with pytest.raises(ValueError, match="at least"):
        save_selection({**selection(database), "regions": []}, database)


@pytest.mark.parametrize("mode,expected", [
    ("touching", [True, True, True, False]),
    ("within", [True, True, False, False]),
    ("boundary", [True, False, True, False]),
])
def test_corridor_selection(mode, expected):
    assert [region_matches(r, {"R1", "R2"}, mode)
            for r in ["R1", "R1-R2", "R3-R1", "R3"]] == expected


def test_export_scopes_tables_and_records_selection(tmp_path):
    database = tmp_path / "model.sqlite"
    with sqlite3.connect(database) as connection:
        pd.DataFrame({"region": ["R1", "R3", "R1-R2", "R2-R3"], "value": [1,2,3,4]}).to_sql(
            "OutputCost", connection, index=False)
        pd.DataFrame({"objective": [100]}).to_sql("OutputObjective", connection, index=False)
    path = save_selection(selection(database), database)
    workbook, = export_output_tables(database, tmp_path / "excel", selection_path=path, transport="within")
    sheets = pd.read_excel(workbook, sheet_name=None)
    assert sheets["OutputCost"]["region"].tolist() == ["R1", "R1-R2"]
    assert sheets["RegionSelection"]["region"].tolist() == ["R1", "R2"]
    assert "OutputObjective" not in sheets


def test_sql_uses_temporary_ids_and_database_is_read_only(tmp_path):
    database = tmp_path / "model.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE OutputCost (region TEXT, cost REAL)")
        connection.executemany("INSERT INTO OutputCost VALUES (?, ?)", [("R1", 5), ("R3", 9)])
    path = save_selection(selection(database), database)
    sql = tmp_path / "cost.sql"
    sql.write_text("SELECT o.* FROM OutputCost o JOIN selected_regions s ON o.region=s.region")
    result = query_selection(database, path, sql)
    assert result.parent == tmp_path / "region_selections" / "study_area" / "sql_diagnostics"
    assert pd.read_csv(result)["region"].tolist() == ["R1"]
    sql.write_text("DELETE FROM OutputCost RETURNING *")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        query_selection(database, path, sql)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM OutputCost").fetchone()[0] == 2
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='selected_regions'").fetchall()


def test_map_embeds_exact_model_polygons_and_script_safe_identity(tmp_path):
    sites = gpd.GeoDataFrame(geometry=[box(0,0,100,100), box(100,0,200,100)],
                             index=["R1", "R2"], crs=3857)
    model_map = folium.Map()
    add_region_selection(model_map, sites, tmp_path / "model.sqlite", fingerprint="</script>")
    html = model_map.get_root().render()
    assert '"region": "R1"' in html
    assert '"centre_lon":' in html
    assert '"fingerprint": "\\u003c/script\\u003e"' in html
    assert "Select regions (click to toggle)" in html
    assert "__REGION_SELECTION_TOKEN__" in html


def test_generated_javascript_interactions(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for generated JavaScript interaction checks")
    sites = gpd.GeoDataFrame(geometry=[box(0,0,100,100), box(100,0,200,100)],
                             index=["R1", "R2"], crs=3857)
    model_map = folium.Map()
    add_region_selection(model_map, sites, tmp_path / "model.sqlite")
    html = model_map.get_root().render()
    script = html[html.index("(function() {"):html.index("})();")+5]
    target = tmp_path / "selection.js"
    target.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(Path(__file__).with_name("region_selection_ui_harness.cjs")),
                             str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_local_helper_saves_lists_and_rejects_unauthorized_requests(tmp_path, monkeypatch):
    database = tmp_path / "model.sqlite"
    with sqlite3.connect(database) as connection:
        pd.DataFrame({"region": ["R1", "R3"], "value": [1, 2]}).to_sql("OutputCost", connection, index=False)
    map_path = tmp_path / "map.html"
    map_path.write_text("<body>token=__REGION_SELECTION_TOKEN__</body>")
    original = ThreadingHTTPServer.serve_forever

    def exercise(server):
        thread = Thread(target=original, args=(server,), daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            page = urlopen(url).read().decode()
            token = page.split("=", 1)[1].split("<", 1)[0]
            assert "navigator.sendBeacon" in page
            headers = {"X-Selection-Token": token, "Content-Type": "application/json"}
            for event in ["heartbeat", "close"]:
                assert urlopen(Request(url + "/clients", method="POST",
                    data=json.dumps(dict(token=token, client="test-tab", event=event)).encode(),
                    headers={"Content-Type": "application/json"})).status == 200
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url + "/clients", method="POST",
                    data=json.dumps(dict(token="wrong", client="test-tab", event="close")).encode()))
            assert error.value.code == 403
            options = json.loads(urlopen(Request(url + "/export-options", headers=headers)).read())
            assert options["tables"] == ["OutputCost"]
            exported = json.loads(urlopen(Request(url + "/exports", method="POST", headers=headers,
                data=json.dumps(dict(selection=selection(database), tables=["OutputCost"],
                                     filename="ui_subset.xlsx", transport="touching")).encode())).read())
            assert pd.read_excel(exported["path"], sheet_name="OutputCost")["region"].tolist() == ["R1"]
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url + "/selections", data=b"{}", method="POST"))
            assert error.value.code == 403
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url + "/selections", data=b"{}", method="POST",
                                headers={**headers, "Origin": "https://other.example"}))
            assert error.value.code == 403
            response = urlopen(Request(url + "/selections", method="POST", headers=headers,
                                       data=json.dumps(selection(database)).encode()))
            assert response.status == 201
            values = json.loads(urlopen(Request(url + "/selections", headers=headers)).read())
            assert values[0]["regions"] == ["R1", "R2"]
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url + "/selections", method="POST", headers=headers,
                                data=json.dumps(selection(database)).encode()))
            assert error.value.code == 400
        finally:
            server.shutdown()
            thread.join()

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", exercise)
    serve_map(database, map_path, open_browser=False)


def test_ui_export_validates_destination_and_never_overwrites(tmp_path):
    database = tmp_path / "model.sqlite"
    with sqlite3.connect(database) as connection:
        pd.DataFrame({"region": ["R1", "R3"], "cost": [1, 2]}).to_sql("OutputCost", connection, index=False)
    request = dict(selection=selection(database), tables=["OutputCost"], transport="touching", filename="subset.xlsx")
    target = export_selection(database, request)
    assert target.with_suffix(".sqlite").exists()
    assert target.with_suffix(".sql").exists()
    assert target == tmp_path / "region_selections" / "study_area" / "excel_outputs" / "subset.xlsx"
    assert pd.read_excel(target, sheet_name="RegionSelection")["region"].tolist() == ["R1", "R2"]
    with pytest.raises(FileExistsError):
        export_selection(database, request)
    with pytest.raises(ValueError, match="Workbook name"):
        export_selection(database, {**request, "filename": "../../escape.xlsx"})
    with pytest.raises(ValueError, match="output table"):
        export_selection(database, {**request, "tables": ["OutputMissing"]})
    with pytest.raises(ValueError, match="different run"):
        export_selection(database, {**request, "selection": {**selection(database), "database": "other"}})


def test_tab_close_waits_for_refresh_and_other_tabs():
    clients = MapClients(grace=15, expiry=180)
    assert not clients.should_stop(1000)  # Wait indefinitely for the first tab.
    clients.update("first", "heartbeat", 1000)
    clients.update("first", "close", 1001)
    assert not clients.should_stop(1010)
    clients.update("refreshed", "heartbeat", 1011)
    assert not clients.should_stop(1020)
    clients.update("second", "heartbeat", 1021)
    clients.update("refreshed", "close", 1022)
    assert not clients.should_stop(1040)
    clients.update("second", "close", 1041)
    assert not clients.should_stop(1055)
    assert clients.should_stop(1056)


def test_crashed_tabs_expire_after_missing_heartbeats():
    clients = MapClients(grace=15, expiry=180)
    clients.update("crashed", "heartbeat", 1000)
    assert not clients.should_stop(1179)
    assert not clients.should_stop(1180)
    assert clients.should_stop(1195)
