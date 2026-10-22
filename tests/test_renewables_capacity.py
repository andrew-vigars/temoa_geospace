import pandas as pd
import pytest
from geocanoe.preprocessing.renewables_capacity import capacity_bounds


def test_explicit_capacity_bounds_and_zero_exclusion(tmp_path):
    path = tmp_path / "input.csv"
    rows = pd.DataFrame(
        [
            dict(
                region="R0",
                tech=t,
                eligible_area_km2=a,
                density_mw_per_km2=5,
                basis="scenario",
                source="test assumption",
            )
            for t, a in [("WIND_GEN", 10), ("SOLAR_GEN", 0)]
        ]
    )
    rows.to_csv(path, index=False)
    bounds, audit = capacity_bounds(path, ["R0"], 2025)
    assert [r["capacity"] for r in bounds] == [50, 0]
    assert [r["units"] for r in bounds] == ["MW", "MW"]
    assert audit.capacity_mw.sum() == 50
    with pytest.raises(ValueError, match="every region"):
        capacity_bounds(path, ["R0", "R1"], 2025)
    rows.loc[0, "eligible_area_km2"] = -1
    rows.to_csv(path, index=False)
    with pytest.raises(ValueError, match="nonnegative"):
        capacity_bounds(path, ["R0"], 2025)
