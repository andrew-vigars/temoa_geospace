"""Offline contracts for the ERA5 Bronze stage."""

from pathlib import Path

import pytest

from geocanoe.acquisition import era5_renewables as era5
from geocanoe.execution.bronze import (
    BronzeStageOptions,
    execute_bronze_stage,
    parse_args,
)


def test_request_plan_is_deterministic_and_unique():
    plan = era5.build_request_plan([57.0, -95.5, 41.75, -74.0], 2018)
    assert len(plan) == 36
    assert len({item["request_id"] for item in plan}) == 36
    assert plan == era5.build_request_plan([57.0, -95.5, 41.75, -74.0], 2018)


def test_corrupt_later_cache_prevents_all_network_calls(tmp_path, monkeypatch):
    import cdsapi

    monkeypatch.setattr(
        cdsapi, "Client", lambda **kwargs: pytest.fail("Unexpected CDS call")
    )
    plan = era5.build_request_plan([45, -82, 43, -78], 2018, [1])
    bad = tmp_path / f"{plan[-1]['request_id']}.payload"
    bad.write_bytes(b"no manifest")
    with pytest.raises(ValueError, match="Unmanifested"):
        era5.acquire(plan, tmp_path)


def test_all_cached_and_zero_budget_never_construct_client(tmp_path, monkeypatch):
    import cdsapi

    monkeypatch.setattr(
        cdsapi, "Client", lambda **kwargs: pytest.fail("Unexpected CDS call")
    )
    plan = era5.build_request_plan([45, -82, 43, -78], 2020, [2])
    assert plan[0]["hours"] == 696
    assert era5.acquire(plan, tmp_path, max_new_requests=0) == 0
    for item in plan:
        payload = tmp_path / f"{item['request_id']}.payload"
        payload.write_bytes(b"weather")
        era5.manifest(
            payload.with_suffix(".json"),
            {"request": item["request"], "sha256": era5.sha256(payload)},
        )
    assert era5.acquire(plan, tmp_path) == 0
    payload.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Invalid Bronze"):
        era5.acquire(plan, tmp_path)


def test_stage_defaults_to_offline_and_reports_incomplete(tmp_path, monkeypatch):
    config = tmp_path / "era5.toml"
    config.write_text(
        f'area = [45, -82, 43, -78]\nweather_year = 2018\nmonths = [1]\nraw_dir = "{(tmp_path / "raw").as_posix()}"\n'
    )
    monkeypatch.setattr(
        era5, "acquire", lambda *args: pytest.fail("Offline stage called acquisition")
    )
    result = execute_bronze_stage(
        "era5_renewables", era5, BronzeStageOptions(era5_config=config, overwrite=True)
    )
    assert result["remaining"] == 3
    assert result["downloaded"] == 0
    assert (tmp_path / "raw/request_plan.json").is_file()
    args = parse_args(
        [
            "--stages",
            "era5_renewables",
            "--era5-download",
            "--era5-max-new-requests",
            "1",
        ]
    )
    assert args.era5_download and args.era5_max_new_requests == 1


@pytest.mark.parametrize(
    "area,months",
    [
        ([43, -82, 45, -78], [1]),
        ([45, -82, 43, -78], [1, 1]),
        ([45, -82, 43, -78], [13]),
    ],
)
def test_invalid_plan_is_rejected(area, months):
    with pytest.raises(ValueError):
        era5.build_request_plan(area, 2018, months)


def test_default_acquires_every_missing_request_with_progress(
    tmp_path, monkeypatch, capsys
):
    import cdsapi

    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"retry_max": 3, "sleep_max": 30, "timeout": 60}

        def retrieve(self, dataset, request):
            calls.append(request)
            return self

        def download(self, destination):
            Path(destination).write_bytes(b"weather")

    monkeypatch.setattr(cdsapi, "Client", Client)
    pauses = []
    monkeypatch.setattr(era5.time, "sleep", pauses.append)
    plan = era5.build_request_plan([45, -82, 43, -78], 2018, [1, 2])
    assert era5.acquire(plan, tmp_path) == 6
    assert len(calls) == 6
    assert pauses == [1] * 5
    assert not (tmp_path / ".acquisition.lock").exists()
    assert "remaining 0" in capsys.readouterr().out
    assert era5.acquire(plan, tmp_path) == 0
    assert len(calls) == 6


def test_overlapping_download_and_failure_stop_without_resubmitting(
    tmp_path, monkeypatch
):
    import cdsapi

    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("CDS unavailable")

    monkeypatch.setattr(cdsapi, "Client", fail)
    plan = era5.build_request_plan([45, -82, 43, -78], 2018, [1])
    with era5.acquisition_lock(tmp_path):
        with pytest.raises(RuntimeError, match="lock exists"):
            era5.acquire(plan, tmp_path)
    assert not calls
    with pytest.raises(RuntimeError, match="CDS unavailable"):
        era5.acquire(plan, tmp_path)
    assert len(calls) == 1
    assert not (tmp_path / ".acquisition.lock").exists()
    assert not list(tmp_path.glob("*.part"))


def test_canoe_weather_alignment_and_atb_years_are_distinct(tmp_path):
    (tmp_path / "params.toml").write_text(
        'weather_year = 2018\n[atb]\nyear = 2024\nurl = "fixture"\n'
    )
    (tmp_path / "generators.csv").write_text(
        "code,atb_display_name,atb_min_year\nsolar,Utility PV - Class 3,2022\nwind_onshore,Land-Based Wind - Class 7 - Technology 1,2022\n"
    )
    config = {
        "weather_year": 2018,
        "canoe": {
            "params": "params.toml",
            "generators": "generators.csv",
            "atb_edition": 2024,
        },
    }
    reference = era5.canoe_reference(config, tmp_path)
    assert reference["weather_year"] == 2018
    assert reference["atb_edition"] == 2024
    assert all(r["atb_min_year"] == "2022" for r in reference["selected_rows"])
    config["weather_year"] = 2024
    with pytest.raises(ValueError, match="weather_year must match"):
        era5.canoe_reference(config, tmp_path)


def test_census_extent_covers_all_features_and_preserves_source_identity(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box

    path = tmp_path / "census.gpkg"
    gpd.GeoDataFrame(
        {"PRUID": ["35", "60"]},
        geometry=[box(-80, 43, -79, 44), box(-141, 60, -120, 70)],
        crs=4326,
    ).to_file(path)
    area, provenance = era5.boundary_extent({"boundary_file": path.name}, tmp_path)
    assert area == [70.25, -141.25, 42.75, -78.75]
    assert provenance["feature_count"] == 2
    assert provenance["sha256"][path.name] == era5.sha256(path)
    assert "Silver" in provenance["selection"]
    config = {
        "grid_degrees": 0.25,
        "buffer_cells": 2,
        "boundary": {
            "kind": "digital",
            "census_year": 2021,
            "digital_file": path.name,
            "cartographic_file": "unused.gpkg",
        },
    }
    selected_area, selected = era5.boundary_extent(config, tmp_path)
    assert selected_area == [70.5, -141.5, 42.5, -78.5]
    assert selected["boundary_kind"] == "digital"
    assert selected["census_year"] == 2021
    config["boundary"]["kind"] = "unknown"
    with pytest.raises(ValueError, match="boundary.kind"):
        era5.boundary_extent(config, tmp_path)
    with pytest.raises(ValueError, match="not both"):
        era5.boundary_extent({"boundary_file": path.name, "area": area}, tmp_path)
