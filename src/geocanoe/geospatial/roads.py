"""
build_roads.py

Stage 3 of the Geospatial-CANOE workflow.

This script reads the downloaded raw National Road Network (NRN)
GeoPackages for the provinces and territories selected in a shared TOML
build profile. It filters and merges those jurisdictions into three nested
study-area road-network representations and exports profile-labelled
GeoPackage and summary products.

Source:
    https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f

Both English and French versions of the NRN are available, but this script
uses the English version only.

Three nested road-network representations are constructed:

1. Backbone network:
    - Freeway
    - Expressway / Highway
    - Ramp

2. Primary-freight network:
    - Freeway
    - Expressway / Highway
    - Ramp
    - Arterial

3. Freight-access network:
    - Freeway
    - Expressway / Highway
    - Ramp
    - Arterial
    - Collector
    - Local / Street
    - Local / Unknown

Inputs
------
data_files/raw/nrn/{PROVINCE}/*_en.gpkg

Outputs
-------
data_files/processed/nrn/
    {PROVINCE}_filtered_road_networks.gpkg  [optional]
    {study_area}_filtered_road_networks.gpkg
    {study_area}_filtered_road_network_summary.csv
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sqlite3

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from geocanoe.config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)
from geocanoe.paths import find_project_root


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = find_project_root()

RAW_NRN = PROJECT_ROOT / "data_files" / "raw" / "nrn"
PROCESSED_NRN = PROJECT_ROOT / "data_files" / "processed" / "nrn"

# =============================================================================
# Helpers
# =============================================================================

def get_nrn_gpkg_path(province: str) -> Path:
    """Return the raw English NRN GeoPackage path for one province or territory.

    The Stage 3 road preprocessing workflow expects each province or territory
    directory under ``RAW_NRN`` to contain exactly one English National Road
    Network GeoPackage matching ``*_en.gpkg``. This function normalizes the
    province code, checks that the expected directory exists, and fails early if
    the GeoPackage input is missing or ambiguous.

    Parameters
    ----------
    province : str
        Province or territory code, such as ``"ON"``, ``"AB"``, or ``"YT"``.

    Returns
    -------
    Path
        Path to the single English NRN GeoPackage for the requested province or
        territory.

    Raises
    ------
    FileNotFoundError
        If the province or territory directory does not exist, or if it
        contains no English NRN GeoPackage.
    ValueError
        If more than one English NRN GeoPackage is found in the directory.
    """

    province = province.upper()
    province_dir = RAW_NRN / province

    if not province_dir.is_dir():
        raise FileNotFoundError(
            f"No directory found for {province} at {province_dir}. "
            f"Check RAW_NRN and that the data has been downloaded."
        )

    matches = list(province_dir.glob("*_en.gpkg"))

    if len(matches) == 0:
        raise FileNotFoundError(
            f"Directory {province_dir} exists but contains no '*_en.gpkg' file."
        )

    if len(matches) > 1:
        raise ValueError(
            f"Expected exactly one English GPKG file for {province}, "
            f"found {len(matches)}: {[m.name for m in matches]}"
        )

    return matches[0]


def get_gpkg_contents(gpkg_path: Path) -> pd.DataFrame:
    """Read the GeoPackage contents metadata table.

    This function opens a GeoPackage as a SQLite database and returns its
    ``gpkg_contents`` table. The contents table lists the layers available in
    the file and is used downstream to identify the provincial ``ROADSEG``
    layer without hard-coding province-specific table names.

    Parameters
    ----------
    gpkg_path : Path
        Path to the NRN GeoPackage to inspect.

    Returns
    -------
    pd.DataFrame
        GeoPackage contents table listing available layers and associated
        metadata.

    Raises
    ------
    FileNotFoundError
        If the GeoPackage file does not exist.
    """

    if not gpkg_path.exists():
        raise FileNotFoundError(f"GeoPackage not found: {gpkg_path}")

    with sqlite3.connect(gpkg_path) as conn:
        contents = pd.read_sql_query(
            """
            SELECT *
            FROM gpkg_contents
            """,
            conn,
        )

    return contents


def get_roadseg_table(gpkg_path: Path) -> str:
    """Find the NRN road-segment layer name in a provincial GeoPackage.

    Provincial NRN GeoPackages use layer names that can vary by province or
    release, but the road segment layer is expected to end with ``"_ROADSEG"``.
    This function reads the GeoPackage contents table and returns the single
    matching road segment layer name used for road-class filtering.

    Parameters
    ----------
    gpkg_path : Path
        Path to the provincial or territorial NRN GeoPackage.

    Returns
    -------
    str
        Name of the single ``ROADSEG`` layer contained in the GeoPackage.

    Raises
    ------
    ValueError
        If no ``ROADSEG`` layer is found, or if more than one matching layer is
        present.
    """

    contents = get_gpkg_contents(gpkg_path)

    matches = contents[
        contents["table_name"].str.endswith("_ROADSEG")
    ]["table_name"].tolist()

    if len(matches) == 0:
        raise ValueError(
            f"No ROADSEG table found in {gpkg_path.name}. "
            f"Available tables: {contents['table_name'].tolist()}"
        )

    if len(matches) > 1:
        raise ValueError(
            f"Expected exactly one ROADSEG table in {gpkg_path.name}, "
            f"found {len(matches)}: {matches}"
        )

    return matches[0]


def get_roadclass_counts(
    gpkg_path: Path,
    roadseg_table: str,
) -> pd.DataFrame:
    """Count NRN road segments by road class for one GeoPackage.

    This function queries the provincial ``ROADSEG`` layer directly through
    SQLite and returns the number of road segments in each source ``ROADCLASS``.
    These counts provide the unfiltered baseline used later to summarize how
    much of each provincial road network is retained in the backbone and
    freight-access subsets.

    Parameters
    ----------
    gpkg_path : Path
        Path to the provincial or territorial NRN GeoPackage.
    roadseg_table : str
        Name of the ``ROADSEG`` layer to query.

    Returns
    -------
    pd.DataFrame
        Table with one row per ``ROADCLASS`` and a ``segments`` count, sorted
        from most to fewest segments.
    """
    query = f"""
        SELECT ROADCLASS,
               COUNT(*) AS segments
        FROM "{roadseg_table}"
        GROUP BY ROADCLASS
        ORDER BY segments DESC;
    """

    with sqlite3.connect(gpkg_path) as conn:
        counts = pd.read_sql_query(query, conn)

    return counts


def deduplicate_boundary_segments(
    gdf: gpd.GeoDataFrame,
    priority_classes: Sequence[str],
) -> gpd.GeoDataFrame:
    """Remove duplicate road geometries after provincial networks are merged.

    Some NRN road segments can appear more than once when provincial and
    territorial road networks are concatenated, especially near shared
    boundaries. This function keeps one row per exact geometry and resolves
    duplicates by preferring lower-index ``ROADCLASS`` values from
    ``priority_classes``.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Road network GeoDataFrame containing a ``ROADCLASS`` column and road
        segment geometries.
    priority_classes : Sequence[str]
        Ordered road-class priority list. Classes earlier in the sequence are
        preferred when duplicate geometries are found.

    Returns
    -------
    gpd.GeoDataFrame
        Deduplicated road network with one row per exact road geometry.
    """

    priority = {
        road_class: index
        for index, road_class in enumerate(priority_classes)
    }

    default_priority = len(priority_classes)

    working = gdf.copy()

    working["_priority"] = (
        working["ROADCLASS"]
        .map(priority)
        .fillna(default_priority)
    )

    working["_geom_wkt"] = working.geometry.apply(lambda geometry: geometry.wkt)

    deduped = (
        working
        .sort_values("_priority")
        .drop_duplicates(subset="_geom_wkt", keep="first")
        .drop(columns=["_priority", "_geom_wkt"])
        .reset_index(drop=True)
    )

    return deduped


def validate_road_network(
    gdf: gpd.GeoDataFrame,
    name: str,
) -> None:
    """Print geometry quality diagnostics for a road network.

    This function reports basic geometry sanity checks for a filtered road
    network, including null geometries, empty geometries, invalid geometries,
    and exact duplicate geometries based on WKT representation. It is intended
    as a diagnostic check after network filtering or study-area concatenation.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Road network GeoDataFrame to inspect.
    name : str
        Human-readable network name used in printed diagnostics.

    Returns
    -------
    None
    """

    print(f"--- {name} ---")

    geometry = gdf.geometry

    non_null_geometry: list[BaseGeometry] = [
        geometry_item
        for geometry_item in geometry.array
        if isinstance(geometry_item, BaseGeometry)
    ]

    null_geometry = len(geometry) - len(non_null_geometry)

    empty_geometry = sum(
        geometry_item.is_empty
        for geometry_item in non_null_geometry
    )

    invalid_geometry = sum(
        not geometry_item.is_valid
        for geometry_item in non_null_geometry
    )

    duplicate_geometry = int(
        pd.Series(
            [
                geometry_item.wkt
                for geometry_item in non_null_geometry
            ],
            dtype="string",
        )
        .duplicated()
        .sum()
    )

    print(f"Null geometries: {null_geometry:,}")
    print(f"Empty geometries: {empty_geometry:,}")
    print(f"Invalid geometries: {invalid_geometry:,}")
    print(
        "Duplicate geometries (identical WKT): "
        f"{duplicate_geometry:,}"
    )
    print()


def load_filtered_provincial_networks(
    config: GeospatialBuildConfig,
) -> tuple[
    dict[str, dict[str, gpd.GeoDataFrame]],
    pd.DataFrame,
    dict[str, CRS],
]:
    """Load and filter provincial NRN road networks for one study area.

    For each selected province or territory, this function locates the raw
    English National Road Network GeoPackage, identifies its ``ROADSEG`` layer,
    and records source segment counts by ``ROADCLASS``.

    The broadest configured network, ``freight_access``, is read directly from
    the GeoPackage. All narrower configured road networks are then derived from
    that table using their corresponding road-class definitions. This supports
    a nested hierarchy such as:

    ``backbone`` ⊆ ``primary_freight`` ⊆ ``freight_access``

    Individual jurisdictions may legitimately contain no segments for a
    narrower network. For example, Nunavut may contain freight-access roads but
    no backbone or primary-freight roads. An error is raised only when the
    broadest freight-access network is empty.

    Study-area metadata is added to each retained road segment. The function
    also validates that every selected source network has a defined coordinate
    reference system and that all selected GeoPackages use a common CRS.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the selected jurisdictions,
        study-area label, enabled road networks, and road-class definitions.

    Returns
    -------
    tuple[
        dict[str, dict[str, gpd.GeoDataFrame]],
        pd.DataFrame,
        dict[str, CRS],
    ]
        Three objects containing:

        - Nested provincial road networks indexed first by jurisdiction and then
          by network name.
        - Combined source ``ROADCLASS`` segment counts for all selected
          jurisdictions.
        - Source coordinate reference systems keyed by jurisdiction code.

    Raises
    ------
    FileNotFoundError
        If a selected jurisdiction does not have the expected English NRN
        GeoPackage.
    ValueError
        If a GeoPackage has an invalid or ambiguous ``ROADSEG`` layer, the
        configured freight-access classes produce no segments, an enabled
        network has no corresponding class configuration, a source network has
        no CRS, or the selected GeoPackages do not share one common CRS.
    """

    selected_provinces = list(config.study_area.provinces)
    enabled_networks = tuple(config.roads.networks)

    if "freight_access" not in enabled_networks:
        raise ValueError(
            "The road build requires 'freight_access' to be enabled because it "
            "is used as the broadest source network from which nested networks "
            "are derived."
        )

    network_classes: dict[str, tuple[str, ...]] = {}

    for network_name in enabled_networks:
        if not hasattr(config.roads.classes, network_name):
            raise ValueError(
                f"No road-class configuration was found for enabled network "
                f"{network_name!r}."
            )

        classes = tuple(
            getattr(config.roads.classes, network_name)
        )

        if not classes:
            raise ValueError(
                f"Configured road network {network_name!r} has no road classes."
            )

        network_classes[network_name] = classes

    freight_access_classes = network_classes["freight_access"]

    # Escape embedded single quotes before constructing the SQLite WHERE clause.
    quoted_freight_classes = [
        "'" + road_class.replace("'", "''") + "'"
        for road_class in freight_access_classes
    ]

    freight_access_where = (
        "ROADCLASS IN ("
        + ", ".join(quoted_freight_classes)
        + ")"
    )

    provincial_networks: dict[
        str,
        dict[str, gpd.GeoDataFrame],
    ] = {}

    provincial_roadclass_counts: list[pd.DataFrame] = []
    provincial_crs: dict[str, CRS] = {}

    for province in selected_provinces:
        gpkg_path = get_nrn_gpkg_path(province)
        roadseg_table = get_roadseg_table(gpkg_path)

        roadclass_counts = get_roadclass_counts(
            gpkg_path=gpkg_path,
            roadseg_table=roadseg_table,
        )

        roadclass_counts.insert(
            0,
            "province",
            province,
        )
        provincial_roadclass_counts.append(roadclass_counts)

        full_count = int(
            roadclass_counts["segments"].sum()
        )

        freight_access = gpd.read_file(
            gpkg_path,
            layer=roadseg_table,
            where=freight_access_where,
        )

        if freight_access.empty:
            available_classes = (
                roadclass_counts["ROADCLASS"]
                .dropna()
                .astype(str)
                .tolist()
            )

            raise ValueError(
                "No configured freight-access road classes were found for "
                f"{province}. "
                f"Configured classes: {list(freight_access_classes)}. "
                f"Available ROADCLASS values: {available_classes}."
            )

        source_crs = freight_access.crs

        if source_crs is None:
            raise ValueError(
                f"Selected provincial GeoPackage has no CRS: {province}"
            )

        freight_access = freight_access.copy()

        freight_access["province"] = province
        freight_access["study_area"] = config.study_area.label
        freight_access["province_codes"] = ",".join(
            selected_provinces
        )

        networks_for_province: dict[
            str,
            gpd.GeoDataFrame,
        ] = {}

        for network_name in enabled_networks:
            road_classes = network_classes[network_name]

            network = freight_access.loc[
                freight_access["ROADCLASS"].isin(road_classes)
            ].copy()

            networks_for_province[network_name] = network

        provincial_networks[province] = networks_for_province
        provincial_crs[province] = source_crs

        network_counts = " | ".join(
            (
                f"{len(networks_for_province[network_name]):,} "
                f"{network_name.replace('_', '-')}"
            )
            for network_name in enabled_networks
        )

        print(
            f"{province}: "
            f"{full_count:,} total | "
            f"{network_counts}"
        )

    roadclass_counts_all = pd.concat(
        provincial_roadclass_counts,
        ignore_index=True,
    )

    unique_crs = {
        crs.to_string()
        for crs in provincial_crs.values()
    }

    if len(unique_crs) != 1:
        crs_by_province = {
            province: crs.to_string()
            for province, crs in provincial_crs.items()
        }

        raise ValueError(
            "Selected provincial GeoPackages do not share a common CRS: "
            f"{crs_by_province}"
        )

    common_crs = next(iter(unique_crs))

    print(
        f"\nAll selected jurisdictions share CRS: {common_crs}"
    )

    return (
        provincial_networks,
        roadclass_counts_all,
        provincial_crs,
    )


def build_study_area_networks(
    provincial_networks: dict[
        str,
        dict[str, gpd.GeoDataFrame],
    ],
    provincial_crs: dict[str, CRS],
    config: GeospatialBuildConfig,
) -> dict[str, gpd.GeoDataFrame]:
    """Merge provincial road layers into study-area road networks.

    Each road-network representation enabled in the build profile is assembled
    by concatenating the corresponding provincial and territorial layers.
    Jurisdictions with no segments in a narrower network are skipped rather
    than treated as errors.

    Duplicate geometries occurring along jurisdictional boundaries are removed
    using the configured road-class ordering for each network. The resulting
    study-area networks are reprojected to the configured output CRS and
    returned in a dictionary keyed by network name.

    Parameters
    ----------
    provincial_networks : dict[str, dict[str, gpd.GeoDataFrame]]
        Nested road-network mapping indexed first by jurisdiction code and then
        by configured network name.
    provincial_crs : dict[str, CRS]
        Source coordinate reference systems keyed by jurisdiction code.
    config : GeospatialBuildConfig
        Validated build profile containing the selected jurisdictions, enabled
        road networks, road-class definitions, and output CRS.

    Returns
    -------
    dict[str, gpd.GeoDataFrame]
        Deduplicated and reprojected study-area road networks keyed by network
        name.

    Raises
    ------
    ValueError
        If no jurisdictions are selected, an enabled network has no class
        configuration, no segments exist for an enabled network across the
        complete study area, or a configured jurisdiction or network is missing
        from ``provincial_networks``.
    """

    selected_provinces = list(config.study_area.provinces)
    enabled_networks = tuple(config.roads.networks)

    if not selected_provinces:
        raise ValueError(
            "No provinces or territories were selected for the study area."
        )

    source_crs = provincial_crs[selected_provinces[0]]

    network_classes: dict[str, tuple[str, ...]] = {}

    for network_name in enabled_networks:
        if not hasattr(config.roads.classes, network_name):
            raise ValueError(
                f"No road-class configuration was found for enabled network "
                f"{network_name!r}."
            )

        network_classes[network_name] = tuple(
            getattr(config.roads.classes, network_name)
        )

    study_area_networks: dict[str, gpd.GeoDataFrame] = {}

    for network_name in enabled_networks:
        provincial_layers: list[gpd.GeoDataFrame] = []

        for province in selected_provinces:
            if province not in provincial_networks:
                raise ValueError(
                    f"Provincial road networks are missing jurisdiction "
                    f"{province!r}."
                )

            if network_name not in provincial_networks[province]:
                raise ValueError(
                    f"Jurisdiction {province!r} is missing configured network "
                    f"{network_name!r}."
                )

            provincial_layer = provincial_networks[province][network_name]

            if provincial_layer.empty:
                print(
                    f"{province}: no {network_name.replace('_', '-')} "
                    "segments; skipped during study-area merge."
                )
                continue

            provincial_layers.append(provincial_layer)

        if not provincial_layers:
            raise ValueError(
                f"No road segments were found for enabled network "
                f"{network_name!r} across the complete study area."
            )

        merged_raw = gpd.GeoDataFrame(
            pd.concat(
                provincial_layers,
                ignore_index=True,
            ),
            geometry="geometry",
            crs=source_crs,
        )

        merged_network = deduplicate_boundary_segments(
            merged_raw,
            network_classes[network_name],
        ).to_crs(config.roads.output_crs)

        study_area_networks[network_name] = merged_network

        display_name = network_name.replace("_", "-")

        print(
            f"{config.study_area.label} {display_name} network: "
            f"{len(merged_network):,} segments "
            f"(CRS: {merged_network.crs})"
        )

        validate_road_network(
            merged_network,
            f"{config.study_area.label} {display_name}",
        )

    return study_area_networks


def build_network_summary(
    roadclass_counts_all: pd.DataFrame,
    study_area_networks: dict[str, gpd.GeoDataFrame],
    config: GeospatialBuildConfig,
) -> pd.DataFrame:
    """Build jurisdiction-level and study-area road-network summaries.

    Segment counts and retained shares are calculated for every road network
    enabled in the build profile. The resulting table contains one row per
    selected jurisdiction and one combined ``STUDY_AREA`` row.

    Parameters
    ----------
    roadclass_counts_all : pd.DataFrame
        Source road-segment counts by jurisdiction and ``ROADCLASS``.
    study_area_networks : dict[str, gpd.GeoDataFrame]
        Deduplicated study-area road networks keyed by configured network name.
        Each GeoDataFrame must contain a ``province`` column.
    config : GeospatialBuildConfig
        Validated build profile containing the selected jurisdictions, enabled
        road networks, and study-area label.

    Returns
    -------
    pd.DataFrame
        Jurisdiction-level and combined road-network summary statistics.

    Raises
    ------
    ValueError
        If the source count table is missing required columns, an enabled
        network is absent from ``study_area_networks``, or a network lacks the
        required ``province`` column.
    """

    required_count_columns = {
        "province",
        "segments",
    }
    missing_count_columns = (
        required_count_columns - set(roadclass_counts_all.columns)
    )

    if missing_count_columns:
        raise ValueError(
            "Road-class count table is missing required columns: "
            f"{sorted(missing_count_columns)}"
        )

    enabled_networks = tuple(config.roads.networks)

    for network_name in enabled_networks:
        if network_name not in study_area_networks:
            raise ValueError(
                f"Study-area road networks are missing enabled network "
                f"{network_name!r}."
            )

        if "province" not in study_area_networks[network_name].columns:
            raise ValueError(
                f"Study-area network {network_name!r} is missing the "
                "'province' column."
            )

    summary_rows: list[dict[str, object]] = []

    for province in config.study_area.provinces:
        full_segments = int(
            roadclass_counts_all.loc[
                roadclass_counts_all["province"] == province,
                "segments",
            ].sum()
        )

        summary_row: dict[str, object] = {
            "study_area": config.study_area.label,
            "jurisdiction": province,
            "full_segments": full_segments,
        }

        for network_name in enabled_networks:
            network = study_area_networks[network_name]

            retained_segments = int(
                (network["province"] == province).sum()
            )

            summary_row[
                f"{network_name}_segments"
            ] = retained_segments

            summary_row[
                f"{network_name}_percent"
            ] = (
                round(
                    100
                    * retained_segments
                    / full_segments,
                    1,
                )
                if full_segments
                else 0.0
            )

        summary_rows.append(summary_row)

    combined_full_segments = int(
        roadclass_counts_all.loc[
            roadclass_counts_all["province"].isin(
                config.study_area.provinces
            ),
            "segments",
        ].sum()
    )

    combined_row: dict[str, object] = {
        "study_area": config.study_area.label,
        "jurisdiction": "STUDY_AREA",
        "full_segments": combined_full_segments,
    }

    for network_name in enabled_networks:
        retained_segments = len(
            study_area_networks[network_name]
        )

        combined_row[
            f"{network_name}_segments"
        ] = retained_segments

        combined_row[
            f"{network_name}_percent"
        ] = (
            round(
                100
                * retained_segments
                / combined_full_segments,
                1,
            )
            if combined_full_segments
            else 0.0
        )

    summary_rows.append(combined_row)

    summary = pd.DataFrame(summary_rows)

    ordered_columns = [
        "study_area",
        "jurisdiction",
        "full_segments",
    ]

    for network_name in enabled_networks:
        ordered_columns.extend(
            [
                f"{network_name}_segments",
                f"{network_name}_percent",
            ]
        )

    return summary[ordered_columns]


def export_provincial_networks(
    provincial_networks: dict[
        str,
        dict[str, gpd.GeoDataFrame],
    ],
    output_dir: Path,
    selected_provinces: tuple[str, ...],
    enabled_networks: tuple[str, ...],
) -> None:
    """Export configured road-network layers for each jurisdiction.

    One GeoPackage is written for each selected province or territory. Each
    non-empty configured road network is written as a separate layer. Empty
    narrower networks are skipped because some jurisdictions may legitimately
    have no backbone or primary-freight roads.

    Parameters
    ----------
    provincial_networks : dict[str, dict[str, gpd.GeoDataFrame]]
        Nested road-network mapping indexed first by jurisdiction and then by
        configured network name.
    output_dir : Path
        Directory where jurisdiction-level GeoPackages are written.
    selected_provinces : tuple[str, ...]
        Ordered jurisdiction codes included in the build profile.
    enabled_networks : tuple[str, ...]
        Ordered road-network layers enabled in the build profile.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If a selected jurisdiction or enabled network is absent from
        ``provincial_networks``.
    """

    for province in selected_provinces:
        if province not in provincial_networks:
            raise ValueError(
                f"Provincial road networks are missing jurisdiction "
                f"{province!r}."
            )

        output_path = (
            output_dir
            / f"{province}_filtered_road_networks.gpkg"
        )

        if output_path.exists():
            output_path.unlink()

        exported_layers: list[str] = []

        for network_name in enabled_networks:
            if network_name not in provincial_networks[province]:
                raise ValueError(
                    f"Jurisdiction {province!r} is missing enabled network "
                    f"{network_name!r}."
                )

            network = provincial_networks[province][network_name]

            if network.empty:
                print(
                    f"{province}: no "
                    f"{network_name.replace('_', '-')} segments; "
                    "layer skipped."
                )
                continue

            network.to_file(
                output_path,
                layer=network_name,
                driver="GPKG",
            )

            exported_layers.append(network_name)

        if exported_layers:
            layer_summary = " | ".join(
                (
                    f"{len(provincial_networks[province][network_name]):,} "
                    f"{network_name.replace('_', '-')}"
                )
                for network_name in exported_layers
            )

            print(
                f"{province}: {layer_summary} "
                f"→ {output_path.name}"
            )
        else:
            print(
                f"{province}: no configured road-network layers "
                "were exported."
            )


def export_study_area_networks(
    study_area_networks: dict[str, gpd.GeoDataFrame],
    output_dir: Path,
    config: GeospatialBuildConfig,
) -> Path:
    """Export configured study-area road networks to one GeoPackage.

    Each road-network representation enabled in the build profile is written as
    a separate GeoPackage layer. The existing profile-specific GeoPackage is
    removed before export so stale layers from previous configurations are not
    retained.

    Parameters
    ----------
    study_area_networks : dict[str, gpd.GeoDataFrame]
        Deduplicated and reprojected study-area road networks keyed by network
        name.
    output_dir : Path
        Directory where the filtered road-network GeoPackage is written.
    config : GeospatialBuildConfig
        Validated build profile containing the study-area label and enabled
        network outputs.

    Returns
    -------
    Path
        Path to the exported study-area GeoPackage.

    Raises
    ------
    ValueError
        If an enabled road network is absent or empty.
    """

    output_path = (
        output_dir
        / f"{config.study_area.label}_filtered_road_networks.gpkg"
    )

    if output_path.exists():
        output_path.unlink()

    layer_summaries: list[str] = []

    for network_name in config.roads.networks:
        if network_name not in study_area_networks:
            raise ValueError(
                f"Study-area road networks are missing enabled network "
                f"{network_name!r}."
            )

        network = study_area_networks[network_name]

        if network.empty:
            raise ValueError(
                f"Study-area network {network_name!r} contains no segments."
            )

        network.to_file(
            output_path,
            layer=network_name,
            driver="GPKG",
        )

        layer_summaries.append(
            f"{len(network):,} {network_name.replace('_', '-')}"
        )

    print(
        f"{config.study_area.label}: "
        + " | ".join(layer_summaries)
        + f" → {output_path.name}"
    )

    return output_path


def export_network_summary(
    network_summary: pd.DataFrame,
    output_dir: Path,
    study_area_label: str,
) -> Path:
    """Export the filtered road-network summary table.

    This function writes the jurisdiction-level and combined study-area
    road-network filtering summary to CSV. The table records the full source
    segment count together with retained segment counts and retained percentages
    for every road network enabled in the build profile.

    Parameters
    ----------
    network_summary : pd.DataFrame
        Summary table produced by ``build_network_summary``.
    output_dir : Path
        Directory where the summary CSV is written.
    study_area_label : str
        Study-area label used to construct the output filename.

    Returns
    -------
    Path
        Path to the exported summary CSV.
    """

    output_path = (
        output_dir
        / f"{study_area_label}_filtered_road_network_summary.csv"
    )

    network_summary.to_csv(
        output_path,
        index=False,
    )

    print(f"Exported network summary table → {output_path.name}")
    return output_path


def parse_args() -> argparse.Namespace:
    """Parse the command-line path to the Stage 3 build profile.

    The command-line interface requires a TOML configuration file describing the
    study area, road classes, enabled network outputs, output CRS, and other shared
    Geospatial-CANOE preprocessing settings.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments containing the required ``config`` path.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Build configured Geospatial-CANOE processed road networks."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help=(
            "Path to a geospatial preprocessing TOML build profile."
        ),
    )
    return parser.parse_args()


