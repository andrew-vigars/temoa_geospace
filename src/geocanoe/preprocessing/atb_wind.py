"""Read wind resource-to-technology relationships from the ATB source itself."""


def wind_mapping(master):
    columns = ["techdetail", "techdetail2", "resourcedetail", "display_name"]
    rows = master.loc[
        (master.technology == "LandbasedWind")
        & master.techdetail.str.fullmatch(r"Class\d+"),
        columns,
    ].drop_duplicates()
    rows = rows.rename(columns={"techdetail": "resource_class"})
    rows["resource_class"] = rows.resource_class.str.extract(r"Class(\d+)")[0].astype(
        int
    )
    rows["turbine_technology"] = rows.techdetail2.str.extract(
        r"^Wind Turbine Technology (\d+)$"
    )[0]
    if rows.turbine_technology.isna().any():
        raise ValueError("ATB wind row has an unrecognized turbine technology.")
    rows["turbine_technology"] = rows.turbine_technology.astype(int)
    if not rows.resource_class.is_unique or set(rows.resource_class) != set(
        range(1, 11)
    ):
        raise ValueError(
            "ATB wind mapping must resolve exactly one row for each class 1–10."
        )
    if not (rows.resourcedetail == "Class" + rows.resource_class.astype(str)).all():
        raise ValueError("ATB wind resource columns disagree.")
    return rows.sort_values("resource_class").reset_index(drop=True)


def resolve_wind_crosswalk(crosswalk, mapping, resource_class=7):
    """Resolve the existing physical pilot's cost row without assuming class=T ID."""
    row = mapping.loc[mapping.resource_class == resource_class]
    if len(row) != 1:
        raise ValueError("Configured ATB wind resource class is unavailable.")
    result = crosswalk.copy()
    wind = result.tech == "WIND_GEN"
    expected = "T" + str(row.turbine_technology.iloc[0]) + "_"
    if not result.loc[wind, "physical_reference"].str.startswith(expected).all():
        raise ValueError(
            "ATB-selected turbine does not match the configured physical power curve."
        )
    result.loc[wind, "display_name"] = row.display_name.iloc[0]
    return result


def canoe_wind_mapping(mapping, selections):
    """Select CANOE's representative ATB rows, without assigning spatial fractions."""
    if set(selections) not in ({"T1", "T2", "T3"}, {"T1", "T2", "T3", "T4"}):
        raise ValueError("CANOE wind mapping requires T1, T2 and T3 selections.")
    selected = []
    for turbine, resource_class in selections.items():
        row = mapping.loc[mapping.resource_class == resource_class].copy()
        if len(row) != 1 or row.turbine_technology.iloc[0] != int(turbine[1:]):
            raise ValueError(
                f"CANOE mapping {turbine}/Class {resource_class} disagrees with ATB."
            )
        selected.append(row.index[0])
    return mapping.loc[selected].reset_index(drop=True)
