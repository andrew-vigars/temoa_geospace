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
    "primary_freight",
    "freight_access",
}

SUPPORTED_ROAD_CONNECTIVITY_METHODS = {
    "weak",
    "strong",
}

SUPPORTED_ROAD_LAYERS = {
    "backbone",
    "primary_freight",
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
    """Immutable study-area identity for geospatial preprocessing.

    Attributes
    ----------
    label : str
        Canonical label used to identify the study area in filenames, metadata,
        summaries, and downstream workflow products.
    provinces : tuple[str, ...]
        Ordered province and territory codes included in the study area.
    """

    label: str
    provinces: tuple[str, ...]


@dataclass(frozen=True)
class BasemapGridFamilyConfig:
    """Immutable resolution settings for one basemap grid family.

    Attributes
    ----------
    resolutions : tuple[float, ...]
        Ordered grid resolutions to generate for the associated basemap family,
        expressed in that family's configured native unit.
    """

    resolutions: tuple[float, ...]


@dataclass(frozen=True)
class BasemapConfig:
    """Immutable settings for basemap generation.

    Attributes
    ----------
    grid_types : tuple[str, ...]
        Basemap grid families enabled for generation, such as ``"geographic"``
        and ``"projected"``.
    keep_method : str
        Spatial retention rule used to determine which candidate grid cells are
        retained within the study area.
    coordinate_precision : int
        Number of decimal places used when rounding generated grid coordinates.
    geographic : BasemapGridFamilyConfig
        Resolution settings for geographic grids generated in EPSG:4326.
    projected : BasemapGridFamilyConfig
        Resolution settings for projected grids generated in EPSG:3347.
    """

    grid_types: tuple[str, ...]
    keep_method: str
    coordinate_precision: int
    geographic: BasemapGridFamilyConfig
    projected: BasemapGridFamilyConfig

    @property
    def geographic_resolutions_deg(self) -> tuple[float, ...]:
        """Return configured geographic-grid resolutions in decimal degrees.

        Returns
        -------
        tuple[float, ...]
            Geographic basemap resolutions configured for EPSG:4326.
        """

        return self.geographic.resolutions

    @property
    def projected_resolutions_km(self) -> tuple[float, ...]:
        """Return configured projected-grid resolutions in kilometres.

        Returns
        -------
        tuple[float, ...]
            Projected basemap resolutions configured for EPSG:3347.
        """

        return self.projected.resolutions


@dataclass(frozen=True)
class AdjacencyConfig:
    """Immutable settings for region-adjacency construction.

    Attributes
    ----------
    method : str
        Adjacency rule used to connect neighbouring graph regions, such as
        ``"rook"``.
    coordinate_precision : int
        Number of decimal places used when normalizing or comparing region
        coordinates during adjacency construction.
    no_neighbor_id : str
        Sentinel identifier used when a region has no neighbour in a given
        direction.
    """

    method: str
    coordinate_precision: int
    no_neighbor_id: str


@dataclass(frozen=True)
class RoadClassConfig:
    """Immutable road-class selections for processed network representations.

    Attributes
    ----------
    backbone : tuple[str, ...]
        Ordered source road classes included in the backbone network.
    primary_freight : tuple[str, ...]
        Ordered source road classes included in the primary-freight network.
    freight_access : tuple[str, ...]
        Ordered source road classes included in the freight-access network.
    """

    backbone: tuple[str, ...]
    primary_freight: tuple[str, ...]
    freight_access: tuple[str, ...]


@dataclass(frozen=True)
class RoadConfig:
    """Immutable settings for processed road-network construction.

    Attributes
    ----------
    networks : tuple[str, ...]
        Ordered processed road-network representations to generate, such as
        ``"backbone"``, ``"primary_freight"``, and ``"freight_access"``.
    export_individual_provinces : bool
        Whether filtered road-network outputs are also written separately for each
        selected province or territory.
    output_crs : str
        Coordinate reference system assigned to merged processed road outputs.
    classes : RoadClassConfig
        Road-class definitions used to construct each processed network
        representation.
    """

    networks: tuple[str, ...]
    export_individual_provinces: bool
    output_crs: str
    classes: RoadClassConfig


@dataclass(frozen=True)
class RoadConnectivityConfig:
    """Immutable settings for mapping roads onto graph regions.

    Attributes
    ----------
    road_layer : str
        Processed road-network layer used for spatial overlay and connectivity
        construction, such as ``"backbone"``, ``"primary_freight"``, or
        ``"freight_access"``.
    methods : tuple[str, ...]
        Ordered road-connectivity methods to generate, such as ``"weak"`` and
        ``"strong"``.
    plot_outputs : bool
        Whether diagnostic road-presence and edge-connectivity figures are
        generated for each processed graph.
    """

    road_layer: str
    methods: tuple[str, ...]
    plot_outputs: bool


@dataclass(frozen=True)
class SchemaConfig:
    """Immutable schema-selection and point-assignment settings.

    Attributes
    ----------
    road_connection_method : str
        Road-connectivity method used when selecting road-enabled graph edges for
        schema construction.
    interactive_basemap_selection : bool
        Whether the schema-building workflow prompts the user to select a basemap
        interactively at runtime.
    basemap_stem : str | None
        Explicit basemap filename stem used when interactive selection is disabled,
        or ``None`` when selection is performed interactively.
    boundary_buffer_km : float
        Buffer distance, in kilometres, applied to the study-area boundary during
        point-assignment preprocessing.
    boundary_simplify_tolerance_km : float
        Simplification tolerance, in kilometres, applied to buffered boundary
        geometry before point-assignment operations.
    max_snap_distance_factor : float
        Multiplier applied to the selected grid resolution to determine the maximum
        permitted point-to-node snapping distance.
    """

    road_connection_method: str
    interactive_basemap_selection: bool
    basemap_stem: str | None
    boundary_buffer_km: float
    boundary_simplify_tolerance_km: float
    max_snap_distance_factor: float


@dataclass(frozen=True)
class GeospatialBuildConfig:
    """Immutable shared configuration for the geospatial preprocessing workflow.

    Attributes
    ----------
    study_area : StudyAreaConfig
        Study-area identity and included province or territory codes.
    basemaps : BasemapConfig
        Basemap grid families, resolutions, retention method, and coordinate
        precision.
    adjacency : AdjacencyConfig
        Region-adjacency construction method and related identifier settings.
    roads : RoadConfig
        Processed road-network representations, road classes, export behaviour, and
        output CRS.
    road_connectivity : RoadConnectivityConfig
        Road layer, connectivity methods, and diagnostic plotting preferences.
    schema : SchemaConfig
        Schema-selection, boundary-processing, and point-snapping settings.
    source_path : Path
        Path to the TOML build profile from which the configuration was loaded.
    """

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
    """Return a required TOML table from the parsed configuration.

    Parameters
    ----------
    raw : dict
        Parsed TOML configuration mapping.
    key : str
        Name of the required top-level TOML table.

    Returns
    -------
    dict
        Mapping stored under ``key``.

    Raises
    ------
    ValueError
        If the requested configuration section is missing or is not represented
        as a dictionary.
    """

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
    """Return a required non-empty string from a TOML section.

    The requested value is validated as a string, stripped of leading and trailing
    whitespace, and returned in normalized form.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required string setting.
    section : str
        TOML section name used to construct validation error messages.

    Returns
    -------
    str
        Stripped non-empty string stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing, is not a string, or contains only whitespace.
    """

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
    """Return a required boolean from a TOML section.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required boolean setting.
    section : str
        TOML section name used to construct validation error messages.

    Returns
    -------
    bool
        Boolean value stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing or is not a boolean.
    """

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
    """Return a required integer from a TOML section.

    Boolean values are rejected explicitly because ``bool`` is a subclass of
    ``int`` in Python. An optional inclusive lower bound can also be enforced.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required integer setting.
    section : str
        TOML section name used to construct validation error messages.
    minimum : int | None, optional
        Inclusive lower bound for the setting, or ``None`` when no lower bound is
        required.

    Returns
    -------
    int
        Validated integer value stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing, is a boolean, is not an integer, or is smaller
        than ``minimum`` when a lower bound is provided.
    """

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
    """Return a required positive numeric value from a TOML section.

    Boolean values are rejected explicitly because ``bool`` is a subclass of
    ``int`` in Python. Valid integer and floating-point values are normalized to
    ``float`` before the positivity constraint is applied.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required numeric setting.
    section : str
        TOML section name used to construct validation error messages.

    Returns
    -------
    float
        Validated positive numeric value stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing, is a boolean, is not numeric, or is less than
        or equal to zero.
    """

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
    """Return a required list of non-empty strings from a TOML section.

    The requested value is validated as a non-empty list whose elements are all
    non-empty strings. Each string is stripped of leading and trailing whitespace,
    and the normalized values are returned as an immutable tuple.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required string-list setting.
    section : str
        TOML section name used to construct validation error messages.

    Returns
    -------
    tuple[str, ...]
        Normalized non-empty strings stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing, is not a non-empty list, or contains a value
        that is not a non-empty string.
    """

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
    """Return a required list of unique positive numbers from a TOML section.

    The requested value is validated as a non-empty list containing only integer
    or floating-point values. Boolean values are rejected explicitly, each entry is
    normalized to ``float``, positivity is enforced, and duplicate numeric values
    are not permitted.

    Parameters
    ----------
    table : dict
        Parsed TOML section containing the requested setting.
    key : str
        Name of the required numeric-list setting.
    section : str
        TOML section name used to construct validation error messages.

    Returns
    -------
    tuple[float, ...]
        Normalized unique positive values stored under ``key``.

    Raises
    ------
    ValueError
        If the setting is missing, is not a non-empty list, contains a boolean or
        non-numeric value, contains a value less than or equal to zero, or contains
        duplicate numeric values.
    """

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
    """Validate and return a single enumerated configuration value.

    Parameters
    ----------
    value : str
        Configuration value to validate.
    allowed : set[str]
        Permitted values for the configuration field.
    field_name : str
        Human-readable field name used to construct validation error messages.

    Returns
    -------
    str
        The validated configuration value.

    Raises
    ------
    ValueError
        If ``value`` is not present in ``allowed``.
    """

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
    """Validate and return a sequence of unique enumerated values.

    The supplied values are checked for duplicates before being compared with the
    set of permitted values. Unsupported entries are reported in sorted order to
    produce deterministic validation messages.

    Parameters
    ----------
    values : tuple[str, ...]
        Ordered configuration values to validate.
    allowed : set[str]
        Permitted values for the configuration field.
    field_name : str
        Human-readable field name used to construct validation error messages.

    Returns
    -------
    tuple[str, ...]
        The original validated tuple, preserving its input order.

    Raises
    ------
    ValueError
        If ``values`` contains duplicate entries or any value not present in
        ``allowed``.
    """

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
    """Normalize and validate a filename-safe study-area label.

    Leading and trailing whitespace is removed, letters are converted to lowercase,
    and spaces and hyphens are replaced with underscores. Consecutive underscores
    are collapsed before the normalized label is validated.

    Parameters
    ----------
    label : str
        User-provided study-area label.

    Returns
    -------
    str
        Normalized lowercase label containing only letters, numbers, and
        underscores.

    Raises
    ------
    ValueError
        If the normalized label contains characters other than lowercase letters,
        numbers, or underscores.
    """

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
    """Normalize and validate province and territory abbreviations.

    Each supplied code is stripped of surrounding whitespace and converted to
    uppercase. The normalized codes are then checked for duplicates and validated
    against the complete set of supported Canadian province and territory
    abbreviations.

    Parameters
    ----------
    provinces : tuple[str, ...]
        Province and territory abbreviations to normalize and validate.

    Returns
    -------
    tuple[str, ...]
        Normalized uppercase province and territory codes, preserving the original
        input order.

    Raises
    ------
    ValueError
        If the normalized sequence contains duplicate codes or any code not present
        in ``ALL_PROVINCE_CODES``.
    """

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
    primary_freight: tuple[str, ...],
    freight_access: tuple[str, ...],
) -> None:
    """Validate uniqueness and nesting of configured road classes.

    Each road-class sequence must contain unique values. The configured
    networks must also form the nested hierarchy:

    ``backbone`` ⊆ ``primary_freight`` ⊆ ``freight_access``

    Parameters
    ----------
    backbone : tuple[str, ...]
        Ordered road classes included in the backbone network.
    primary_freight : tuple[str, ...]
        Ordered road classes included in the primary-freight network.
    freight_access : tuple[str, ...]
        Ordered road classes included in the freight-access network.

    Raises
    ------
    ValueError
        If any sequence contains duplicate values or the configured road
        classes do not follow the required nested hierarchy.
    """

    road_class_groups = {
        "backbone": backbone,
        "primary_freight": primary_freight,
        "freight_access": freight_access,
    }

    for network_name, road_classes in road_class_groups.items():
        if len(road_classes) != len(set(road_classes)):
            raise ValueError(
                f"roads.classes.{network_name} contains duplicate values."
            )

    missing_backbone_classes = sorted(
        set(backbone) - set(primary_freight)
    )

    if missing_backbone_classes:
        raise ValueError(
            "roads.classes.primary_freight must include every backbone "
            f"class. Missing: {missing_backbone_classes}"
        )

    missing_primary_freight_classes = sorted(
        set(primary_freight) - set(freight_access)
    )

    if missing_primary_freight_classes:
        raise ValueError(
            "roads.classes.freight_access must include every "
            "primary-freight class. "
            f"Missing: {missing_primary_freight_classes}"
        )


