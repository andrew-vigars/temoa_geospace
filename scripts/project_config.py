"""
project_config.py

Shared configuration loader for Geospatial-CANOE preprocessing build profiles.

This module loads a TOML build profile, validates shared study-area and
geospatial workflow settings, and exposes immutable dataclasses for use by
build_basemaps.py, build_roads.py, map_roads.py, build_region_adjacency.py,
and build_schema.py.

The configuration describes one preprocessing build profile. It is separate
from the TEMOA solver configuration used by main_run.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


# =============================================================================
# Canonical project constants
# =============================================================================

ALL_PROVINCE_CODES = (
    "NL",
    "PE",
    "NS",
    "NB",
    "QC",
    "ON",
    "MB",
    "SK",
    "AB",
    "BC",
    "YT",
    "NT",
    "NU",
)

SUPPORTED_GRID_TYPES = {
    "geographic",
    "projected",
}

SUPPORTED_KEEP_METHODS = {
    "centroid",
}

SUPPORTED_ADJACENCY_METHODS = {
    "rook",
}

SUPPORTED_ROAD_NETWORKS = {
    "backbone",
    "freight_access",
}

SUPPORTED_ROAD_CONNECTIVITY_METHODS = {
    "weak",
    "strong",
}

SUPPORTED_ROAD_LAYERS = {
    "backbone",
    "freight_access",
}

DEFAULT_CONFIG_RELATIVE_PATH = (
    Path("config")
    / "build_profiles"
    / "provinces_only.toml"
)


# =============================================================================
# Immutable configuration containers
# =============================================================================

@dataclass(frozen=True)
class StudyAreaConfig:
    """Study-area identity shared across geospatial preprocessing stages."""

    label: str
    provinces: tuple[str, ...]


@dataclass(frozen=True)
class BasemapGridFamilyConfig:
    """Resolution settings for one basemap grid family."""

    resolutions: tuple[float, ...]


@dataclass(frozen=True)
class BasemapConfig:
    """Basemap-generation settings."""

    grid_types: tuple[str, ...]
    keep_method: str
    coordinate_precision: int
    geographic: BasemapGridFamilyConfig
    projected: BasemapGridFamilyConfig

    @property
    def geographic_resolutions_deg(self) -> tuple[float, ...]:
        """Return configured EPSG:4326 resolutions in decimal degrees."""

        return self.geographic.resolutions

    @property
    def projected_resolutions_km(self) -> tuple[float, ...]:
        """Return configured EPSG:3347 resolutions in kilometres."""

        return self.projected.resolutions


@dataclass(frozen=True)
class AdjacencyConfig:
    """Region-adjacency settings."""

    method: str
    coordinate_precision: int
    no_neighbor_id: str


@dataclass(frozen=True)
class RoadClassConfig:
    """Road classes included in each processed network representation."""

    backbone: tuple[str, ...]
    freight_access: tuple[str, ...]


@dataclass(frozen=True)
class RoadConfig:
    """Processed-road-network settings."""

    networks: tuple[str, ...]
    export_individual_provinces: bool
    output_crs: str
    classes: RoadClassConfig


@dataclass(frozen=True)
class RoadConnectivityConfig:
    """Road-to-region connectivity settings."""

    road_layer: str
    methods: tuple[str, ...]
    plot_outputs: bool


@dataclass(frozen=True)
class SchemaConfig:
    """Schema-selection and point-assignment preferences."""

    road_connection_method: str
    interactive_basemap_selection: bool
    basemap_stem: str | None
    boundary_buffer_km: float
    boundary_simplify_tolerance_km: float
    max_snap_distance_factor: float


@dataclass(frozen=True)
class GeospatialBuildConfig:
    """Complete shared preprocessing build profile."""

    study_area: StudyAreaConfig
    basemaps: BasemapConfig
    adjacency: AdjacencyConfig
    roads: RoadConfig
    road_connectivity: RoadConnectivityConfig
    schema: SchemaConfig
    source_path: Path


# =============================================================================
# Validation helpers
# =============================================================================

def _require_table(
    raw: dict,
    key: str,
) -> dict:
    """Return a required TOML table."""

    value = raw.get(key)

    if not isinstance(value, dict):
        raise ValueError(
            f"Configuration section [{key}] is missing or invalid."
        )

    return value


def _require_string(
    table: dict,
    key: str,
    section: str,
) -> str:
    """Return a required non-empty string."""

    value = table.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"[{section}].{key} must be a non-empty string."
        )

    return value.strip()


def _require_bool(
    table: dict,
    key: str,
    section: str,
) -> bool:
    """Return a required boolean."""

    value = table.get(key)

    if not isinstance(value, bool):
        raise ValueError(
            f"[{section}].{key} must be true or false."
        )

    return value


def _require_int(
    table: dict,
    key: str,
    section: str,
    minimum: int | None = None,
) -> int:
    """Return a required integer with an optional lower bound."""

    value = table.get(key)

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"[{section}].{key} must be an integer."
        )

    if minimum is not None and value < minimum:
        raise ValueError(
            f"[{section}].{key} must be at least {minimum}."
        )

    return value



def _require_positive_number(
    table: dict,
    key: str,
    section: str,
) -> float:
    """Return a required positive numeric value."""

    value = table.get(key)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"[{section}].{key} must be a number."
        )

    numeric_value = float(value)

    if numeric_value <= 0:
        raise ValueError(
            f"[{section}].{key} must be greater than zero."
        )

    return numeric_value


def _require_string_list(
    table: dict,
    key: str,
    section: str,
) -> tuple[str, ...]:
    """Return a required list of non-empty strings."""

    value = table.get(key)

    if not isinstance(value, list) or not value:
        raise ValueError(
            f"[{section}].{key} must be a non-empty list."
        )

    if not all(
        isinstance(item, str) and item.strip()
        for item in value
    ):
        raise ValueError(
            f"[{section}].{key} must contain only non-empty strings."
        )

    return tuple(item.strip() for item in value)


def _require_positive_number_list(
    table: dict,
    key: str,
    section: str,
) -> tuple[float, ...]:
    """Return a required list of unique positive numeric values."""

    value = table.get(key)

    if not isinstance(value, list) or not value:
        raise ValueError(
            f"[{section}].{key} must be a non-empty list."
        )

    normalized: list[float] = []

    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(
                f"[{section}].{key} must contain only numbers."
            )

        numeric_value = float(item)

        if numeric_value <= 0:
            raise ValueError(
                f"[{section}].{key} values must be positive."
            )

        normalized.append(numeric_value)

    if len(normalized) != len(set(normalized)):
        raise ValueError(
            f"[{section}].{key} contains duplicate values."
        )

    return tuple(normalized)


def _validate_choice(
    value: str,
    allowed: set[str],
    field_name: str,
) -> str:
    """Validate a single enumerated configuration value."""

    if value not in allowed:
        raise ValueError(
            f"{field_name} must be one of {sorted(allowed)}; "
            f"received {value!r}."
        )

    return value


def _validate_choices(
    values: tuple[str, ...],
    allowed: set[str],
    field_name: str,
) -> tuple[str, ...]:
    """Validate a sequence of unique enumerated values."""

    if len(values) != len(set(values)):
        raise ValueError(
            f"{field_name} contains duplicate values: {values}"
        )

    unknown = sorted(set(values) - allowed)

    if unknown:
        raise ValueError(
            f"{field_name} contains unsupported values: {unknown}. "
            f"Valid values are: {sorted(allowed)}"
        )

    return values


def _normalize_study_area_label(label: str) -> str:
    """Normalize and validate a filename-safe study-area label."""

    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)

    if not re.fullmatch(r"[a-z0-9_]+", normalized):
        raise ValueError(
            "study_area.label may contain only letters, numbers, "
            "underscores, spaces, and hyphens."
        )

    return normalized


def _validate_provinces(
    provinces: tuple[str, ...],
) -> tuple[str, ...]:
    """Normalize and validate province and territory abbreviations."""

    normalized = tuple(
        code.upper().strip()
        for code in provinces
    )

    if len(normalized) != len(set(normalized)):
        raise ValueError(
            f"study_area.provinces contains duplicate codes: {normalized}"
        )

    unknown = sorted(
        set(normalized) - set(ALL_PROVINCE_CODES)
    )

    if unknown:
        raise ValueError(
            f"Unknown province or territory codes: {unknown}. "
            f"Valid codes are: {list(ALL_PROVINCE_CODES)}"
        )

    return normalized


def _validate_road_classes(
    backbone: tuple[str, ...],
    freight_access: tuple[str, ...],
) -> None:
    """Validate nested backbone and freight-access road classes."""

    if len(backbone) != len(set(backbone)):
        raise ValueError(
            "roads.classes.backbone contains duplicate values."
        )

    if len(freight_access) != len(set(freight_access)):
        raise ValueError(
            "roads.classes.freight_access contains duplicate values."
        )

    missing_backbone_classes = sorted(
        set(backbone) - set(freight_access)
    )

    if missing_backbone_classes:
        raise ValueError(
            "roads.classes.freight_access must include every backbone "
            f"class. Missing: {missing_backbone_classes}"
        )


# =============================================================================
# Configuration loading
# =============================================================================

def load_geospatial_build_config(
    config_path: Path,
) -> GeospatialBuildConfig:
    """Load and validate one Geospatial-CANOE preprocessing build profile."""

    config_path = config_path.expanduser().resolve()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Geospatial build configuration not found: {config_path}"
        )

    if not config_path.is_file():
        raise ValueError(
            f"Geospatial build configuration is not a file: {config_path}"
        )

    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    study_area_raw = _require_table(raw, "study_area")
    basemaps_raw = _require_table(raw, "basemaps")
    adjacency_raw = _require_table(raw, "adjacency")
    roads_raw = _require_table(raw, "roads")
    road_connectivity_raw = _require_table(
        raw,
        "road_connectivity",
    )
    schema_raw = _require_table(raw, "schema")

    geographic_raw = _require_table(
        basemaps_raw,
        "geographic",
    )
    projected_raw = _require_table(
        basemaps_raw,
        "projected",
    )
    road_classes_raw = _require_table(
        roads_raw,
        "classes",
    )

    study_area = StudyAreaConfig(
        label=_normalize_study_area_label(
            _require_string(
                study_area_raw,
                "label",
                "study_area",
            )
        ),
        provinces=_validate_provinces(
            _require_string_list(
                study_area_raw,
                "provinces",
                "study_area",
            )
        ),
    )

    grid_types = _validate_choices(
        _require_string_list(
            basemaps_raw,
            "grid_types",
            "basemaps",
        ),
        SUPPORTED_GRID_TYPES,
        "basemaps.grid_types",
    )

    basemaps = BasemapConfig(
        grid_types=grid_types,
        keep_method=_validate_choice(
            _require_string(
                basemaps_raw,
                "keep_method",
                "basemaps",
            ),
            SUPPORTED_KEEP_METHODS,
            "basemaps.keep_method",
        ),
        coordinate_precision=_require_int(
            basemaps_raw,
            "coordinate_precision",
            "basemaps",
            minimum=0,
        ),
        geographic=BasemapGridFamilyConfig(
            resolutions=_require_positive_number_list(
                geographic_raw,
                "resolutions",
                "basemaps.geographic",
            )
        ),
        projected=BasemapGridFamilyConfig(
            resolutions=_require_positive_number_list(
                projected_raw,
                "resolutions_km",
                "basemaps.projected",
            )
        ),
    )

    adjacency = AdjacencyConfig(
        method=_validate_choice(
            _require_string(
                adjacency_raw,
                "method",
                "adjacency",
            ),
            SUPPORTED_ADJACENCY_METHODS,
            "adjacency.method",
        ),
        coordinate_precision=_require_int(
            adjacency_raw,
            "coordinate_precision",
            "adjacency",
            minimum=0,
        ),
        no_neighbor_id=_require_string(
            adjacency_raw,
            "no_neighbor_id",
            "adjacency",
        ),
    )

    backbone_classes = _require_string_list(
        road_classes_raw,
        "backbone",
        "roads.classes",
    )
    freight_access_classes = _require_string_list(
        road_classes_raw,
        "freight_access",
        "roads.classes",
    )

    _validate_road_classes(
        backbone=backbone_classes,
        freight_access=freight_access_classes,
    )

    roads = RoadConfig(
        networks=_validate_choices(
            _require_string_list(
                roads_raw,
                "networks",
                "roads",
            ),
            SUPPORTED_ROAD_NETWORKS,
            "roads.networks",
        ),
        export_individual_provinces=_require_bool(
            roads_raw,
            "export_individual_provinces",
            "roads",
        ),
        output_crs=_require_string(
            roads_raw,
            "output_crs",
            "roads",
        ),
        classes=RoadClassConfig(
            backbone=backbone_classes,
            freight_access=freight_access_classes,
        ),
    )

    road_connectivity = RoadConnectivityConfig(
        road_layer=_validate_choice(
            _require_string(
                road_connectivity_raw,
                "road_layer",
                "road_connectivity",
            ),
            SUPPORTED_ROAD_LAYERS,
            "road_connectivity.road_layer",
        ),
        methods=_validate_choices(
            _require_string_list(
                road_connectivity_raw,
                "methods",
                "road_connectivity",
            ),
            SUPPORTED_ROAD_CONNECTIVITY_METHODS,
            "road_connectivity.methods",
        ),
        plot_outputs=_require_bool(
            road_connectivity_raw,
            "plot_outputs",
            "road_connectivity",
        ),
    )

    basemap_stem_value = schema_raw.get("basemap_stem")

    if basemap_stem_value is not None:
        if (
            not isinstance(basemap_stem_value, str)
            or not basemap_stem_value.strip()
        ):
            raise ValueError(
                "[schema].basemap_stem must be a non-empty string "
                "or omitted."
            )

        basemap_stem = basemap_stem_value.strip()
    else:
        basemap_stem = None

    schema = SchemaConfig(
        road_connection_method=_validate_choice(
            _require_string(
                schema_raw,
                "road_connection_method",
                "schema",
            ),
            SUPPORTED_ROAD_CONNECTIVITY_METHODS,
            "schema.road_connection_method",
        ),
        interactive_basemap_selection=_require_bool(
            schema_raw,
            "interactive_basemap_selection",
            "schema",
        ),
        basemap_stem=basemap_stem,
        boundary_buffer_km=_require_positive_number(
            schema_raw,
            "boundary_buffer_km",
            "schema",
        ),
        boundary_simplify_tolerance_km=_require_positive_number(
            schema_raw,
            "boundary_simplify_tolerance_km",
            "schema",
        ),
        max_snap_distance_factor=_require_positive_number(
            schema_raw,
            "max_snap_distance_factor",
            "schema",
        ),
    )

    if (
        schema.road_connection_method
        not in road_connectivity.methods
    ):
        raise ValueError(
            "schema.road_connection_method must also appear in "
            "road_connectivity.methods."
        )

    if (
        not schema.interactive_basemap_selection
        and schema.basemap_stem is None
    ):
        raise ValueError(
            "schema.basemap_stem is required when "
            "interactive_basemap_selection is false."
        )

    if (
        road_connectivity.road_layer
        not in roads.networks
    ):
        raise ValueError(
            "road_connectivity.road_layer must also appear in "
            "roads.networks."
        )

    return GeospatialBuildConfig(
        study_area=study_area,
        basemaps=basemaps,
        adjacency=adjacency,
        roads=roads,
        road_connectivity=road_connectivity,
        schema=schema,
        source_path=config_path,
    )


def print_build_config(
    config: GeospatialBuildConfig,
) -> None:
    """Print a compact summary of a loaded preprocessing build profile."""

    print("\n" + "=" * 78)
    print("Geospatial-CANOE build profile")
    print("=" * 78)
    print(f"Source:                  {config.source_path}")
    print(f"Study area:              {config.study_area.label}")
    print(
        "Provinces/territories:  "
        + ", ".join(config.study_area.provinces)
    )
    print(
        "Grid types:             "
        + ", ".join(config.basemaps.grid_types)
    )
    print(
        "Geographic resolutions: "
        + ", ".join(
            f"{value:g}°"
            for value in config.basemaps.geographic_resolutions_deg
        )
    )
    print(
        "Projected resolutions:  "
        + ", ".join(
            f"{value:g} km"
            for value in config.basemaps.projected_resolutions_km
        )
    )
    print(
        "Road networks:          "
        + ", ".join(config.roads.networks)
    )
    print(
        "Connectivity methods:   "
        + ", ".join(config.road_connectivity.methods)
    )
    print(
        "Schema road method:     "
        f"{config.schema.road_connection_method}"
    )
    print(
        "Point boundary buffer:  "
        f"{config.schema.boundary_buffer_km:g} km"
    )
    print(
        "Buffer simplification:  "
        f"{config.schema.boundary_simplify_tolerance_km:g} km"
    )
    print(
        "Max snap factor:        "
        f"{config.schema.max_snap_distance_factor:g} × grid resolution"
    )


def default_config_path(
    project_root: Path,
) -> Path:
    """Return the canonical default preprocessing build-profile path."""

    return project_root / DEFAULT_CONFIG_RELATIVE_PATH
