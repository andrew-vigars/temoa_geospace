"""Build configurable population-centre proxies and allocate gasoline demand.

The first Silver task selects one or more market/depot proxies in every
configured province or territory and maps dissemination-area population to the
nearest proxy without crossing jurisdiction boundaries.  The second task
allocates annual Statistics Canada net gasoline sales to those proxy catchments.
The third maps proxy demand onto every basemap resolution produced by the build
profile and exports source and resolution-specific diagnostic maps.

The NRCan anchor registry is a controlled modelling input, not a claimed
terminal inventory.  Rows inferred from the qualitative supply-orbit map remain
labelled as such in every downstream artifact.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

import geopandas as gpd  # type: ignore[import-untyped]
import matplotlib.pyplot as plt  # type: ignore[import-untyped]
from matplotlib.colors import LogNorm  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from geocanoe.config import GeospatialBuildConfig
from geocanoe.geospatial.adjacency import find_basemap_files
from geocanoe.geospatial.basemaps import (
    find_boundary_shapefile,
    load_province_boundaries,
)
from geocanoe.paths import find_project_root


PROJECT_ROOT = find_project_root()
DATA_FILES = PROJECT_ROOT / "data_files"
RAW_GASOLINE_DEMAND = DATA_FILES / "raw" / "gasoline_demand"
RAW_RESIDENTIAL = DATA_FILES / "raw" / "residential"
PROCESSED_GASOLINE_DEMAND = DATA_FILES / "processed" / "gasoline_demand"
PROCESSED_BASEMAPS = DATA_FILES / "processed" / "basemaps"
RAW_BASEMAPS = DATA_FILES / "raw" / "basemaps"

POPULATION_CENTRES_PATH = (
    RAW_GASOLINE_DEMAND / "population_centres" / "lpc_000b21a_e.shp"
)
FUEL_SALES_PATH = RAW_GASOLINE_DEMAND / "fuel_sales" / "23100066.csv"
DA_BOUNDARIES_PATH = RAW_RESIDENTIAL / "lda_000b21a_e.shp"
DA_POPULATION_PATH = RAW_RESIDENTIAL / "98100015.csv"

DA_POPULATION_FIELD = (
    "Population and dwelling counts (5): Population, 2021 [1]"
)
NET_GASOLINE_LABEL = "Net sales of gasoline"
METRIC_CRS = "EPSG:3347"
WGS84_CRS = "EPSG:4326"

PRUID_TO_CODE = {
    "10": "NL",
    "11": "PE",
    "12": "NS",
    "13": "NB",
    "24": "QC",
    "35": "ON",
    "46": "MB",
    "47": "SK",
    "48": "AB",
    "59": "BC",
    "60": "YT",
    "61": "NT",
    "62": "NU",
}
PROVINCE_NAME_TO_CODE = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}
MINIMUM_CLASS_BY_POPULATION = {1_000: 2, 30_000: 3, 100_000: 4}


class GasolineProxyResult(TypedDict):
    hubs: Path
    crosswalk: Path
    catchments: Path
    audit: Path


class GasolineDemandResult(TypedDict):
    demand: Path
    audit: Path
    manifest: Path


class GasolineBasemapResult(TypedDict):
    summary: Path
    manifest: Path
    source_preview: Path


def output_directory(config: GeospatialBuildConfig) -> Path:
    """Return the profile-specific Silver gasoline-demand directory."""

    return PROCESSED_GASOLINE_DEMAND / config.build_id


def resolve_anchor_registry(config: GeospatialBuildConfig) -> Path:
    """Resolve the controlled anchor registry relative to the project root."""

    path = Path(config.gasoline_demand.proxies.anchor_registry)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _required_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def load_population_centres(
    configured_provinces: tuple[str, ...],
) -> gpd.GeoDataFrame:
    """Load provincial population-centre parts and standardized identifiers."""

    centres = gpd.read_file(_required_file(
        POPULATION_CENTRES_PATH,
        "Population-centre boundary",
    )).to_crs(METRIC_CRS)
    centres = centres.copy()
    centres["province"] = centres["PRUID"].astype(str).map(PRUID_TO_CODE)
    centres["population_class"] = pd.to_numeric(
        centres["PCCLASS"], errors="coerce"
    )
    centres["hub_id"] = centres["PCPUID"].astype(str).str.zfill(6)
    centres["population_centre_id"] = centres["PCUID"].astype(str).str.zfill(4)
    centres = centres.loc[
        centres["province"].isin(configured_provinces)
    ].copy()
    if centres.empty:
        raise ValueError("No population centres intersect the configured study area.")
    if centres[["province", "population_class"]].isna().any().any():
        raise ValueError("Population-centre province or class values are invalid.")
    return centres


def load_dissemination_areas(
    configured_provinces: tuple[str, ...],
) -> gpd.GeoDataFrame:
    """Join DA polygons to 2021 population and convert them to interior points."""

    boundaries = gpd.read_file(_required_file(
        DA_BOUNDARIES_PATH,
        "Dissemination-area boundary",
    )).to_crs(METRIC_CRS)
    population = pd.read_csv(
        _required_file(DA_POPULATION_PATH, "DA population table"),
        usecols=["DGUID", DA_POPULATION_FIELD, "Symbols"],
        low_memory=False,
    )
    population[DA_POPULATION_FIELD] = pd.to_numeric(
        population[DA_POPULATION_FIELD], errors="coerce"
    )
    population = population.drop_duplicates(subset=["DGUID"], keep="first")

    das = boundaries.merge(
        population,
        on="DGUID",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if not das["_merge"].eq("both").all():
        missing = int(das["_merge"].ne("both").sum())
        raise ValueError(f"{missing:,} DA boundaries have no population-table row.")
    das["province"] = das["PRUID"].astype(str).map(PRUID_TO_CODE)
    das = das.loc[das["province"].isin(configured_provinces)].copy()
    das["population_available"] = das[DA_POPULATION_FIELD].notna()
    das["population_2021"] = das[DA_POPULATION_FIELD].fillna(0.0).astype(float)
    das["geometry"] = das.geometry.representative_point()
    return das[[
        "DGUID",
        "DAUID",
        "province",
        "population_2021",
        "population_available",
        "Symbols",
        "geometry",
    ]]


def estimate_centre_populations(
    centres: gpd.GeoDataFrame,
    das: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Estimate centre populations from DA interior points for ranking only."""

    joined = gpd.sjoin(
        das[["population_2021", "geometry"]],
        centres[["hub_id", "geometry"]],
        how="inner",
        predicate="within",
    )
    estimates = joined.groupby("hub_id")["population_2021"].sum()
    output = centres.copy()
    output["estimated_population_2021"] = (
        output["hub_id"].map(estimates).fillna(0.0)
    )
    return output


