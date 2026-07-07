"""
build_roads.py

Stage 3 of the Geospatial-CANOE workflow.

This script takes the downloaded raw national road network (NRN) GeoPackage files for each province and territory
and filters them to create two nested road network representations, 
and exports the results as new GeoPackage files and a summary CSV.
All provinces and terrirories are processed, and the resulting national networks are also exported
in addition to the provincial networks

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
        {PROVINCE}_filtered_road_networks.gpkg
        CANADA_filtered_road_networks.gpkg
        filtered_road_network_summary.csv
"""

from pathlib import Path
import sqlite3
from collections.abc import Sequence

import geopandas as gpd
import pandas as pd


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_NRN = PROJECT_ROOT / "data_files" / "raw" / "nrn"
PROCESSED_NRN = PROJECT_ROOT / "data_files" / "processed" / "nrn"


# =============================================================================
# Settings
# =============================================================================

PROVINCES = [
    "AB", "BC", "MB", "NB", "NL", "NS",
    "NT", "NU", "ON", "PE", "QC", "SK", "YT",
]

WGS84_CRS = "EPSG:4326"

BACKBONE_CLASSES = (
    "Freeway",
    "Expressway / Highway",
    "Ramp",
)

FREIGHT_ACCESS_CLASSES = BACKBONE_CLASSES + (
    "Arterial",
)


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
    as a diagnostic check after network filtering or national concatenation.

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
    non_null_geometry = geometry.dropna()

    null_geometry = geometry.isna().sum()
    empty_geometry = non_null_geometry.is_empty.sum()
    invalid_geometry = (~non_null_geometry.is_valid).sum()
    duplicate_geometry = (
        non_null_geometry
        .apply(lambda geom: geom.wkt)
        .duplicated()
        .sum()
    )

    print(f"Null geometries: {null_geometry:,}")
    print(f"Empty geometries: {empty_geometry:,}")
    print(f"Invalid geometries: {invalid_geometry:,}")
    print(f"Duplicate geometries (identical WKT): {duplicate_geometry:,}")
    print()


def load_filtered_provincial_networks() -> tuple[
    dict[str, gpd.GeoDataFrame],
    dict[str, gpd.GeoDataFrame],
    pd.DataFrame,
    dict[str, object],
]:
    """Load and filter provincial NRN road networks.

    This function processes each configured province and territory in
    ``PROVINCES``. For each raw NRN GeoPackage, it finds the road segment
    layer, records source road-class counts, loads the freight-access subset,
    derives the nested backbone subset, and stores the source CRS. After all
    provinces are processed, it verifies that the loaded provincial networks
    have non-missing and consistent CRS values.

    Returns
    -------
    tuple[dict[str, gpd.GeoDataFrame], dict[str, gpd.GeoDataFrame], pd.DataFrame, dict[str, object]]
        Provincial backbone networks keyed by province code, provincial
        freight-access networks keyed by province code, combined source
        road-class counts, and provincial CRS values keyed by province code.

    Raises
    ------
    FileNotFoundError
        If a required provincial NRN directory or English GeoPackage is missing.
    ValueError
        If a provincial GeoPackage has an invalid ROADSEG layer structure, a
        missing CRS, or a CRS inconsistent with the other provinces.
    """

    freight_access_where = (
        "ROADCLASS IN ("
        + ", ".join(repr(cls) for cls in FREIGHT_ACCESS_CLASSES)
        + ")"
    )

    provincial_freight_access = {}
    provincial_backbone = {}
    provincial_roadclass_counts = []
    provincial_crs = {}

    for province in PROVINCES:
        gpkg_path = get_nrn_gpkg_path(province)
        roadseg_table = get_roadseg_table(gpkg_path)

        roadclass_counts = get_roadclass_counts(
            gpkg_path=gpkg_path,
            roadseg_table=roadseg_table,
        )

        roadclass_counts.insert(0, "province", province)
        provincial_roadclass_counts.append(roadclass_counts)

        full_count = roadclass_counts["segments"].sum()

        freight_access = gpd.read_file(
            gpkg_path,
            layer=roadseg_table,
            where=freight_access_where,
        )

        freight_access["province"] = province

        backbone = freight_access[
            freight_access["ROADCLASS"].isin(BACKBONE_CLASSES)
        ].copy()

        provincial_freight_access[province] = freight_access
        provincial_backbone[province] = backbone
        provincial_crs[province] = freight_access.crs

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

    if any(crs is None for crs in provincial_crs.values()):
        raise ValueError(
            f"Some provincial GeoPackages have missing CRS: "
            f"{[p for p, c in provincial_crs.items() if c is None]}"
        )

    unique_crs = {
        crs.to_string()
        for crs in provincial_crs.values()
    }

    if len(unique_crs) != 1:
        raise ValueError(
            f"Provincial GeoPackages do not share a common CRS: "
            f"{ {p: c.to_string() for p, c in provincial_crs.items()} }"
        )

    print(f"\nAll provinces share CRS: {unique_crs.pop()}")

    return (
        provincial_backbone,
        provincial_freight_access,
        roadclass_counts_all,
        provincial_crs,
    )


