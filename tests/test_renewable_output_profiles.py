import pandas as pd
import pytest

from geocanoe.analysis.renewables import weighted_profiles


def test_province_profile_uses_capacity_and_sums_vintages():
    capacity = pd.DataFrame(
        [
            dict(scenario="s", period=2030, region=r, tech="WIND_GEN", capacity=c)
            for r, c in [("a", 1), ("a", 2), ("b", 9), ("c", 0)]
        ]
    )
    profiles = pd.DataFrame(
        [
            dict(region=r, tech="WIND_GEN", month=1, hours=744, factor=f)
            for r, f in [("a", 0.2), ("b", 0.6), ("c", 0)]
        ]
    )
    grid, province = weighted_profiles(
        capacity, profiles, pd.Series({"a": "AB", "b": "AB", "c": "BC"})
    )
    assert len(grid) == 2
    assert province.capacity.item() == 12
    assert province.factor.item() == pytest.approx(0.5)
    assert province.available_mwh.item() == pytest.approx(6 * 744)
    with pytest.raises(ValueError, match="Missing renewable"):
        weighted_profiles(
            capacity, profiles.iloc[:1], pd.Series({"a": "AB", "b": "AB"})
        )
    partial = pd.concat([profiles, profiles.iloc[[0]].assign(month=2, hours=672)])
    with pytest.raises(ValueError, match="Missing renewable profile months"):
        weighted_profiles(capacity, partial, pd.Series({"a": "AB", "b": "AB"}))
