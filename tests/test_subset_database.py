"""Diagnostic SQL bundles preserve scoped inputs and solved evidence."""
import sqlite3

from geocanoe.analysis.subset_database import export_subset_database


def test_subset_preserves_inputs_context_and_restorable_sql(tmp_path):
    source = tmp_path / "solved.sqlite"
    with sqlite3.connect(source) as connection:
        connection.executescript("""
            CREATE TABLE region (region TEXT PRIMARY KEY);
            INSERT INTO region VALUES ('R1'),('R2'),('R3'),('R4'),('R1-R2'),('R3-R4');
            CREATE TABLE efficiency (region TEXT, tech TEXT, efficiency REAL);
            INSERT INTO efficiency VALUES ('R1-R2','H2_PIPE',1),('R3-R4','H2_PIPE',1);
            CREATE TABLE cost_variable (region TEXT, cost REAL, units TEXT);
            INSERT INTO cost_variable VALUES ('R1-R2',2,'CAD2020/t'),('R3-R4',3,'CAD2020/t'),('global',4,'CAD2020/t');
            CREATE TABLE commodity (name TEXT, units TEXT);
            INSERT INTO commodity VALUES ('h2',NULL),('elc','MWh');
            CREATE TABLE output_flow_out (region TEXT, flow REAL, units TEXT);
            INSERT INTO output_flow_out VALUES ('R1-R2',12.5,NULL),('R3-R4',9,NULL);
            CREATE INDEX flow_region ON output_flow_out(region);
            CREATE VIEW active_flows AS SELECT * FROM output_flow_out WHERE flow > 0;
        """)
    selection = dict(version=1, name="loop", database=str(source.resolve()), regions=["R1"])
    target = export_subset_database(source, selection, tmp_path / "subset.sqlite", "touching")
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT * FROM efficiency").fetchall() == [("R1-R2", "H2_PIPE", 1)]
        assert connection.execute("SELECT * FROM active_flows").fetchall() == [("R1-R2", 12.5, None)]
        assert connection.execute("SELECT region FROM region ORDER BY region").fetchall() == [("R1",),("R1-R2",),("R2",)]
        assert connection.execute("SELECT COUNT(*) FROM cost_variable").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM commodity").fetchone()[0] == 2
        assert connection.execute("SELECT * FROM subset_selection").fetchall() == [("R1",)]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(target.with_suffix(".sql").read_text(encoding="utf-8"))
        assert restored.execute("SELECT * FROM output_flow_out").fetchall() == [("R1-R2", 12.5, None)]
        assert restored.execute("SELECT value FROM subset_manifest WHERE key='transport'").fetchone()[0] == "touching"
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT COUNT(*) FROM output_flow_out").fetchone()[0] == 2
