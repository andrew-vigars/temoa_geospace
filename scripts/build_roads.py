"""
build_roads.py

Stage 3 of the Geospatial-CANOE workflow.

This script reads the downloaded raw National Road Network (NRN) GeoPackages for the provinces and territories selected in a shared TOML build profile. It filters and merges those jurisdictions into two nested study-area road-network representations and exports profile-labelled GeoPackage and summary products.

Source: https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f
Both English and French versions of the NRN are available, but this script uses the English version only.

Two nested road network representations are constructed:

1. Backbone network:
    - Freeway
    - Expressway / Highway
    - Ramp

2. Freight-access network:
    - Freeway
    - Expressway / Highway
    - Ramp
    - Arterial

Inputs:
    data_files/raw/nrn/{PROVINCE}/*_en.gpkg

Outputs:
    data_files/processed/nrn/
        {PROVINCE}_filtered_road_networks.gpkg  [optional]
        {study_area}_filtered_road_networks.gpkg
        {study_area}_filtered_road_network_summary.csv
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sqlite3
from typing import TypedDict

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from project_config import (
    GeospatialBuildConfig,
    load_geospatial_build_config,
    print_build_config,
)


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_NRN = PROJECT_ROOT / "data_files" / "raw" / "nrn"
PROCESSED_NRN = PROJECT_ROOT / "data_files" / "processed" / "nrn"


class RoadNetworkSummaryRow(TypedDict):
    """One jurisdiction-level road-network summary record."""

    study_area: str
    jurisdiction: str
    full_segments: int
    backbone_segments: int
    arterial_segments: int
    freight_access_segments: int
    backbone_percent: float
    arterial_percent: float
    freight_access_percent: float

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
    dict[str, gpd.GeoDataFrame],
    dict[str, gpd.GeoDataFrame],
    pd.DataFrame,
    dict[str, CRS],
]:
    """Load and filter provincial NRN road networks for one study area.

    For each province or territory selected in the build profile, this function
    locates the raw English National Road Network GeoPackage, identifies its
    ``ROADSEG`` layer, and records the source road-segment counts by
    ``ROADCLASS``. It then reads only the configured freight-access classes and
    derives the nested backbone network from that filtered table.

    Study-area metadata is added to each retained road segment. The function
    also validates that every selected provincial network has a defined
    coordinate reference system and that all selected GeoPackages use the same
    CRS before the networks are returned for merging and export.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated geospatial build profile containing the selected provinces or
        territories, study-area label, and configured backbone and
        freight-access road classes.

    Returns
    -------
    tuple[
        dict[str, gpd.GeoDataFrame],
        dict[str, gpd.GeoDataFrame],
        pd.DataFrame,
        dict[str, CRS],
    ]
        Four objects containing:

        - Backbone road networks keyed by province or territory code.
        - Freight-access road networks keyed by province or territory code.
        - Combined source ``ROADCLASS`` segment counts for all selected
          jurisdictions.
        - Source coordinate reference systems keyed by province or territory
          code.

    Raises
    ------
    FileNotFoundError
        If a selected province or territory does not have the expected raw
        English NRN GeoPackage.
    ValueError
        If a GeoPackage has an invalid or ambiguous ``ROADSEG`` layer, no
        configured freight-access or backbone classes are found, a selected
        network has no CRS, or the selected provincial GeoPackages do not share
        one common CRS.
    """

    selected_provinces = list(config.study_area.provinces)
    backbone_classes = config.roads.classes.backbone
    freight_access_classes = config.roads.classes.freight_access

    freight_access_where = (
        "ROADCLASS IN ("
        + ", ".join(repr(cls) for cls in freight_access_classes)
        + ")"
    )

    provincial_freight_access: dict[str, gpd.GeoDataFrame] = {}
    provincial_backbone: dict[str, gpd.GeoDataFrame] = {}
    provincial_roadclass_counts: list[pd.DataFrame] = []
    provincial_crs: dict[str, CRS] = {}

    for province in selected_provinces:
        gpkg_path = get_nrn_gpkg_path(province)
        roadseg_table = get_roadseg_table(gpkg_path)

        roadclass_counts = get_roadclass_counts(
            gpkg_path=gpkg_path,
            roadseg_table=roadseg_table,
        )

        roadclass_counts.insert(0, "province", province)
        provincial_roadclass_counts.append(roadclass_counts)

        full_count = int(roadclass_counts["segments"].sum())

        freight_access = gpd.read_file(
            gpkg_path,
            layer=roadseg_table,
            where=freight_access_where,
        )

        if freight_access.empty:
            raise ValueError(
                "No configured freight-access road classes were found for "
                f"{province}."
            )

        source_crs = freight_access.crs

        if source_crs is None:
            raise ValueError(
                f"Selected provincial GeoPackage has no CRS: {province}"
            )

        freight_access["province"] = province
        freight_access["study_area"] = config.study_area.label
        freight_access["province_codes"] = ",".join(selected_provinces)

        backbone = freight_access.loc[
            freight_access["ROADCLASS"].isin(backbone_classes)
        ].copy()

        if backbone.empty:
            raise ValueError(
                "No configured backbone road classes were found for "
                f"{province}."
            )

        provincial_freight_access[province] = freight_access
        provincial_backbone[province] = backbone
        provincial_crs[province] = source_crs

        print(
            f"{province}: "
            f"{full_count:,} total | "
            f"{len(backbone):,} backbone | "
            f"{len(freight_access):,} freight-access"
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
        raise ValueError(
            "Selected provincial GeoPackages do not share a common CRS: "
            f"{ {p: c.to_string() for p, c in provincial_crs.items()} }"
        )

    print(f"\nAll selected jurisdictions share CRS: {unique_crs.pop()}")

    return (
        provincial_backbone,
        provincial_freight_access,
        roadclass_counts_all,
        provincial_crs,
    )


def build_study_area_networks(
    provincial_backbone: dict[str, gpd.GeoDataFrame],
    provincial_freight_access: dict[str, gpd.GeoDataFrame],
    provincial_crs: dict[str, CRS],
    config: GeospatialBuildConfig,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Merge provincial road layers into study-area networks.

The provincial backbone and freight-access GeoDataFrames are concatenated
separately using the common source CRS established during provincial loading.
Duplicate road segments occurring along jurisdictional boundaries are removed,
and both merged networks are reprojected to the configured output CRS.

Geometry-quality diagnostics are printed for the resulting study-area networks
before they are returned.

Parameters
----------
provincial_backbone : dict[str, gpd.GeoDataFrame]
    Backbone road networks keyed by province or territory code.
provincial_freight_access : dict[str, gpd.GeoDataFrame]
    Freight-access road networks keyed by province or territory code.
provincial_crs : dict[str, CRS]
    Source coordinate reference systems keyed by province or territory code.
config : GeospatialBuildConfig
    Validated build profile containing the selected jurisdictions, study-area
    label, road-class definitions, and output CRS.

Returns
-------
tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]
    Deduplicated and reprojected study-area backbone and freight-access road
    networks, respectively.
    """

    selected_provinces = list(config.study_area.provinces)
    source_crs = provincial_crs[selected_provinces[0]]

    study_area_backbone_raw = gpd.GeoDataFrame(
        pd.concat(
            provincial_backbone.values(),
            ignore_index=True,
        ),
        geometry="geometry",
        crs=source_crs,
    )

    study_area_freight_access_raw = gpd.GeoDataFrame(
        pd.concat(
            provincial_freight_access.values(),
            ignore_index=True,
        ),
        geometry="geometry",
        crs=source_crs,
    )

    study_area_backbone = deduplicate_boundary_segments(
        study_area_backbone_raw,
        config.roads.classes.backbone,
    ).to_crs(config.roads.output_crs)

    study_area_freight_access = deduplicate_boundary_segments(
        study_area_freight_access_raw,
        config.roads.classes.freight_access,
    ).to_crs(config.roads.output_crs)

    print(
        f"{config.study_area.label} backbone network: "
        f"{len(study_area_backbone):,} segments "
        f"(CRS: {study_area_backbone.crs})"
    )

    print(
        f"{config.study_area.label} freight-access network: "
        f"{len(study_area_freight_access):,} segments "
        f"(CRS: {study_area_freight_access.crs})"
    )

    validate_road_network(
        study_area_backbone,
        f"{config.study_area.label} backbone",
    )
    validate_road_network(
        study_area_freight_access,
        f"{config.study_area.label} freight-access",
    )

    return study_area_backbone, study_area_freight_access


