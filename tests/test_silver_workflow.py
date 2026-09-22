from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from geocanoe.execution.silver import (
    FunctionExecutor,
    SILVER_STAGE_DEPENDENCIES,
    SILVER_STAGE_ORDER,
    ScriptExecutor,
    SilverWorkflowDefinition,
    SilverWorkflowState,
    execute_silver_stage,
    run_silver_workflow,
    validate_requested_stages,
    validate_stage_dependencies,
    validate_workflow_definition,
)


def test_gasoline_basemap_stage_follows_both_inputs() -> None:
    positions = {stage: index for index, stage in enumerate(SILVER_STAGE_ORDER)}

    assert SILVER_STAGE_DEPENDENCIES["gasoline_basemap"] == (
        "gasoline_demand",
        "basemaps",
    )
    assert positions["gasoline_demand"] < positions["gasoline_basemap"]
    assert positions["basemaps"] < positions["gasoline_basemap"]


def test_aboriginal_lands_stage_follows_basemaps() -> None:
    positions = {stage: index for index, stage in enumerate(SILVER_STAGE_ORDER)}

    assert SILVER_STAGE_DEPENDENCIES["aboriginal_lands"] == ("basemaps",)
    assert positions["basemaps"] < positions["aboriginal_lands"]


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        source_path=Path("profile.toml"),
        study_area=SimpleNamespace(label="test"),
    )


def _definition(
    first,
    second,
    *,
    second_dependencies: tuple[str, ...] = ("first",),
) -> SilverWorkflowDefinition:
    stages = ("first", "second")
    return SilverWorkflowDefinition(
        stage_order=stages,
        stage_modules={stage: f"example.{stage}" for stage in stages},
        dependencies={"first": (), "second": second_dependencies},
        external_dependencies={stage: () for stage in stages},
        executors={
            "first": FunctionExecutor(first),
            "second": FunctionExecutor(second),
        },
    )


def test_workflow_executes_in_dependency_order_and_records_history() -> None:
    calls: list[str] = []
    definition = _definition(
        lambda _config: calls.append("first") or "one",
        lambda _config: calls.append("second") or "two",
    )
    state = SilverWorkflowState.empty()

    results = run_silver_workflow(
        config=_config(),
        definition=definition,
        state=state,
    )

    assert calls == ["first", "second"]
    assert results == {"first": "one", "second": "two"}
    assert [record["status"] for record in state.history] == [
        "completed",
        "completed",
    ]


def test_workflow_stops_on_failure_and_preserves_completed_state() -> None:
    def fail(_config) -> None:
        raise RuntimeError("stage failed")

    definition = _definition(lambda _config: "one", fail)
    state = SilverWorkflowState.empty()

    with pytest.raises(RuntimeError, match="stage failed"):
        run_silver_workflow(
            config=_config(),
            definition=definition,
            state=state,
        )

    assert state.completed_stages == {"first"}
    assert [record["status"] for record in state.history] == [
        "completed",
        "failed",
    ]
    assert state.history[-1]["exception_type"] == "RuntimeError"


def test_config_independent_stage_is_called_without_argument() -> None:
    calls: list[str] = []
    definition = SilverWorkflowDefinition(
        stage_order=("only",),
        stage_modules={"only": "example.only"},
        dependencies={"only": ()},
        external_dependencies={"only": ()},
        executors={
            "only": FunctionExecutor(
                lambda: calls.append("only") or 42,
                pass_config=False,
            )
        },
    )
    state = SilverWorkflowState.empty()

    result = execute_silver_stage(
        "only",
        config=_config(),
        definition=definition,
        state=state,
    )

    assert result == 42
    assert calls == ["only"]


def test_stage_dependency_must_be_complete() -> None:
    definition = _definition(lambda _config: None, lambda _config: None)

    with pytest.raises(RuntimeError, match="first"):
        validate_stage_dependencies(
            "second",
            definition,
            SilverWorkflowState.empty(),
        )


@pytest.mark.parametrize(
    "requested",
    [
        ("unknown",),
        ("first", "first"),
        ("second", "first"),
    ],
)
def test_requested_stages_reject_invalid_sequences(
    requested: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        validate_requested_stages(requested, ("first", "second"))


def test_workflow_definition_rejects_dependency_after_consumer() -> None:
    definition = _definition(
        lambda _config: None,
        lambda _config: None,
        second_dependencies=(),
    )
    definition = SilverWorkflowDefinition(
        stage_order=definition.stage_order,
        stage_modules=definition.stage_modules,
        dependencies={"first": ("second",), "second": ()},
        external_dependencies=definition.external_dependencies,
        executors=definition.executors,
    )

    with pytest.raises(ValueError, match="must precede"):
        validate_workflow_definition(definition)


def test_workflow_definition_rejects_missing_script(tmp_path: Path) -> None:
    definition = SilverWorkflowDefinition(
        stage_order=("script",),
        stage_modules={"script": "example.script"},
        dependencies={"script": ()},
        external_dependencies={"script": ()},
        executors={"script": ScriptExecutor(tmp_path / "missing.py")},
    )

    with pytest.raises(FileNotFoundError, match="missing.py"):
        validate_workflow_definition(definition)