# =============================================================================
# Configuration loading
# =============================================================================

def load_geospatial_build_config(
    config_path: Path,
) -> GeospatialBuildConfig:
    """Load, validate, and normalize a geospatial preprocessing build profile.

    The TOML file is resolved to an absolute path, parsed into its required
    configuration sections, and converted into immutable configuration dataclasses.
    Individual settings are type-checked, normalized, and validated against the
    supported grid, adjacency, road-network, and connectivity options.

    Cross-section consistency is also enforced. The schema road-connectivity method
    must be enabled by the road-connectivity configuration, non-interactive basemap
    selection requires an explicit basemap stem, and the selected road-connectivity
    layer must be included among the processed road networks.

    Parameters
    ----------
    config_path : Path
        Path to the TOML geospatial preprocessing build profile.

    Returns
    -------
    GeospatialBuildConfig
        Fully validated and normalized build configuration containing study-area,
        basemap, adjacency, road, road-connectivity, schema, and source-path
        settings.

    Raises
    ------
    FileNotFoundError
        If ``config_path`` does not exist.
    ValueError
        If ``config_path`` is not a file, a required TOML section or setting is
        missing or invalid, an enumerated value is unsupported, configured lists
        contain duplicates, province or territory codes are invalid, road classes
        are inconsistently nested, or related settings across configuration
        sections are incompatible.
    tomllib.TOMLDecodeError
        If the configuration file contains invalid TOML syntax.
    """

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

    primary_freight_classes = _require_string_list(
        road_classes_raw,
        "primary_freight",
        "roads.classes",
    )

    freight_access_classes = _require_string_list(
        road_classes_raw,
        "freight_access",
        "roads.classes",
    )

    _validate_road_classes(
        backbone=backbone_classes,
        primary_freight=primary_freight_classes,
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
            primary_freight=primary_freight_classes,
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
    """Print a compact summary of a loaded geospatial build profile.

    The summary reports the configuration source, study-area membership, enabled
    grid families and resolutions, processed road networks, road-connectivity
    methods, and key schema point-assignment settings.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial preprocessing configuration to summarize.
    """

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
    """Return the canonical path to the default preprocessing build profile.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE project.

    Returns
    -------
    Path
        Path formed by joining ``project_root`` with
        ``DEFAULT_CONFIG_RELATIVE_PATH``.
    """

    return project_root / DEFAULT_CONFIG_RELATIVE_PATH