def build_network_summary(
    roadclass_counts_all: pd.DataFrame,
    study_area_backbone: gpd.GeoDataFrame,
    study_area_freight_access: gpd.GeoDataFrame,
    config: GeospatialBuildConfig,
) -> pd.DataFrame:
    """Build jurisdiction-level and combined road-network summary statistics."""

    summary_rows: list[RoadNetworkSummaryRow] = []

    for province in config.study_area.provinces:
        full_segments = int(
            roadclass_counts_all.loc[
                roadclass_counts_all["province"] == province,
                "segments",
            ].sum()
        )

        backbone_segments = int(
            (study_area_backbone["province"] == province).sum()
        )

        freight_access_segments = int(
            (study_area_freight_access["province"] == province).sum()
        )

        arterial_segments = int(
            (
                (study_area_freight_access["province"] == province)
                & (
                    study_area_freight_access["ROADCLASS"]
                    == "Arterial"
                )
            ).sum()
        )

        summary_rows.append(
            {
                "study_area": config.study_area.label,
                "jurisdiction": province,
                "full_segments": full_segments,
                "backbone_segments": backbone_segments,
                "arterial_segments": arterial_segments,
                "freight_access_segments": freight_access_segments,
                "backbone_percent": (
                    round(100 * backbone_segments / full_segments, 1)
                    if full_segments
                    else 0.0
                ),
                "arterial_percent": (
                    round(100 * arterial_segments / full_segments, 1)
                    if full_segments
                    else 0.0
                ),
                "freight_access_percent": (
                    round(
                        100 * freight_access_segments / full_segments,
                        1,
                    )
                    if full_segments
                    else 0.0
                ),
            }
        )

    combined_full_segments = sum(
        row["full_segments"]
        for row in summary_rows
    )
    combined_backbone_segments = len(study_area_backbone)
    combined_arterial_segments = int(
        (
            study_area_freight_access["ROADCLASS"]
            == "Arterial"
        ).sum()
    )
    combined_freight_access_segments = len(
        study_area_freight_access
    )

    combined_row: RoadNetworkSummaryRow = {
        "study_area": config.study_area.label,
        "jurisdiction": "STUDY_AREA",
        "full_segments": combined_full_segments,
        "backbone_segments": combined_backbone_segments,
        "arterial_segments": combined_arterial_segments,
        "freight_access_segments": combined_freight_access_segments,
        "backbone_percent": (
            round(
                100
                * combined_backbone_segments
                / combined_full_segments,
                1,
            )
            if combined_full_segments
            else 0.0
        ),
        "arterial_percent": (
            round(
                100
                * combined_arterial_segments
                / combined_full_segments,
                1,
            )
            if combined_full_segments
            else 0.0
        ),
        "freight_access_percent": (
            round(
                100
                * combined_freight_access_segments
                / combined_full_segments,
                1,
            )
            if combined_full_segments
            else 0.0
        ),
    }

    summary_rows.append(combined_row)

    return pd.DataFrame(summary_rows)


