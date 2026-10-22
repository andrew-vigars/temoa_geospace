import pandas as pd
import pytest

from geocanoe.preprocessing.atb_wind import resolve_wind_crosswalk, wind_mapping


def source():
    return pd.DataFrame(
        [
            dict(
                technology="LandbasedWind",
                techdetail=f"Class{c}",
                techdetail2=f"Wind Turbine Technology {t}",
                resourcedetail=f"Class{c}",
                display_name=f"Land-Based Wind - Class {c} - Technology {t}",
            )
            for c, t in [(c, 1 if c <= 7 else c - 6) for c in range(1, 11)]
        ]
    )


def test_native_mapping_and_duplicate_metrics():
    rows = source()
    mapping = wind_mapping(pd.concat([rows, rows]))
    assert mapping.turbine_technology.tolist() == [1] * 7 + [2, 3, 4]


def test_ambiguous_or_missing_mapping_rejected():
    rows = source()
    with pytest.raises(ValueError, match="exactly one"):
        wind_mapping(rows.iloc[:-1])
    conflict = rows.iloc[[0]].assign(techdetail2="Wind Turbine Technology 2")
    with pytest.raises(ValueError, match="exactly one"):
        wind_mapping(pd.concat([rows, conflict]))


def test_cost_selection_matches_physical_turbine():
    crosswalk = pd.DataFrame(
        [dict(tech="WIND_GEN", physical_reference="T1_curve", display_name="old")]
    )
    resolved = resolve_wind_crosswalk(crosswalk, wind_mapping(source()), 4)
    assert resolved.display_name.iloc[0] == "Land-Based Wind - Class 4 - Technology 1"
    assert crosswalk.display_name.iloc[0] == "old"
    with pytest.raises(ValueError, match="power curve"):
        resolve_wind_crosswalk(crosswalk, wind_mapping(source()), 8)


def test_canoe_representatives_are_validated_against_native_atb():
    from geocanoe.preprocessing.atb_wind import canoe_wind_mapping

    mapping = wind_mapping(source())
    selected = canoe_wind_mapping(mapping, {"T1": 7, "T2": 8, "T3": 9})
    assert selected.resource_class.tolist() == [7, 8, 9]
    assert selected.turbine_technology.tolist() == [1, 2, 3]
    with pytest.raises(ValueError, match="disagrees"):
        canoe_wind_mapping(mapping, {"T1": 8, "T2": 7, "T3": 9})