def run_road_build(
    config: GeospatialBuildConfig,
) -> pd.DataFrame:
    """Run the complete Stage 3 road-network build workflow.

    This function orchestrates Stage 3 using an already validated geospatial
    build profile. It creates the processed NRN output directory, loads and
    filters the selected provincial road networks, merges every configured
    network representation into study-area layers, and builds the
    jurisdiction-level summary table.

    Provincial GeoPackages are exported when enabled in the profile. The merged
    study-area GeoPackage and road-network summary CSV are always exported
    before the completed summary table is returned.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated build profile containing the study area, road classes,
        enabled network outputs, output CRS, and provincial export settings.

    Returns
    -------
    pd.DataFrame
        Jurisdiction-level and combined study-area road-network summary table.
    """

    PROCESSED_NRN.mkdir(parents=True, exist_ok=True)

    (
        provincial_networks,
        roadclass_counts_all,
        provincial_crs,
    ) = load_filtered_provincial_networks(config)

    study_area_networks = build_study_area_networks(
        provincial_networks=provincial_networks,
        provincial_crs=provincial_crs,
        config=config,
    )

    network_summary = build_network_summary(
        roadclass_counts_all=roadclass_counts_all,
        study_area_networks=study_area_networks,
        config=config,
    )

    if config.roads.export_individual_provinces:
        export_provincial_networks(
            provincial_networks=provincial_networks,
            output_dir=PROCESSED_NRN,
            selected_provinces=config.study_area.provinces,
            enabled_networks=config.roads.networks,
        )

    export_study_area_networks(
        study_area_networks=study_area_networks,
        output_dir=PROCESSED_NRN,
        config=config,
    )

    export_network_summary(
        network_summary=network_summary,
        output_dir=PROCESSED_NRN,
        study_area_label=config.study_area.label,
    )

    print("\nStage 3 complete.")

    return network_summary


def main() -> None:
    """Load the configured build profile and run Stage 3.

    The command-line configuration path is parsed, loaded into a validated
    ``GeospatialBuildConfig``, printed for run traceability, and passed to the
    Stage 3 road-network build workflow.

    Returns
    -------
    None
    """

    args = parse_args()
    config = load_geospatial_build_config(args.config)
    print_build_config(config)
    run_road_build(config)


if __name__ == "__main__":
    main()
