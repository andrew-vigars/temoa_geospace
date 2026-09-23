"""Console and structured renderers for diagnostic reports."""

from __future__ import annotations

import json
from pathlib import Path

from geocanoe.diagnostics.models import DiagnosticReport, DiagnosticResult


def result_status(result: DiagnosticResult) -> str:
    """Return the compact display status for one result."""

    if not result.ran:
        return "SKIP"
    if result.passed:
        return "PASS"
    return str(result.severity)


def render_console_result(result: DiagnosticResult, max_rows: int = 20) -> str:
    """Render one check and bounded row-level evidence as plain text."""

    lines = [f"[{result_status(result)}] {result.name}", f"       {result.detail}"]
    if result.failed and result.failures is not None and not result.failures.empty:
        lines.append(result.failures.head(max_rows).to_string(index=False))
    if result.failed and result.remediation:
        lines.append(f"       Remediation: {result.remediation}")
    return "\n".join(lines)


def write_json_report(report: DiagnosticReport, path: Path) -> Path:
    """Write a stable machine-readable summary for a diagnostic report."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": report.metadata, "results": report.to_records()}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
