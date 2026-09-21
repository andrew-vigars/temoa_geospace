"""Validated registry access for external geospatial source databases."""

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
    if root.get("schema_version") != 1:
        raise ValueError("Geospatial source registry schema_version must be 1.")

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

        bronze = _require_mapping(source.get("bronze"), f"source {source_id!r}.bronze")
        for key in (
            "path",
            "metadata_path",
            "acquisition_manifest_path",
            "format",
            "source_crs",
        ):
            _require_string(bronze, key, f"source {source_id!r}.bronze")

        domains = _require_mapping(source.get("domains"), f"source {source_id!r}.domains")
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

        layers = _require_mapping(source.get("layers"), f"source {source_id!r}.layers")
        if not layers:
            raise ValueError(f"Source {source_id!r} requires at least one layer mapping.")
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
        normalized.append(source)

    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Geospatial source registry contains duplicate source IDs.")

    return {
        "schema_version": 1,
        "path": registry_path,
        "sources": normalized,
    }


class GeospatialSourceRegistry:
    """Resolve source declarations, local Bronze paths, layers, and domains."""

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

    def resolve_bronze_path(self, source_id: str) -> Path:
        source = self.get(source_id)
        return (self.repo_root / source["bronze"]["path"]).resolve()

    def resolve_metadata_path(self, source_id: str) -> Path:
        source = self.get(source_id)
        return (self.repo_root / source["bronze"]["metadata_path"]).resolve()

    def resolve_acquisition_manifest_path(self, source_id: str) -> Path:
        source = self.get(source_id)
        return (
            self.repo_root / source["bronze"]["acquisition_manifest_path"]
        ).resolve()

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
