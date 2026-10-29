"""Physical unit lineage from commodity definitions to output workbooks."""
import sqlite3

import pandas as pd
import pytest

from geocanoe.analysis.exports import export_output_tables
from geocanoe.analysis.flow_units import label_flow_units
from geocanoe.registry import Registry
from geocanoe.schema.build import load_empty_temoa_v4_tables, rebuild_static_supporting_tables


def test_gold_preserves_parseable_logistical_commodity_units():
    from temoa.model_checking.unit_checking.relations_checker import make_commodity_lut

    raw = pd.read_csv(Registry().resolve_path("commodities"))
    tables = load_empty_temoa_v4_tables()
    rebuild_static_supporting_tables(tables, raw, 2025, 2050, 0, 0)
    with sqlite3.connect(":memory:") as connection:
        tables["commodity"].to_sql("commodity", connection, index=False)
        units = make_commodity_lut(connection)
    for name in ["h2", "ch3oh", "co2", "co2_stored", "gsl", "d_gsl"]:
        assert str(units[name]) == "metric_ton"
    assert str(units["elc"]) == "megawatt_hour"


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_gold_rejects_missing_commodity_units(bad):
    raw = pd.read_csv(Registry().resolve_path("commodities"))
    raw.loc[raw.name.eq("h2"), "units"] = bad
    with pytest.raises(ValueError, match="nonempty physical units"):
        rebuild_static_supporting_tables(load_empty_temoa_v4_tables(), raw, 2025, 2050, 0, 0)


def test_flow_unit_recovery_is_directional_and_preserves_declared_units():
    rows = pd.DataFrame({"input_comm": ["elc"]*4, "output_comm": ["h2", "co2", "gsl", "unknown"],
                         "flow": [1., 2., 3., 4.], "units": [None, "", "kg", None]})
    result, audit = label_flow_units(rows, "output_flow_out", {"h2": "t"}, {"co2": "t", "gsl": "t"})
    assert result.units.iloc[:3].tolist() == ["t", "t", "kg"]
    assert result.units_source.tolist() == ["archived_commodity", "current_registry_inference", "output_table", "missing"]
    assert result.flow.tolist() == rows.flow.tolist()
    incoming, _ = label_flow_units(rows.assign(units=None), "OutputFlowIn", {"elc": "MWh"}, {})
    assert incoming.units.eq("MWh").all()
    assert audit.rows.sum() == 4


def test_legacy_workbook_labels_units_and_keeps_database_unchanged(tmp_path):
    database = tmp_path / "solved.sqlite"
    with sqlite3.connect(database) as connection:
        pd.DataFrame({"name": ["h2", "elc"], "units": [None, "MWh"]}).to_sql("commodity", connection, index=False)
        pd.DataFrame({"region": ["R1-R2"], "input_comm": ["h2"], "output_comm": ["h2"],
                      "tech": ["H2_TRUCK"], "flow": [12.5], "units": [None]}).to_sql("output_flow_out", connection, index=False)
    workbook, = export_output_tables(database, tmp_path / "export")
    frame = pd.read_excel(workbook, sheet_name="output_flow_out")
    assert frame.units.tolist() == ["t"]
    assert frame.units_source.tolist() == ["current_registry_inference"]
    assert frame.flow.tolist() == [12.5]
    assert pd.read_excel(workbook, sheet_name="FlowUnitAudit").rows.sum() == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT units FROM output_flow_out").fetchone()[0] is None
