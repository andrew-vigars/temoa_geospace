from __future__ import annotations

from pathlib import Path

from geocanoe.registry.geospatial_sources import GeospatialSourceRegistry
from geocanoe.registry.pipeline_impedance import PipelinePenaltyRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_committed_penalty_profiles_are_audited_and_sources_are_registered() -> None:
    penalties = PipelinePenaltyRegistry(
        PROJECT_ROOT / "registry" / "pipeline_impedance_penalties.yaml"
    )
    sources = GeospatialSourceRegistry(
        PROJECT_ROOT / "registry" / "geospatial_sources.yaml"
    )

    disabled = penalties.get_layer("no_penalties", "aboriginal_lands")
    legacy = penalties.get_layer("legacy_reference_v1", "aboriginal_lands")
    disabled_nhn = penalties.get_layer("no_penalties", "nhn_waterbodies")
    legacy_nhn = penalties.get_layer("legacy_reference_v1", "nhn_waterbodies")
    disabled_population = penalties.get_layer("no_penalties", "population_exposure")
    legacy_population = penalties.get_layer(
        "legacy_reference_v1", "population_exposure"
    )

    assert disabled.enabled is False
    assert disabled.applied_scalar == 0.0
    assert legacy.enabled is True
    assert legacy.scalar == 9.0
    assert legacy.applied_scalar == 9.0
    assert "unvalidated" in legacy.evidence_status
    assert legacy.source_id in sources.list_ids()
    assert disabled_nhn.applied_scalar == 0.0
    assert legacy_nhn.enabled is True
    assert legacy_nhn.scalar == 9.0
    assert legacy_nhn.source_id in sources.list_ids()
    assert disabled_population.enabled is False
    assert legacy_population.enabled is True
    assert legacy_population.penalty_method == "population_density_band_factor"
    assert [band.factor for band in legacy_population.bands] == [0.0, 0.11, 0.43, 0.82]
    assert legacy_population.source_id in sources.list_ids()
