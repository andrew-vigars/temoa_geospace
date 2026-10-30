import pandas as pd
import pytest

from geocanoe.preprocessing.fuel_supply import select_price


def test_selects_terminal_year_not_five_year_default():
    frame = pd.DataFrame({'Tech Name': ['T_gsl', 'T_gsl', 'I_gsl'],
                          'period': ['2030', '2050', '2050'], 'value': [30, 22, 19]})
    assert select_price(frame, 2050) == 22


@pytest.mark.parametrize('values', [[], [0], [float('nan')], [float('inf')], [22, 23]])
def test_rejects_missing_invalid_or_ambiguous_prices(values):
    frame = pd.DataFrame({'Tech Name': ['T_gsl'] * len(values),
                          'period': ['2050'] * len(values), 'value': values})
    with pytest.raises(ValueError):
        select_price(frame, 2050)