def export_provincial_networks(
    provincial_backbone: dict[str, gpd.GeoDataFrame],
    provincial_freight_access: dict[str, gpd.GeoDataFrame],
    output_dir: Path,
    selected_provinces: tuple[str, ...],
) -> None:
    """Export filtered road networks for each province and territory.

    This function writes one GeoPackage per configured province or territory.
    Each output GeoPackage contains two layers: the filtered ``backbone``
    network and the broader ``freight_access`` network. Existing provincial
    output files are removed before new layers are written.

    Parameters
    ----------
    provincial_backbone : dict[str, gpd.GeoDataFrame]
        Provincial and territorial backbone road networks keyed by province or
        territory code.
    provincial_freight_access : dict[str, gpd.GeoDataFrame]
        Provincial and territorial freight-access road networks keyed by
        province or territory code.
    output_dir : Path
        Directory where provincial and territorial GeoPackage outputs are
        written.

    Returns
    -------
    None
    """

    for province in selected_provinces:
        output_path = output_dir / f"{province}_filtered_road_networks.gpkg"

        if output_path.exists():
            output_path.unlink()

        provincial_backbone[province].to_file(
            output_path,
            layer="backbone",
            driver="GPKG",
        )

        provincial_freight_access[province].to_file(
            output_path,
            layer="freight_access",
            driver="GPKG",
        )

        print(f"{province}: exported {output_path.name}")
        print(
            f"{province}: "
            f"{len(provincial_backbone[province]):,} backbone | "
            f"{len(provincial_freight_access[province]):,} freight-access "
            f"→ {output_path.name}"
        )


