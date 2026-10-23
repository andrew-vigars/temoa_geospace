"""Economic policy checks against independently selected StatCan observations."""

import pytest

from geocanoe.costs.economics import conversion_basis, validate_preconverted_source


def test_usd_2009_currency_first_then_gdp_rebasing():
    conversion = conversion_basis("USD", 2009)
    assert conversion["multiplier"] == pytest.approx(1.202244 * 104.3 / 87.6)
    assert conversion["basis"]["index_provenance"]["table"] == "36-10-0223-01"


def test_preconverted_cad2020_is_identity():
    assert conversion_basis("CAD", 2020)["multiplier"] == 1
    assert validate_preconverted_source("h2_pipeline_workbook", "CAD", 2020)[
        "already_converted"
    ]
    with pytest.raises(ValueError, match="cannot be relabelled"):
        validate_preconverted_source("h2_pipeline_workbook", "USD", 2009)


def test_scale_and_missing_economic_metadata():
    assert conversion_basis("CAD", 2020, 1e6)["multiplier"] == 1e6
    for currency, year in [("USD", 1999), ("CAD", 2025), ("JPY", 2020)]:
        with pytest.raises(ValueError):
            conversion_basis(currency, year)
    with pytest.raises(ValueError):
        conversion_basis("USD", 2009, float("nan"))


def test_h2_importer_preserves_preconverted_costs_and_rejects_relabelling():
    import pandas as pd

    from geocanoe.costs.pipelines.h2.capacity_costs import add_cost_model_metadata

    original = pd.DataFrame({
        "diameter_in": [12.0], "capacity_t_h2_per_year": [1000.0],
        "total_capex_cad2020_per_km": [123456.0],
        "total_variable_opex_cad2020_per_year_per_km": [123.0],
        "total_fixed_opex_cad2020_per_year_per_km": [456.0],
    })
    result = add_cost_model_metadata(
        original, "H2_PIPE", "h2", "CAD", 2020, "embedded.xlsx", "v1"
    )
    pd.testing.assert_frame_equal(result[original.columns], original)
    with pytest.raises(ValueError, match="cannot be relabelled"):
        add_cost_model_metadata(
            original, "H2_PIPE", "h2", "USD", 2009, "embedded.xlsx", "v1"
        )


def test_exchange_lookup_depends_on_original_cost_year_and_currency():
    usd2009 = conversion_basis("USD", 2009)
    usd2023 = conversion_basis("USD", 2023)
    assert usd2009["basis"]["cad_per_source_currency"] == 1.202244
    assert usd2023["basis"]["cad_per_source_currency"] == 1.225042
    assert usd2023["multiplier"] == pytest.approx(1.225042 * 104.3 / 123.2)
    assert conversion_basis("EUR", 2020)["multiplier"] == pytest.approx(1.77806488)
