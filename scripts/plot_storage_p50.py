"""Plot source-footprint P50 density without allocating model-region capacity."""

import sqlite3
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.cm import ScalarMappable
import pandas as pd

from geocanoe.acquisition.co2_storage import find_latest_raw_storage_gpkg
from geocanoe.geospatial.co2_storage import load_storage_source, METRIC_CRS
from geocanoe.paths import find_project_root


def main() -> None:
    root = find_project_root()
    source = find_latest_raw_storage_gpkg(root / "data_files/raw/canco2_storage")
    features, _, assessments = load_storage_source(source)
    silver_path = root / "data_files/processed/co2_storage/prov_quant_basemap_25km_centroid_co2_storage.gpkg"
    connection = sqlite3.connect(f"file:{silver_path.as_posix()}?mode=ro", uri=True)
    try:
        assessment_ids = {row[0] for row in connection.execute(
            "SELECT DISTINCT storage_assessment_id FROM storage_capacity_mapping"
        )}
    finally:
        connection.close()
    regions = gpd.read_file(
        silver_path,
        layer="regional_storage_evidence",
    ).to_crs(METRIC_CRS)
    features = features.to_crs(METRIC_CRS)
    assessments["storage_p50_tonnes"] = pd.to_numeric(
        assessments["storage_p50_tonnes"], errors="coerce"
    )
    positive = assessments.loc[
        assessments["storage_assessment_id"].isin(assessment_ids)
        & assessments["storage_p50_tonnes"].gt(0)
        & assessments["storage_p50_tonnes"].lt(float("inf"))
    ].copy()
    footprints = []
    for scope, key in [("feature", "storage_feature_id"), ("unit", "storage_unit_id")]:
        scoped = positive.loc[positive["assessment_scope"].eq(scope)]
        if scoped[key].duplicated().any():
            raise ValueError(f"Multiple P50 assessments for the same {scope}; select a method first.")
        geometry = features[[key, "geometry"]].dissolve(by=key).reset_index()
        mapped = geometry.merge(
            scoped[[key, "storage_p50_tonnes", "source_dataset"]],
            on=key,
            how="inner",
            validate="one_to_one",
        )
        mapped["assessment_scope"] = scope
        mapped["p50_tonnes_per_km2"] = (
            mapped["storage_p50_tonnes"] / (mapped.geometry.area / 1e6)
        )
        footprints.append(mapped)
    mapped = gpd.GeoDataFrame(pd.concat(footprints, ignore_index=True), crs=METRIC_CRS)
    # Compute density using the complete assessed footprint before clipping.
    mapped = gpd.clip(mapped, regions.geometry.union_all())
    mapped = mapped.loc[mapped.geometry.area.gt(0)].copy()
    norm = LogNorm(
        vmin=mapped["p50_tonnes_per_km2"].min(),
        vmax=mapped["p50_tonnes_per_km2"].max(),
    )
    figure, axis = plt.subplots(figsize=(15, 9))
    regions.plot(ax=axis, facecolor="#f5f5f3", edgecolor="#d8dcda", linewidth=0.13)
    # Unit polygons first so finer feature evidence remains visible.
    for scope in ["unit", "feature"]:
        mapped.loc[mapped["assessment_scope"].eq(scope)].plot(
            ax=axis, column="p50_tonnes_per_km2", cmap="inferno",
            norm=norm, edgecolor="none", linewidth=0,
        )
    colorbar = figure.colorbar(
        ScalarMappable(norm=norm, cmap="inferno"), ax=axis,
        fraction=0.025, pad=0.015, shrink=0.65,
    )
    colorbar.set_label("P50 CO₂ storage / assessment footprint area (tonnes/km²) · log scale")
    axis.set_title("P50 geological CO₂ storage density", fontsize=20, loc="left", pad=18)
    axis.set_axis_off()
    figure.text(
        0.08, 0.07,
        "Sources: " + ", ".join(sorted(mapped["source_dataset"].unique())) + ". Atlantic excluded.\n"
        "Source-footprint averages; overlapping assessments are not summed. "
        "These are not allocated model-region capacities.",
        fontsize=10, color="#555555",
    )
    output = root / "data_files/processed/co2_storage/preview/prov_quant_p50_density.png"
    figure.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    print(output)
    print(f"Mapped {len(mapped):,} assessment footprints; density range "
          f"{norm.vmin:,.1f}–{norm.vmax:,.1f} tonnes/km²")


if __name__ == "__main__":
    main()