def build_national_networks(
    provincial_backbone: dict[str, gpd.GeoDataFrame],
    provincial_freight_access: dict[str, gpd.GeoDataFrame],
    provincial_crs: dict[str, object],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Build national backbone and freight-access road networks.

    This function concatenates the filtered provincial and territorial NRN
    networks into national backbone and freight-access layers. Boundary
    duplicates are removed using exact geometry matching and road-class
    priority rules, then both national layers are reprojected to the workflow
    WGS84 CRS. Geometry diagnostics are printed for each national network.

    Parameters
    ----------
    provincial_backbone : dict[str, gpd.GeoDataFrame]
        Provincial and territorial backbone road networks keyed by province or
        territory code.
    provincial_freight_access : dict[str, gpd.GeoDataFrame]
        Provincial and territorial freight-access road networks keyed by
        province or territory code.
    provincial_crs : dict[str, object]
        Source CRS values for the provincial and territorial NRN files, keyed
        by province or territory code.

    Returns
    -------
    tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]
        National backbone and freight-access road networks in WGS84.
    """

    source_crs = provincial_crs[PROVINCES[0]]

    canada_backbone_raw = gpd.GeoDataFrame(
        pd.concat(provincial_backbone.values(), ignore_index=True),
        geometry="geometry",
        crs=source_crs,
    )

    canada_freight_access_raw = gpd.GeoDataFrame(
        pd.concat(provincial_freight_access.values(), ignore_index=True),
        geometry="geometry",
        crs=source_crs,
    )

    canada_backbone = deduplicate_boundary_segments(
        canada_backbone_raw,
        BACKBONE_CLASSES,
    ).to_crs(WGS84_CRS)

    canada_freight_access = deduplicate_boundary_segments(
        canada_freight_access_raw,
        FREIGHT_ACCESS_CLASSES,
    ).to_crs(WGS84_CRS)

    print(
        f"National backbone network: "
        f"{canada_backbone.shape[0]:,} segments "
        f"(CRS: {canada_backbone.crs})"
    )

    print(
        f"National freight-access network: "
        f"{canada_freight_access.shape[0]:,} segments "
        f"(CRS: {canada_freight_access.crs})"
    )

    validate_road_network(canada_backbone, "Backbone")
    validate_road_network(canada_freight_access, "Freight-access")

    return canada_backbone, canada_freight_access


def build_network_summary(
    roadclass_counts_all: pd.DataFrame,
    canada_backbone: gpd.GeoDataFrame,
    canada_freight_access: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Build provincial and national road-network filtering summary.

    This function summarizes how many source NRN road segments are retained in
    the filtered backbone and freight-access networks for each province and
    territory. It uses the full source ``ROADCLASS`` counts as the denominator,
    counts retained segments from the national filtered layers, and reports
    retention percentages for backbone, arterial, and freight-access segments.
    A final Canada row aggregates the provincial and territorial totals.

    Parameters
    ----------
    roadclass_counts_all : pd.DataFrame
        Source road-class segment counts for all provinces and territories.
    canada_backbone : gpd.GeoDataFrame
        Deduplicated national backbone road network.
    canada_freight_access : gpd.GeoDataFrame
        Deduplicated national freight-access road network.

    Returns
    -------
    pd.DataFrame
        Summary table with provincial, territorial, and national segment counts
        and filtering percentages.
    """

    summary_rows = []

    for province in PROVINCES:
        full_segments = int(
            roadclass_counts_all.loc[
                roadclass_counts_all["province"] == province,
                "segments",
            ].sum()
        )

        backbone_segments = int(
            (canada_backbone["province"] == province).sum()
        )

        freight_access_segments = int(
            (canada_freight_access["province"] == province).sum()
        )

        arterial_segments = int(
            (
                (canada_freight_access["province"] == province)
                & (canada_freight_access["ROADCLASS"] == "Arterial")
            ).sum()
        )

        summary_rows.append(
            {
                "province": province,
                "full_segments": full_segments,
                "backbone_segments": backbone_segments,
                "arterial_segments": arterial_segments,
                "freight_access_segments": freight_access_segments,
                "backbone_percent": round(
                    100 * backbone_segments / full_segments,
                    1,
                ) if full_segments else 0.0,
                "arterial_percent": round(
                    100 * arterial_segments / full_segments,
                    1,
                ) if full_segments else 0.0,
                "freight_access_percent": round(
                    100 * freight_access_segments / full_segments,
                    1,
                ) if full_segments else 0.0,
            }
        )

    national_row = {
        "province": "Canada",
        "full_segments": sum(row["full_segments"] for row in summary_rows),
        "backbone_segments": sum(row["backbone_segments"] for row in summary_rows),
        "arterial_segments": sum(row["arterial_segments"] for row in summary_rows),
        "freight_access_segments": sum(
            row["freight_access_segments"]
            for row in summary_rows
        ),
    }

    national_row["backbone_percent"] = round(
        100
        * national_row["backbone_segments"]
        / national_row["full_segments"],
        1,
    )

    national_row["arterial_percent"] = round(
        100
        * national_row["arterial_segments"]
        / national_row["full_segments"],
        1,
    )

    national_row["freight_access_percent"] = round(
        100
        * national_row["freight_access_segments"]
        / national_row["full_segments"],
        1,
    )

    summary_rows.append(national_row)

    return pd.DataFrame(summary_rows)


