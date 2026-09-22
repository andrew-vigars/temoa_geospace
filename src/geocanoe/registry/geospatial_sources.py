"""Validated registry access for external geospatial Bronze sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
GEOSPATIAL_SOURCE_REGISTRY = PROJECT_ROOT / "registry" / "geospatial_sources.yaml"


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping.")
    return value


def _require_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string.")
    return value.strip()


def load_geospatial_source_registry(
    path: Path | str = GEOSPATIAL_SOURCE_REGISTRY,
) -> dict[str, Any]:
    """Load and structurally validate the committed geospatial source registry."""

    registry_path = Path(path).expanduser().resolve()
    if not registry_path.is_file():
        raise FileNotFoundError(f"Geospatial source registry not found: {registry_path}")

    with registry_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file)
    root = _require_mapping(raw, "Geospatial source registry")
    if root.get("schema_version") != 2:
        raise ValueError("Geospatial source registry schema_version must be 2.")

    sources = root.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Geospatial source registry requires a non-empty sources list.")

    normalized: list[dict[str, Any]] = []
    source_ids: list[str] = []
    for index, value in enumerate(sources):
        source = _require_mapping(value, f"sources[{index}]")
        source_id = _require_string(source, "id", f"sources[{index}]")
        for key in ("kind", "domain", "publisher", "title", "licence"):
            _require_string(source, key, f"source {source_id!r}")

        acquisition = _require_mapping(
            source.get("acquisition"),
            f"source {source_id!r}.acquisition",
        )
        for key in ("stage", "module", "refresh_policy", "release_model"):
            _require_string(acquisition, key, f"source {source_id!r}.acquisition")

        bronze = _require_mapping(source.get("bronze"), f"source {source_id!r}.bronze")
        _require_string(bronze, "root", f"source {source_id!r}.bronze")
        primary_artifact = _require_string(
            bronze,
            "primary_artifact",
            f"source {source_id!r}.bronze",
        )
        artifacts = _require_mapping(
            bronze.get("artifacts"),
            f"source {source_id!r}.bronze.artifacts",
        )
        if not artifacts:
            raise ValueError(f"Source {source_id!r} requires at least one artifact.")
        if primary_artifact not in artifacts:
            raise ValueError(
                f"Source {source_id!r} primary_artifact {primary_artifact!r} "
                "is not declared in bronze.artifacts."
            )
        selection_artifact = bronze.get("selection_artifact")
        if selection_artifact is not None and (
            not isinstance(selection_artifact, str)
            or selection_artifact not in artifacts
        ):
            raise ValueError(
                f"Source {source_id!r}.bronze.selection_artifact must name "
                "a declared artifact."
            )
        for artifact_name, artifact_value in artifacts.items():
            if not isinstance(artifact_name, str) or not artifact_name.strip():
                raise ValueError(f"Source {source_id!r} has an invalid artifact name.")
            artifact = _require_mapping(
                artifact_value,
                f"source {source_id!r}.bronze.artifacts.{artifact_name}",
            )
            locators = [key for key in ("path", "glob") if key in artifact]
            if len(locators) != 1:
                raise ValueError(
                    f"Artifact {artifact_name!r} must declare exactly one of "
                    "'path' or 'glob'."
                )
            _require_string(artifact, locators[0], f"artifact {artifact_name!r}")
            _require_string(artifact, "format", f"artifact {artifact_name!r}")
            if "source_crs" in artifact:
                _require_string(artifact, "source_crs", f"artifact {artifact_name!r}")
            if "multiple" in artifact and not isinstance(artifact["multiple"], bool):
                raise ValueError(f"Artifact {artifact_name!r}.multiple must be boolean.")

        domains = _require_mapping(
            source.get("domains", {}),
            f"source {source_id!r}.domains",
        )
        for domain_name, domain_value in domains.items():
            domain = _require_mapping(
                domain_value,
                f"source {source_id!r}.domains.{domain_name}",
            )
            _require_string(domain, "field", f"domain {domain_name!r}")
            values = _require_mapping(domain.get("values"), f"domain {domain_name!r}.values")
            if not values or not all(
                isinstance(name, str)
                and name.strip()
                and isinstance(code, int)
                and not isinstance(code, bool)
                for name, code in values.items()
            ):
                raise ValueError(
                    f"Domain {domain_name!r} requires non-empty string names and integer codes."
                )
            if len(set(values.values())) != len(values):
                raise ValueError(f"Domain {domain_name!r} contains duplicate codes.")

        layers = _require_mapping(
            source.get("layers", {}),
            f"source {source_id!r}.layers",
        )
        for layer_name, layer_value in layers.items():
            layer = _require_mapping(layer_value, f"source {source_id!r}.layers.{layer_name}")
            for key in (
                "source_layer",
                "geometry_type",
                "primary_key_field",
                "geometry_field",
                "feature_id_field",
            ):
                _require_string(layer, key, f"layer {layer_name!r}")
            preserve_fields = layer.get("preserve_fields")
            if not isinstance(preserve_fields, list) or not all(
                isinstance(field, str) and field.strip() for field in preserve_fields
            ):
                raise ValueError(f"Layer {layer_name!r}.preserve_fields must be a string list.")
            if layer["feature_id_field"] not in preserve_fields:
                raise ValueError(
                    f"Layer {layer_name!r}.preserve_fields must include its "
                    "feature_id_field."
                )

        source_ids.append(source_id)
        source["domains"] = domains
        source["layers"] = layers
        normalized.append(source)

    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Geospatial source registry contains duplicate source IDs.")

    return {
        "schema_version": 2,
        "path": registry_path,
        "sources": normalized,
    }


class GeospatialSourceRegistry:
    """Resolve source declarations, Bronze artifacts, layers, and domains."""

    def __init__(
        self,
        path: Path | str = GEOSPATIAL_SOURCE_REGISTRY,
        *,
        repo_root: Path | str = PROJECT_ROOT,
    ) -> None:
        data = load_geospatial_source_registry(path)
        self.path = Path(data["path"])
        self.repo_root = Path(repo_root).expanduser().resolve()
        self._sources = {source["id"]: source for source in data["sources"]}

    def get(self, source_id: str) -> dict[str, Any]:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown geospatial source ID: {source_id!r}") from exc

    def list_ids(self) -> tuple[str, ...]:
        """Return registered source IDs in declaration order."""

        return tuple(self._sources)

    def artifact(self, source_id: str, artifact_name: str) -> dict[str, Any]:
        """Return one named Bronze artifact declaration."""

        source = self.get(source_id)
        try:
            return source["bronze"]["artifacts"][artifact_name]
        except KeyError as exc:
            raise KeyError(
                f"Source {source_id!r} has no artifact {artifact_name!r}."
            ) from exc

    def resolve_bronze_root(self, source_id: str) -> Path:
        """Resolve the source's Bronze root directory."""

        source = self.get(source_id)
        return (self.repo_root / source["bronze"]["root"]).resolve()

    def resolve_artifact_paths(
        self,
        source_id: str,
        artifact_name: str,
        *,
        require_matches: bool = False,
    ) -> tuple[Path, ...]:
        """Resolve a fixed artifact path or all paths matching an artifact glob."""

        artifact = self.artifact(source_id, artifact_name)
        root = self.resolve_bronze_root(source_id)
        paths: tuple[Path, ...]
        if "path" in artifact:
            paths = ((root / artifact["path"]).resolve(),)
        else:
            paths = tuple(sorted(path.resolve() for path in root.glob(artifact["glob"])))
        if require_matches and not paths:
            raise FileNotFoundError(
                f"No files found for artifact {artifact_name!r} of source "
                f"{source_id!r}."
            )
        if len(paths) > 1 and not artifact.get("multiple", False):
            raise ValueError(
                f"Artifact {artifact_name!r} of source {source_id!r} resolved "
                f"to {len(paths)} paths but is not declared multiple."
            )
        return paths

    def _resolve_single_artifact(self, source_id: str, artifact_name: str) -> Path:
        paths = self.resolve_artifact_paths(source_id, artifact_name)
        if len(paths) != 1:
            raise ValueError(
                f"Artifact {artifact_name!r} of source {source_id!r} must resolve "
                f"to exactly one path; found {len(paths)}."
            )
        return paths[0]

    def resolve_bronze_path(self, source_id: str) -> Path:
        source = self.get(source_id)
        return self._resolve_single_artifact(
            source_id,
            source["bronze"]["primary_artifact"],
        )

    def resolve_metadata_path(self, source_id: str) -> Path:
        return self._resolve_single_artifact(source_id, "metadata")

    def resolve_acquisition_manifest_path(self, source_id: str) -> Path:
        return self._resolve_single_artifact(source_id, "acquisition_manifest")

    def layer(self, source_id: str, layer_name: str) -> dict[str, Any]:
        source = self.get(source_id)
        try:
            return source["layers"][layer_name]
        except KeyError as exc:
            raise KeyError(
                f"Source {source_id!r} has no layer mapping {layer_name!r}."
            ) from exc

    def domain(self, source_id: str, domain_name: str) -> dict[str, Any]:
        source = self.get(source_id)
        try:
            return source["domains"][domain_name]
        except KeyError as exc:
            raise KeyError(
                f"Source {source_id!r} has no domain {domain_name!r}."
            ) from exc


__all__ = [
    "GEOSPATIAL_SOURCE_REGISTRY",
    "GeospatialSourceRegistry",
    "load_geospatial_source_registry",
]