def export_study_area_networks(
    study_area_backbone: gpd.GeoDataFrame,
    study_area_freight_access: gpd.GeoDataFrame,
    output_dir: Path,
    config: GeospatialBuildConfig,
) -> Path:
    """Export configured study-area road networks to one GeoPackage.

    The existing profile-specific GeoPackage is removed before export so the output
    contains only layers produced by the current run. The backbone and
    freight-access networks are written as separate layers when their corresponding
    network names are enabled in the build profile.

    Parameters
    ----------
    study_area_backbone : gpd.GeoDataFrame
        Deduplicated and reprojected study-area backbone road network.
    study_area_freight_access : gpd.GeoDataFrame
        Deduplicated and reprojected study-area freight-access road network.
    output_dir : Path
        Directory where the filtered road-network GeoPackage will be written.
    config : GeospatialBuildConfig
        Validated build profile containing the study-area label and enabled road
        network outputs.

    Returns
    -------
    Path
        Path to the exported study-area GeoPackage.
    """

    output_path = (
        output_dir
        / f"{config.study_area.label}_filtered_road_networks.gpkg"
    )

    if output_path.exists():
        output_path.unlink()

    if "backbone" in config.roads.networks:
        study_area_backbone.to_file(
            output_path,
            layer="backbone",
            driver="GPKG",
        )

    if "freight_access" in config.roads.networks:
        study_area_freight_access.to_file(
            output_path,
            layer="freight_access",
            driver="GPKG",
        )

    print(
        f"{config.study_area.label}: "
        f"{len(study_area_backbone):,} backbone | "
        f"{len(study_area_freight_access):,} freight-access "
        f"→ {output_path.name}"
    )

    return output_path


def export_network_summary(
    network_summary: pd.DataFrame,
    output_dir: Path,
    study_area_label: str,
) -> Path:
    """Export the filtered road-network summary table.

    This function writes the province, territory, and national road-network
    filtering summary to CSV. The table records the full source segment counts,
    retained backbone and freight-access segment counts, arterial segment
    counts, and filtering percentages.

    Parameters
    ----------
    network_summary : pd.DataFrame
        Summary table produced by ``build_network_summary``.
    output_dir : Path
        Directory where the summary CSV is written.

    Returns
    -------
    None
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

    This function orchestrates Stage 3 using an already validated geospatial build
    profile. It creates the processed NRN output directory, loads and filters the
    selected provincial road networks, merges them into study-area backbone and
    freight-access networks, and builds the jurisdiction-level summary table.

    Provincial GeoPackages are exported when enabled in the profile. The merged
    study-area GeoPackage and road-network summary CSV are always exported before
    the completed summary table is returned.

    Parameters
    ----------
    config : GeospatialBuildConfig
        Validated build profile containing the study area, road classes, enabled
        network outputs, output CRS, and provincial export settings.

    Returns
    -------
    pd.DataFrame
        Jurisdiction-level and combined study-area road-network summary table.
    """

    PROCESSED_NRN.mkdir(parents=True, exist_ok=True)

    (
        provincial_backbone,
        provincial_freight_access,
        roadclass_counts_all,
        provincial_crs,
    ) = load_filtered_provincial_networks(config)

    (
        study_area_backbone,
        study_area_freight_access,
    ) = build_study_area_networks(
        provincial_backbone=provincial_backbone,
        provincial_freight_access=provincial_freight_access,
        provincial_crs=provincial_crs,
        config=config,
    )

    network_summary = build_network_summary(
        roadclass_counts_all=roadclass_counts_all,
        study_area_backbone=study_area_backbone,
        study_area_freight_access=study_area_freight_access,
        config=config,
    )

    if config.roads.export_individual_provinces:
        export_provincial_networks(
            provincial_backbone=provincial_backbone,
            provincial_freight_access=provincial_freight_access,
            output_dir=PROCESSED_NRN,
            selected_provinces=config.study_area.provinces,
        )

    export_study_area_networks(
        study_area_backbone=study_area_backbone,
        study_area_freight_access=study_area_freight_access,
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
