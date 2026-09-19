"""Shared result contracts for model diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pandas as pd


class DiagnosticSeverity(StrEnum):
    """Impact assigned to a failed diagnostic check."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DiagnosticStage(StrEnum):
    """Model lifecycle stage evaluated by a diagnostic check."""

    INPUT = "input"
    MODEL = "model"
    SOLVE = "solve"
    OUTPUT = "output"


@dataclass
class DiagnosticResult:
    """Outcome and optional row-level evidence for one diagnostic check."""

    name: str
    passed: bool
    severity: str | DiagnosticSeverity
    detail: str
    failures: pd.DataFrame | None = None
    ran: bool = True
    check_id: str | None = None
    stage: str | DiagnosticStage = DiagnosticStage.INPUT
    remediation: str | None = None

    def __post_init__(self) -> None:
        self.severity = DiagnosticSeverity(str(self.severity).upper())
        self.stage = DiagnosticStage(str(self.stage).lower())

    @property
    def failed(self) -> bool:
        """Return whether this result should be shown as unsuccessful."""

        return not self.ran or not self.passed

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable summary without tabular evidence."""

        return {
            "check_id": self.check_id,
            "name": self.name,
            "stage": str(self.stage),
            "severity": str(self.severity),
            "passed": self.passed,
            "ran": self.ran,
            "detail": self.detail,
            "remediation": self.remediation,
            "failure_rows": 0 if self.failures is None else len(self.failures),
        }


@dataclass
class DiagnosticReport:
    """Ordered collection of diagnostic results and report metadata."""

    results: list[DiagnosticResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, *results: DiagnosticResult) -> None:
        """Append results while preserving execution order."""

        self.results.extend(results)

    @property
    def has_errors(self) -> bool:
        """Return whether an error check failed or did not execute."""

        return any(
            result.failed and result.severity == DiagnosticSeverity.ERROR
            for result in self.results
        )

    @property
    def has_warnings(self) -> bool:
        """Return whether a warning check failed or did not execute."""

        return any(
            result.failed and result.severity == DiagnosticSeverity.WARNING
            for result in self.results
        )

    def exit_code(self, fail_on_warning: bool = False) -> int:
        """Return the deterministic validation exit code for this report."""

        if self.has_errors or (fail_on_warning and self.has_warnings):
            return 1
        return 0

    def to_records(self) -> list[dict[str, Any]]:
        """Return JSON-serializable summaries in execution order."""

        return [result.to_record() for result in self.results]