def export_provincial_networks(
    provincial_backbone: dict[str, gpd.GeoDataFrame],
    provincial_freight_access: dict[str, gpd.GeoDataFrame],
    output_dir: Path,
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

    for province in PROVINCES:
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


def export_national_networks(
    canada_backbone: gpd.GeoDataFrame,
    canada_freight_access: gpd.GeoDataFrame,
    output_dir: Path,
) -> None:
    """Export national filtered road networks to a GeoPackage.

    This function writes the Canada-wide filtered road networks to a single
    GeoPackage with two layers: ``backbone`` and ``freight_access``. If a
    previous national road-network output exists, it is removed before the new
    layers are written.

    Parameters
    ----------
    canada_backbone : gpd.GeoDataFrame
        Deduplicated national backbone road network.
    canada_freight_access : gpd.GeoDataFrame
        Deduplicated national freight-access road network.
    output_dir : Path
        Directory where the national road-network GeoPackage is written.

    Returns
    -------
    None
    """

    output_path = output_dir / "CANADA_filtered_road_networks.gpkg"

    if output_path.exists():
        output_path.unlink()

    canada_backbone.to_file(
        output_path,
        layer="backbone",
        driver="GPKG",
    )

    canada_freight_access.to_file(
        output_path,
        layer="freight_access",
        driver="GPKG",
    )

    print(f"Canada: exported {output_path.name}")
    print(
        f"Canada: "
        f"{len(canada_backbone):,} backbone | "
        f"{len(canada_freight_access):,} freight-access "
        f"→ {output_path.name}"
    )


def export_network_summary(
    network_summary: pd.DataFrame,
    output_dir: Path,
) -> None:
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

    output_path = output_dir / "filtered_road_network_summary.csv"

    network_summary.to_csv(
        output_path,
        index=False,
    )

    print(f"Exported network summary table → {output_path.name}")


def main() -> None:
    """Run Stage 3 of the Geospatial-CANOE road preprocessing workflow.

    This entry point prepares the processed NRN output directory, loads and
    filters the provincial and territorial NRN road networks, builds the
    deduplicated national backbone and freight-access networks, creates the
    filtered-road summary table, and exports all provincial, national, and
    summary outputs.

    Returns
    -------
    None
    """
    PROCESSED_NRN.mkdir(parents=True, exist_ok=True)

    (
        provincial_backbone,
        provincial_freight_access,
        roadclass_counts_all,
        provincial_crs,
    ) = load_filtered_provincial_networks()

    canada_backbone, canada_freight_access = build_national_networks(
        provincial_backbone=provincial_backbone,
        provincial_freight_access=provincial_freight_access,
        provincial_crs=provincial_crs,
    )

    network_summary = build_network_summary(
        roadclass_counts_all=roadclass_counts_all,
        canada_backbone=canada_backbone,
        canada_freight_access=canada_freight_access,
    )

    export_provincial_networks(
        provincial_backbone=provincial_backbone,
        provincial_freight_access=provincial_freight_access,
        output_dir=PROCESSED_NRN,
    )

    export_national_networks(
        canada_backbone=canada_backbone,
        canada_freight_access=canada_freight_access,
        output_dir=PROCESSED_NRN,
    )

    export_network_summary(
        network_summary=network_summary,
        output_dir=PROCESSED_NRN,
    )

    print("\nStage 3 complete.")


if __name__ == "__main__":
    main()