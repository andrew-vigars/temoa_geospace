"""Execution helpers for collections of diagnostic checks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from geocanoe.diagnostics.models import DiagnosticReport, DiagnosticResult

DiagnosticCheck = Callable[[], DiagnosticResult | Iterable[DiagnosticResult]]


def run_checks(
    checks: Iterable[DiagnosticCheck],
    *,
    metadata: dict[str, object] | None = None,
) -> DiagnosticReport:
    """Execute independent checks and preserve their declared result order."""

    report = DiagnosticReport(metadata=dict(metadata or {}))
    for check in checks:
        outcome = check()
        if isinstance(outcome, DiagnosticResult):
            report.add(outcome)
        else:
            report.add(*outcome)
    return report
