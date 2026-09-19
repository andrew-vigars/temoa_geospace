from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from geocanoe.diagnostics.models import DiagnosticReport, DiagnosticResult
from geocanoe.diagnostics.renderers import render_console_result, write_json_report
from geocanoe.diagnostics.runner import run_checks
from geocanoe.schema.artifacts import resolve_schema_artifact_paths


def test_diagnostic_report_exit_codes_and_runner_order() -> None:
    passing = DiagnosticResult("first", True, "ERROR", "ok")
    warning = DiagnosticResult("second", False, "WARNING", "review")

    report = run_checks([lambda: passing, lambda: [warning]])

    assert [result.name for result in report.results] == ["first", "second"]
    assert not report.has_errors
    assert report.has_warnings
    assert report.exit_code() == 0
    assert report.exit_code(fail_on_warning=True) == 1


def test_error_and_unexecuted_error_fail_report() -> None:
    report = DiagnosticReport(
        results=[
            DiagnosticResult("failed", False, "ERROR", "bad"),
            DiagnosticResult("skipped", False, "ERROR", "not run", ran=False),
        ]
    )

    assert report.has_errors
    assert report.exit_code() == 1


def test_renderers_bound_evidence_and_write_json() -> None:
    result = DiagnosticResult(
        "bad rows",
        False,
        "ERROR",
        "two failures",
        failures=pd.DataFrame({"row": [1, 2]}),
        check_id="INPUT.EXAMPLE",
    )
    report = DiagnosticReport(results=[result], metadata={"database": "model.sqlite"})

    rendered = render_console_result(result, max_rows=1)
    output = Path(".tmp") / "diagnostics-foundation-report.json"
    try:
        write_json_report(report, output)
        payload = json.loads(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)

    assert "bad rows" in rendered
    assert " 1" in rendered
    assert payload["metadata"]["database"] == "model.sqlite"
    assert payload["results"][0]["failure_rows"] == 2


def test_schema_artifact_paths_include_road_layer() -> None:
    paths = resolve_schema_artifact_paths(
        project_root=Path("C:/example/geocanoe"),
        basemap_stem="on_qc_basemap_25km_centroid",
        road_layer="freight_access",
        connection_method="strong",
    )

    assert paths.basemap.name == "on_qc_basemap_25km_centroid.gpkg"
    assert paths.road_edge_connections.name == (
        "on_qc_basemap_25km_centroid_freight_access_"
        "road_connectivity_strong_road_edge_connections.csv"
    )
    assert paths.schema.name == (
        "CANOE_geospatial_on_qc_basemap_25km_centroid_"
        "freight_access_strong.sqlite"
    )