def _hub_points(centres: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    points = centres.copy()
    points["geometry"] = points.geometry.representative_point()
    return points


def _largest_centre(centres: gpd.GeoDataFrame) -> pd.Series:
    return centres.sort_values(
        ["estimated_population_2021", "population_class", "LANDAREA", "hub_id"],
        ascending=[False, False, False, True],
    ).iloc[0]


def select_population_threshold_hubs(
    centres: gpd.GeoDataFrame,
    provinces: tuple[str, ...],
    minimum_population: int,
) -> gpd.GeoDataFrame:
    """Select official population classes and guarantee one hub per region."""

    minimum_class = MINIMUM_CLASS_BY_POPULATION[minimum_population]
    selected = centres.loc[
        centres["population_class"] >= minimum_class
    ].copy()
    selected["selection_reason"] = "population_threshold"
    additions: list[pd.Series] = []
    present = set(selected["province"])
    for province in provinces:
        if province in present:
            continue
        fallback = _largest_centre(centres.loc[centres["province"] == province]).copy()
        fallback["selection_reason"] = "jurisdiction_fallback"
        additions.append(fallback)
    if additions:
        selected = pd.concat(
            [selected, gpd.GeoDataFrame(additions, crs=centres.crs)],
            ignore_index=True,
        )
    return _hub_points(gpd.GeoDataFrame(selected, crs=centres.crs))


def load_anchor_hubs(
    registry_path: Path,
    centres: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Resolve controlled anchor rows to official provincial centre parts."""

    anchors = pd.read_csv(_required_file(registry_path, "Gasoline anchor registry"))
    required = {
        "anchor_id", "province", "pcpuid", "name", "evidence_basis",
        "evidence_url", "notes",
    }
    missing = required - set(anchors.columns)
    if missing:
        raise ValueError(f"Gasoline anchor registry fields are missing: {sorted(missing)}")
    anchors["hub_id"] = anchors["pcpuid"].astype(str).str.zfill(6)
    if anchors["hub_id"].duplicated().any():
        raise ValueError("Gasoline anchor registry contains duplicate PCPUID values.")
    configured = set(centres["province"])
    anchors = anchors.loc[anchors["province"].isin(configured)].copy()
    centre_provinces = centres.set_index("hub_id")["province"]
    unresolved = sorted(set(anchors["hub_id"]) - set(centre_provinces.index))
    if unresolved:
        raise ValueError(
            "Gasoline anchors do not resolve to configured population-centre "
            f"parts: {unresolved}"
        )
    resolved_provinces = anchors["hub_id"].map(centre_provinces)
    mismatched = anchors.loc[
        resolved_provinces.ne(anchors["province"]),
        ["anchor_id", "province", "hub_id"],
    ]
    if not mismatched.empty:
        raise ValueError(
            "Gasoline anchors contain a province/PCPUID mismatch: "
            f"{mismatched.to_dict(orient='records')}"
        )
    resolved = centres.merge(
        anchors.drop(columns=["province", "name", "pcpuid"]),
        on="hub_id",
        how="inner",
        validate="one_to_one",
    )
    return _hub_points(gpd.GeoDataFrame(resolved, crs=centres.crs))


def _weighted_distance_reduction(
    da_points: gpd.GeoDataFrame,
    candidate_geometry,
    current_distance: np.ndarray,
) -> tuple[float, np.ndarray]:
    distances = da_points.geometry.distance(candidate_geometry).to_numpy()
    improved = np.minimum(current_distance, distances)
    reduction = float(
        np.sum((current_distance - improved) * da_points["population_2021"].to_numpy())
    )
    return reduction, improved


def select_hybrid_hubs(
    centres: gpd.GeoDataFrame,
    das: gpd.GeoDataFrame,
    provinces: tuple[str, ...],
    anchor_registry: Path,
    candidate_minimum_population: int,
    additional_hubs: dict[str, int],
) -> gpd.GeoDataFrame:
    """Select controlled anchors plus greedy population-distance improvements."""

    candidates = _hub_points(centres.loc[
        centres["population_class"]
        >= MINIMUM_CLASS_BY_POPULATION[candidate_minimum_population]
    ].copy())
    anchors = load_anchor_hubs(anchor_registry, centres)
    anchors = anchors.loc[anchors["province"].isin(provinces)].copy()
    anchors["selection_reason"] = anchors["evidence_basis"].map(
        lambda value: f"supply_anchor:{value}"
    )
    selected_parts: list[gpd.GeoDataFrame] = []

    for province in provinces:
        province_centres = centres.loc[centres["province"] == province]
        province_candidates = candidates.loc[candidates["province"] == province]
        province_das = das.loc[das["province"] == province]
        chosen = anchors.loc[anchors["province"] == province].copy()
        if chosen.empty:
            fallback = _largest_centre(province_centres).to_frame().T
            chosen = _hub_points(gpd.GeoDataFrame(fallback, crs=centres.crs))
            chosen["selection_reason"] = "jurisdiction_fallback"

        current_distance = np.full(len(province_das), np.inf)
        for geometry in chosen.geometry:
            current_distance = np.minimum(
                current_distance,
                province_das.geometry.distance(geometry).to_numpy(),
            )

        remaining = province_candidates.loc[
            ~province_candidates["hub_id"].isin(chosen["hub_id"])
        ].copy()
        for _ in range(additional_hubs.get(province, 0)):
            if remaining.empty:
                break
            ranked: list[tuple[float, float, str, int, np.ndarray]] = []
            for index, candidate in remaining.iterrows():
                reduction, improved = _weighted_distance_reduction(
                    province_das,
                    candidate.geometry,
                    current_distance,
                )
                ranked.append((
                    reduction,
                    float(candidate["estimated_population_2021"]),
                    str(candidate["hub_id"]),
                    int(index),
                    improved,
                ))
            best = max(ranked, key=lambda row: (row[0], row[1], row[2]))
            selected_row = remaining.loc[[best[3]]].copy()
            selected_row["selection_reason"] = "population_distance_fill"
            chosen = pd.concat([chosen, selected_row], ignore_index=True)
            current_distance = best[4]
            remaining = remaining.drop(index=best[3])

        selected_parts.append(gpd.GeoDataFrame(chosen, crs=centres.crs))

    return gpd.GeoDataFrame(
        pd.concat(selected_parts, ignore_index=True),
        geometry="geometry",
        crs=centres.crs,
    )


def assign_das_to_hubs(
    das: gpd.GeoDataFrame,
    hubs: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Assign every DA to its nearest same-jurisdiction proxy."""

    assignments: list[pd.DataFrame] = []
    for province in sorted(das["province"].unique()):
        province_das = das.loc[das["province"] == province]
        province_hubs = hubs.loc[hubs["province"] == province]
        if province_hubs.empty:
            raise ValueError(f"No gasoline proxy was selected for {province}.")
        joined = gpd.sjoin_nearest(
            province_das,
            province_hubs[["hub_id", "PCNAME", "geometry"]],
            how="left",
            distance_col="distance_m",
        )
        joined = (
            joined.sort_values(["DGUID", "distance_m", "hub_id"])
            .drop_duplicates(subset=["DGUID"], keep="first")
        )
        assignments.append(pd.DataFrame(joined.drop(columns="geometry")))
    crosswalk = pd.concat(assignments, ignore_index=True)
    if len(crosswalk) != len(das) or crosswalk["DGUID"].nunique() != len(das):
        raise AssertionError("Every configured DA must map to exactly one proxy.")
    return crosswalk


def summarize_catchments(
    crosswalk: pd.DataFrame,
    hubs: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Aggregate DA population and distance diagnostics by proxy."""

    working = crosswalk.copy()
    working["population_distance"] = (
        working["population_2021"] * working["distance_m"]
    )
    summary = (
        working.groupby(["province", "hub_id"], as_index=False)
        .agg(
            assigned_population=("population_2021", "sum"),
            assigned_da_count=("DGUID", "count"),
            unavailable_population_da_count=(
                "population_available",
                lambda values: int((~values).sum()),
            ),
            maximum_distance_m=("distance_m", "max"),
            population_distance=("population_distance", "sum"),
        )
    )
    summary["population_weighted_mean_distance_m"] = np.where(
        summary["assigned_population"] > 0,
        summary["population_distance"] / summary["assigned_population"],
        0.0,
    )
    attributes = hubs.drop(columns="geometry").copy()
    keep = [
        "hub_id",
        "population_centre_id",
        "PCNAME",
        "province",
        "lon",
        "lat",
        "population_class",
        "estimated_population_2021",
        "selection_reason",
    ]
    for optional in ("anchor_id", "evidence_basis", "evidence_url", "notes"):
        if optional in attributes.columns:
            keep.append(optional)
    return summary.merge(attributes[keep], on=["province", "hub_id"], how="left")


def run_gasoline_proxy_build(config: GeospatialBuildConfig) -> GasolineProxyResult:
    """Build selected proxies, DA crosswalk, catchments, and selection audit."""

    output_dir = output_directory(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    centres = load_population_centres(config.study_area.provinces)
    das = load_dissemination_areas(config.study_area.provinces)
    centres = estimate_centre_populations(centres, das)
    proxy_config = config.gasoline_demand.proxies

    if proxy_config.strategy == "population_threshold":
        hubs = select_population_threshold_hubs(
            centres,
            config.study_area.provinces,
            proxy_config.minimum_population,
        )
    else:
        hubs = select_hybrid_hubs(
            centres,
            das,
            config.study_area.provinces,
            resolve_anchor_registry(config),
            proxy_config.candidate_minimum_population,
            proxy_config.additional_hubs,
        )

    hub_wgs84 = hubs.to_crs(WGS84_CRS)
    hub_wgs84["lon"] = hub_wgs84.geometry.x
    hub_wgs84["lat"] = hub_wgs84.geometry.y
    crosswalk = assign_das_to_hubs(das, hubs)
    catchments = summarize_catchments(crosswalk, hub_wgs84)

    hubs_path = output_dir / "selected_population_centre_proxies.gpkg"
    crosswalk_path = output_dir / "da_to_population_centre_proxy.csv"
    catchments_path = output_dir / "population_centre_catchments.csv"
    audit_path = output_dir / "proxy_selection_audit.csv"
    hub_wgs84.to_file(hubs_path, layer="selected_hubs", driver="GPKG")
    crosswalk.to_csv(crosswalk_path, index=False)
    catchments.to_csv(catchments_path, index=False)
    (
        catchments.groupby("province", as_index=False)
        .agg(
            selected_hubs=("hub_id", "count"),
            assigned_population=("assigned_population", "sum"),
            assigned_das=("assigned_da_count", "sum"),
            unavailable_population_das=(
                "unavailable_population_da_count",
                "sum",
            ),
            maximum_distance_m=("maximum_distance_m", "max"),
        )
        .to_csv(audit_path, index=False)
    )
    return {
        "hubs": hubs_path,
        "crosswalk": crosswalk_path,
        "catchments": catchments_path,
        "audit": audit_path,
    }


def load_provincial_sales(
    sales_year: int,
    provinces: tuple[str, ...],
) -> pd.DataFrame:
    """Load one net-gasoline observation for every configured jurisdiction."""

    sales = pd.read_csv(_required_file(FUEL_SALES_PATH, "Fuel-sales table"))
    selected = sales.loc[
        sales["REF_DATE"].eq(sales_year)
        & sales["Type of fuel sales"].eq(NET_GASOLINE_LABEL)
        & sales["GEO"].isin(PROVINCE_NAME_TO_CODE)
    ].copy()
    selected["province"] = selected["GEO"].map(PROVINCE_NAME_TO_CODE)
    selected = selected.loc[selected["province"].isin(provinces)].copy()
    if set(selected["province"]) != set(provinces):
        missing = sorted(set(provinces) - set(selected["province"]))
        raise ValueError(
            f"Fuel-sales year {sales_year} is missing jurisdictions: {missing}"
        )
    if not selected["UOM"].eq("Litres").all() or not selected[
        "SCALAR_FACTOR"
    ].eq("thousands").all():
        raise ValueError("Expected fuel sales in thousands of litres.")
    selected["province_gasoline_litres"] = (
        pd.to_numeric(selected["VALUE"], errors="raise") * 1_000.0
    )
    return selected[["province", "province_gasoline_litres"]]


def allocate_demand(
    catchments: pd.DataFrame,
    sales: pd.DataFrame,
    density_t_per_litre: float,
    sales_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Allocate provincial sales by proxy catchment population and reconcile."""

    demand = catchments.merge(sales, on="province", how="left", validate="many_to_one")
    demand["province_population"] = demand.groupby("province")[
        "assigned_population"
    ].transform("sum")
    if demand["province_population"].le(0).any():
        invalid = sorted(demand.loc[demand["province_population"] <= 0, "province"].unique())
        raise ValueError(f"Cannot allocate demand for zero-population regions: {invalid}")
    demand["allocation_weight"] = (
        demand["assigned_population"] / demand["province_population"]
    )
    demand["gasoline_litres"] = (
        demand["province_gasoline_litres"] * demand["allocation_weight"]
    )
    demand["demand"] = demand["gasoline_litres"] * density_t_per_litre
    demand["sales_year"] = sales_year
    demand["demand_units"] = "t/year"
    demand["allocation_method"] = "nearest_hub_population_share"
    demand["source_table"] = "23-10-0066-01"

    audit = (
        demand.groupby("province", as_index=False)
        .agg(
            proxy_count=("hub_id", "count"),
            allocation_weight_sum=("allocation_weight", "sum"),
            source_litres=("province_gasoline_litres", "first"),
            allocated_litres=("gasoline_litres", "sum"),
            allocated_tonnes=("demand", "sum"),
        )
    )
    audit["litre_residual"] = audit["allocated_litres"] - audit["source_litres"]
    if not np.allclose(audit["allocation_weight_sum"], 1.0, rtol=0, atol=1e-12):
        raise AssertionError("Gasoline allocation weights do not sum to one.")
    if not np.allclose(audit["allocated_litres"], audit["source_litres"]):
        raise AssertionError("Allocated gasoline does not conserve provincial totals.")
    return demand, audit


def run_gasoline_demand_build(config: GeospatialBuildConfig) -> GasolineDemandResult:
    """Allocate configured-year provincial gasoline sales to built proxies."""

    output_dir = output_directory(config)
    catchments_path = output_dir / "population_centre_catchments.csv"
    catchments = pd.read_csv(_required_file(catchments_path, "Proxy catchments"))
    sales = load_provincial_sales(
        config.gasoline_demand.sales_year,
        config.study_area.provinces,
    )
    demand, audit = allocate_demand(
        catchments,
        sales,
        config.gasoline_demand.gasoline_density_t_per_litre,
        config.gasoline_demand.sales_year,
    )
    demand_path = output_dir / "gasoline_demand_by_proxy.csv"
    audit_path = output_dir / "gasoline_demand_allocation_audit.csv"
    manifest_path = output_dir / "gasoline_demand_silver_manifest.json"
    demand.to_csv(demand_path, index=False)
    audit.to_csv(audit_path, index=False)
    manifest = {
        "schema_version": 1,
        "build_id": config.build_id,
        "study_area": config.study_area.label,
        "jurisdictions": list(config.study_area.provinces),
        "configuration": asdict(config.gasoline_demand),
        "outputs": {
            "demand": demand_path.name,
            "allocation_audit": audit_path.name,
            "selected_hubs": "selected_population_centre_proxies.gpkg",
            "da_crosswalk": "da_to_population_centre_proxy.csv",
            "catchments": catchments_path.name,
        },
        "methodology_reference": (
            "https://natural-resources.canada.ca/energy-sources/fossil-fuels/"
            "petroleum-products-distribution-networks"
        ),
        "anchor_warning": (
            "The controlled anchor registry is a modelling interpretation of "
            "published distribution evidence, not a terminal inventory."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"demand": demand_path, "audit": audit_path, "manifest": manifest_path}


def load_proxy_demand(config: GeospatialBuildConfig) -> gpd.GeoDataFrame:
    """Load allocated proxy demand as validated WGS84 point features."""

    path = output_directory(config) / "gasoline_demand_by_proxy.csv"
    demand = pd.read_csv(_required_file(path, "Silver proxy-demand table"))
    required = {
        "hub_id",
        "PCNAME",
        "province",
        "lon",
        "lat",
        "assigned_population",
        "gasoline_litres",
        "demand",
        "demand_units",
        "selection_reason",
    }
    missing = required - set(demand.columns)
    if missing:
        raise ValueError(f"Proxy-demand fields are missing: {sorted(missing)}")
    if demand.empty or demand["hub_id"].duplicated().any():
        raise ValueError("Proxy demand must contain unique, non-empty hub IDs.")
    if set(demand["province"]) != set(config.study_area.provinces):
        raise ValueError(
            "Proxy-demand jurisdictions do not match the active build profile."
        )
    numeric = [
        "lon",
        "lat",
        "assigned_population",
        "gasoline_litres",
        "demand",
    ]
    demand[numeric] = demand[numeric].apply(pd.to_numeric, errors="raise")
    if demand[numeric].isna().any().any() or demand["demand"].lt(0).any():
        raise ValueError("Proxy demand contains missing or negative numeric values.")
    return gpd.GeoDataFrame(
        demand,
        geometry=gpd.points_from_xy(demand["lon"], demand["lat"], crs=WGS84_CRS),
        crs=WGS84_CRS,
    )


def assign_basemap_region_provinces(
    regions: gpd.GeoDataFrame,
    province_boundaries: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Assign every basemap cell to the jurisdiction containing its centroid."""

    required = {
        "region",
        "site_id",
        "centroid_x",
        "centroid_y",
        "geometry",
    }
    missing = required - set(regions.columns)
    if missing:
        raise ValueError(f"Basemap fields are missing: {sorted(missing)}")
    if regions.empty or regions.crs is None:
        raise ValueError("Basemap regions must be non-empty and have a CRS.")
    if regions["region"].duplicated().any() or regions["site_id"].duplicated().any():
        raise ValueError("Basemap region and site identifiers must be unique.")
    if "province_code" not in province_boundaries.columns:
        raise ValueError("Province boundaries are missing province_code.")

    centroid_points = gpd.GeoDataFrame(
        regions[["region"]].copy(),
        geometry=gpd.points_from_xy(
            regions["centroid_x"],
            regions["centroid_y"],
            crs=regions.crs,
        ),
        crs=regions.crs,
    )
    boundaries = province_boundaries.to_crs(regions.crs)
    joined = gpd.sjoin(
        centroid_points,
        boundaries[["province_code", "geometry"]],
        how="left",
        predicate="intersects",
    )
    joined = (
        joined.sort_values(["region", "province_code"], na_position="last")
        .drop_duplicates(subset="region", keep="first")
    )
    province_by_region = joined.set_index("region")["province_code"]
    output = regions.copy()
    output["province"] = output["region"].map(province_by_region)
    if output["province"].isna().any():
        missing_regions = output.loc[output["province"].isna(), "region"].tolist()
        raise ValueError(
            "Basemap centroids could not be assigned to a jurisdiction: "
            f"{missing_regions[:10]}"
        )
    return output


def map_proxy_demand_to_regions(
    demand_points: gpd.GeoDataFrame,
    regions: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    """Map proxies to same-jurisdiction regions and conserve allocated demand."""

    if demand_points.crs is None or regions.crs is None:
        raise ValueError("Demand points and basemap regions must have a CRS.")
    if "province" not in regions.columns:
        raise ValueError("Basemap regions must have assigned provinces.")

    points_metric = demand_points.to_crs(METRIC_CRS)
    regions_metric = regions.to_crs(METRIC_CRS)
    mapped_parts: list[gpd.GeoDataFrame] = []
    for province in sorted(points_metric["province"].unique()):
        province_points = points_metric.loc[
            points_metric["province"] == province
        ].copy()
        province_regions = regions_metric.loc[
            regions_metric["province"] == province,
            ["region", "site_id", "geometry"],
        ].copy()
        if province_regions.empty:
            raise ValueError(f"Basemap has no regions assigned to {province}.")

        contained = gpd.sjoin(
            province_points,
            province_regions,
            how="left",
            predicate="intersects",
        )
        contained = (
            contained.sort_values(["hub_id", "region"], na_position="last")
            .drop_duplicates(subset="hub_id", keep="first")
        )
        matched = contained.loc[contained["region"].notna()].copy()
        matched["mapping_method"] = "point_in_same_jurisdiction_cell"
        matched["mapping_distance_m"] = 0.0

        unmatched_ids = set(province_points["hub_id"]) - set(matched["hub_id"])
        if unmatched_ids:
            unmatched = province_points.loc[
                province_points["hub_id"].isin(unmatched_ids)
            ]
            nearest = gpd.sjoin_nearest(
                unmatched,
                province_regions,
                how="left",
                distance_col="mapping_distance_m",
            )
            nearest = (
                nearest.sort_values(["hub_id", "mapping_distance_m", "region"])
                .drop_duplicates(subset="hub_id", keep="first")
            )
            nearest["mapping_method"] = "nearest_same_jurisdiction_cell"
            matched = pd.concat([matched, nearest], ignore_index=True)

        matched = matched.drop(columns=["index_right"], errors="ignore")
        mapped_parts.append(gpd.GeoDataFrame(matched, crs=METRIC_CRS))

    crosswalk = gpd.GeoDataFrame(
        pd.concat(mapped_parts, ignore_index=True),
        geometry="geometry",
        crs=METRIC_CRS,
    )
    if len(crosswalk) != len(demand_points) or crosswalk["hub_id"].duplicated().any():
        raise AssertionError("Every gasoline proxy must map to exactly one region.")
    if crosswalk[["region", "site_id"]].isna().any().any():
        raise AssertionError("Gasoline proxy mapping contains null region IDs.")

    aggregate = (
        crosswalk.groupby(["region", "site_id", "province"], as_index=False)
        .agg(
            proxy_count=("hub_id", "count"),
            assigned_population=("assigned_population", "sum"),
            gasoline_litres=("gasoline_litres", "sum"),
            demand=("demand", "sum"),
        )
    )
    regional = regions.merge(
        aggregate,
        on=["region", "site_id", "province"],
        how="left",
        validate="one_to_one",
    )
    for column in (
        "proxy_count",
        "assigned_population",
        "gasoline_litres",
        "demand",
    ):
        regional[column] = regional[column].fillna(0)
    regional["proxy_count"] = regional["proxy_count"].astype(int)
    regional["has_gasoline_demand"] = regional["proxy_count"].gt(0)
    regional["demand_units"] = "t/year"
    regional = gpd.GeoDataFrame(regional, geometry="geometry", crs=regions.crs)

    source = (
        demand_points.groupby("province", as_index=False)
        .agg(
            source_proxy_count=("hub_id", "count"),
            source_demand=("demand", "sum"),
        )
    )
    mapped = (
        crosswalk.groupby("province", as_index=False)
        .agg(
            mapped_proxy_count=("hub_id", "count"),
            mapped_demand=("demand", "sum"),
            nearest_fallback_count=(
                "mapping_method",
                lambda values: int(
                    values.eq("nearest_same_jurisdiction_cell").sum()
                ),
            ),
            maximum_mapping_distance_m=("mapping_distance_m", "max"),
        )
    )
    audit = source.merge(mapped, on="province", how="outer", validate="one_to_one")
    audit["demand_residual"] = audit["mapped_demand"] - audit["source_demand"]
    if not audit["source_proxy_count"].eq(audit["mapped_proxy_count"]).all():
        raise AssertionError("Mapped proxy counts do not reconcile by jurisdiction.")
    if not np.allclose(
        audit["mapped_demand"],
        audit["source_demand"],
        rtol=0,
        atol=1e-6,
    ):
        raise AssertionError("Mapped demand does not conserve jurisdiction totals.")
    return regional, crosswalk.to_crs(regions.crs), audit


def save_proxy_demand_preview(
    demand_points: gpd.GeoDataFrame,
    province_boundaries: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Plot allocated Silver proxy points before basemap mapping."""

    boundaries = province_boundaries.to_crs(METRIC_CRS)
    points = demand_points.to_crs(METRIC_CRS)
    maximum = float(points["demand"].max())
    marker_sizes = 18.0 + 230.0 * np.sqrt(points["demand"] / maximum)
    figure, axis = plt.subplots(figsize=(13, 10))
    boundaries.plot(
        ax=axis,
        facecolor="#f2f2f2",
        edgecolor="#777777",
        linewidth=0.45,
    )
    points.plot(
        ax=axis,
        column="demand",
        cmap="YlOrRd",
        markersize=marker_sizes,
        edgecolor="#222222",
        linewidth=0.35,
        alpha=0.85,
        legend=True,
        legend_kwds={"label": "Gasoline demand (t/year)", "shrink": 0.65},
    )
    axis.set_title("Silver gasoline-demand proxies before basemap mapping")
    axis.set_axis_off()
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def save_regional_demand_preview(
    regional: gpd.GeoDataFrame,
    crosswalk: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    """Plot demand-bearing cells for one configured basemap resolution."""

    display = regional.to_crs(METRIC_CRS)
    points = crosswalk.to_crs(METRIC_CRS)
    positive = display.loc[display["demand"] > 0].copy()
    figure, axis = plt.subplots(figsize=(13, 10))
    display.plot(
        ax=axis,
        facecolor="#eeeeee",
        edgecolor="#aaaaaa",
        linewidth=0.12,
    )
    if not positive.empty:
        minimum = float(positive["demand"].min())
        maximum = max(float(positive["demand"].max()), minimum * 1.000001)
        positive.plot(
            ax=axis,
            column="demand",
            cmap="YlOrRd",
            norm=LogNorm(vmin=minimum, vmax=maximum),
            edgecolor="#555555",
            linewidth=0.25,
            legend=True,
            legend_kwds={
                "label": "Mapped gasoline demand (t/year, log scale)",
                "shrink": 0.65,
            },
        )
    points.plot(
        ax=axis,
        color="#17202a",
        markersize=7,
        alpha=0.75,
    )
    resolution = regional["resolution"].iloc[0]
    unit = regional["resolution_unit"].iloc[0]
    axis.set_title(
        f"Silver gasoline demand mapped to {resolution:g} {unit} regions"
    )
    axis.set_axis_off()
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)


def export_gasoline_basemap_products(
    *,
    basemap_stem: str,
    regional: gpd.GeoDataFrame,
    crosswalk: gpd.GeoDataFrame,
    audit: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Path]:
    """Export one resolution's spatial layers, tables, audit, and PNG."""

    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = output_dir / f"{basemap_stem}_gasoline_demand.gpkg"
    temporary_gpkg = output_dir / f"{basemap_stem}_gasoline_demand.tmp.gpkg"
    regional_csv = output_dir / f"{basemap_stem}_regional_gasoline_demand.csv"
    crosswalk_csv = output_dir / f"{basemap_stem}_gasoline_region_crosswalk.csv"
    audit_csv = output_dir / f"{basemap_stem}_gasoline_mapping_audit.csv"
    preview_path = preview_dir / f"{basemap_stem}_gasoline_demand_mapping.png"

    temporary_gpkg.unlink(missing_ok=True)
    regional.to_file(
        temporary_gpkg,
        layer="regional_gasoline_demand",
        driver="GPKG",
    )
    crosswalk.to_file(
        temporary_gpkg,
        layer="gasoline_region_crosswalk",
        driver="GPKG",
        mode="a",
    )
    temporary_gpkg.replace(gpkg_path)
    regional.drop(columns="geometry").to_csv(regional_csv, index=False)
    crosswalk.drop(columns="geometry").to_csv(crosswalk_csv, index=False)
    audit.to_csv(audit_csv, index=False)
    save_regional_demand_preview(regional, crosswalk, preview_path)
    return {
        "gpkg": gpkg_path,
        "regional_csv": regional_csv,
        "crosswalk_csv": crosswalk_csv,
        "audit_csv": audit_csv,
        "mapping_preview": preview_path,
    }


def run_gasoline_basemap_build(
    config: GeospatialBuildConfig,
) -> GasolineBasemapResult:
    """Map allocated proxy demand onto every compatible profile basemap."""

    output_dir = output_directory(config)
    basemap_output_dir = output_dir / "basemaps"
    preview_dir = output_dir / "preview"
    basemap_output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    demand_points = load_proxy_demand(config)
    boundary_path = find_boundary_shapefile(RAW_BASEMAPS)
    boundaries = load_province_boundaries(boundary_path)
    boundaries = boundaries.loc[
        boundaries["province_code"].isin(config.study_area.provinces)
    ].copy()
    source_preview = preview_dir / "gasoline_demand_proxies.png"
    save_proxy_demand_preview(demand_points, boundaries, source_preview)

    summary_rows: list[dict[str, object]] = []
    for basemap_path in find_basemap_files(PROCESSED_BASEMAPS, config):
        regions = gpd.read_file(basemap_path, layer="regions")
        regions = assign_basemap_region_provinces(regions, boundaries)
        regional, crosswalk, audit = map_proxy_demand_to_regions(
            demand_points,
            regions,
        )
        outputs = export_gasoline_basemap_products(
            basemap_stem=basemap_path.stem,
            regional=regional,
            crosswalk=crosswalk,
            audit=audit,
            output_dir=basemap_output_dir,
        )
        summary_rows.append({
            "basemap_file": basemap_path.name,
            "regions": len(regional),
            "demand_regions": int(regional["has_gasoline_demand"].sum()),
            "mapped_proxies": len(crosswalk),
            "nearest_fallback_proxies": int(
                crosswalk["mapping_method"]
                .eq("nearest_same_jurisdiction_cell")
                .sum()
            ),
            "source_demand": float(demand_points["demand"].sum()),
            "mapped_demand": float(regional["demand"].sum()),
            "maximum_mapping_distance_m": float(
                crosswalk["mapping_distance_m"].max()
            ),
            **{key: path.name for key, path in outputs.items()},
        })

    summary = pd.DataFrame(summary_rows)
    summary_path = output_dir / "gasoline_basemap_mapping_summary.csv"
    summary.to_csv(summary_path, index=False)
    manifest_path = output_dir / "gasoline_basemap_mapping_manifest.json"
    manifest = {
        "schema_version": 1,
        "build_id": config.build_id,
        "study_area": config.study_area.label,
        "jurisdictions": list(config.study_area.provinces),
        "source_proxy_demand": "gasoline_demand_by_proxy.csv",
        "source_preview": str(source_preview.relative_to(output_dir)),
        "summary": summary_path.name,
        "basemaps": summary.to_dict(orient="records"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "summary": summary_path,
        "manifest": manifest_path,
        "source_preview": source_preview,
    }
