"""Validated access to versioned pipeline-impedance penalty profiles."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any

import yaml

from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
PIPELINE_IMPEDANCE_PENALTY_REGISTRY = (
    PROJECT_ROOT / "registry" / "pipeline_impedance_penalties.yaml"
)
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PipelinePenaltyLayer:
    """One versioned scalar penalty assumption within a named profile."""

    name: str
    enabled: bool
    source_id: str
    penalty_method: str
    scalar: float
    evidence_status: str
    citation: str
    publication_year: int | None
    url: str | None
    note: str

    @property
    def applied_scalar(self) -> float:
        """Return the active scalar, or zero when this layer is disabled."""

        return self.scalar if self.enabled else 0.0


@dataclass(frozen=True)
class PipelinePenaltyProfile:
    """A named collection of pipeline-impedance assumptions."""

    name: str
    description: str
    layers: dict[str, PipelinePenaltyLayer]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping.")
    return value


def _string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string.")
    return value.strip()


class PipelinePenaltyRegistry:
    """Load and resolve audited scalar penalty profiles."""

    def __init__(
        self,
        path: Path | str = PIPELINE_IMPEDANCE_PENALTY_REGISTRY,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Pipeline penalty registry not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as file:
            root = _mapping(yaml.safe_load(file), "Pipeline penalty registry")
        if root.get("schema_version") != 1:
            raise ValueError("Pipeline penalty registry schema_version must be 1.")

        profiles_raw = _mapping(root.get("profiles"), "profiles")
        if not profiles_raw:
            raise ValueError("Pipeline penalty registry requires at least one profile.")

        profiles: dict[str, PipelinePenaltyProfile] = {}
        for profile_name, profile_value in profiles_raw.items():
            if not isinstance(profile_name, str) or not IDENTIFIER_PATTERN.fullmatch(
                profile_name
            ):
                raise ValueError(
                    f"Invalid pipeline penalty profile ID: {profile_name!r}."
                )
            profile_raw = _mapping(profile_value, f"profiles.{profile_name}")
            layers_raw = _mapping(
                profile_raw.get("layers"), f"profiles.{profile_name}.layers"
            )
            if not layers_raw:
                raise ValueError(
                    f"Profile {profile_name!r} requires at least one layer."
                )

            layers: dict[str, PipelinePenaltyLayer] = {}
            for layer_name, layer_value in layers_raw.items():
                label = f"profiles.{profile_name}.layers.{layer_name}"
                if not isinstance(layer_name, str) or not IDENTIFIER_PATTERN.fullmatch(
                    layer_name
                ):
                    raise ValueError(
                        f"Invalid pipeline penalty layer ID: {layer_name!r}."
                    )
                layer_raw = _mapping(layer_value, label)
                enabled = layer_raw.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError(f"{label}.enabled must be boolean.")
                scalar = layer_raw.get("scalar")
                if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
                    raise ValueError(f"{label}.scalar must be numeric.")
                scalar = float(scalar)
                if not math.isfinite(scalar) or scalar < 0:
                    raise ValueError(f"{label}.scalar must be finite and non-negative.")
                method = _string(layer_raw, "penalty_method", label)
                if method != "additional_distance_factor":
                    raise ValueError(
                        f"{label}.penalty_method must be 'additional_distance_factor'."
                    )
                evidence = _mapping(layer_raw.get("evidence"), f"{label}.evidence")
                publication_year = evidence.get("publication_year")
                if publication_year is not None and (
                    isinstance(publication_year, bool)
                    or not isinstance(publication_year, int)
                    or publication_year < 1800
                ):
                    raise ValueError(
                        f"{label}.evidence.publication_year must be null or a valid year."
                    )
                url = evidence.get("url")
                if url is not None and (not isinstance(url, str) or not url.strip()):
                    raise ValueError(
                        f"{label}.evidence.url must be null or a URL string."
                    )
                layers[layer_name] = PipelinePenaltyLayer(
                    name=layer_name,
                    enabled=enabled,
                    source_id=_string(layer_raw, "source_id", label),
                    penalty_method=method,
                    scalar=scalar,
                    evidence_status=_string(evidence, "status", f"{label}.evidence"),
                    citation=_string(evidence, "citation", f"{label}.evidence"),
                    publication_year=publication_year,
                    url=url.strip() if isinstance(url, str) else None,
                    note=_string(evidence, "note", f"{label}.evidence"),
                )
            profiles[profile_name] = PipelinePenaltyProfile(
                name=profile_name,
                description=_string(
                    profile_raw, "description", f"profiles.{profile_name}"
                ),
                layers=layers,
            )
        self._profiles = profiles

    def list_profiles(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def get_profile(self, profile_name: str) -> PipelinePenaltyProfile:
        try:
            return self._profiles[profile_name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown pipeline penalty profile {profile_name!r}; available: "
                f"{list(self._profiles)}"
            ) from exc

    def get_layer(self, profile_name: str, layer_name: str) -> PipelinePenaltyLayer:
        profile = self.get_profile(profile_name)
        try:
            return profile.layers[layer_name]
        except KeyError as exc:
            raise KeyError(
                f"Pipeline penalty profile {profile_name!r} has no layer "
                f"{layer_name!r}."
            ) from exc


__all__ = [
    "PIPELINE_IMPEDANCE_PENALTY_REGISTRY",
    "PipelinePenaltyLayer",
    "PipelinePenaltyProfile",
    "PipelinePenaltyRegistry",
]
