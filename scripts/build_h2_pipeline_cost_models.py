"""
build_h2_pipeline_cost_models.py

Fit and select topology-independent hydrogen-pipeline cost functions for the
Geospatial-CANOE cost workflow.

This script reads the normalized H2 pipeline capacity-cost observations produced
by ``build_h2_pipeline_capacity_costs.py``. It fits linear and power functions to
CAPEX, fixed OPEX, and variable OPEX; calculates diagnostics in the original
cost units; selects the configured model form for each cost component; and
exports the selected cost-model definitions used by the downstream schema
template workflow.

Input
-----
data_files/processed/costs/transport/pipelines/h2_pipeline/
    h2_pipeline_normalized_capacity_costs.csv

Output
------
data_files/processed/costs/transport/pipelines/h2_pipeline/
    h2_pipeline_cost_model_selection.csv

Selected default model forms
----------------------------
- CAPEX: power
- Fixed OPEX: linear
- Variable OPEX: linear
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd


# =============================================================================
# Canonical configuration
# =============================================================================

CAPACITY_COLUMN = "capacity_t_h2_per_year"

COST_COLUMNS = {
    "capex": "total_capex_cad2020_per_km",
    "variable_opex": (
        "total_variable_opex_cad2020_per_year_per_km"
    ),
    "fixed_opex": (
        "total_fixed_opex_cad2020_per_year_per_km"
    ),
}

METADATA_COLUMNS = [
    "technology",
    "commodity",
    "currency",
    "currency_year",
    "source_workbook",
    "cost_model_version",
]

REQUIRED_COLUMNS = [
    *METADATA_COLUMNS,
    "diameter_in",
    CAPACITY_COLUMN,
    *COST_COLUMNS.values(),
]

SELECTED_MODEL_TYPES = {
    "capex": "power",
    "variable_opex": "linear",
    "fixed_opex": "linear",
}

SELECTED_MODEL_OUTPUT_COLUMNS = [
    "technology",
    "commodity",
    "cost_type",
    "model_type",
    "equation",
    "slope",
    "intercept",
    "coefficient",
    "exponent",
    "capacity_min",
    "capacity_max",
    "capacity_column",
    "cost_column",
    "currency",
    "currency_year",
    "source_workbook",
    "cost_model_version",
    "r_squared",
    "rmse",
    "mae",
    "maximum_absolute_error",
]


DEFAULT_DATA_ID = "GEO001"
DEFAULT_ETL_SEGMENT_COUNT = 5
DEFAULT_ETL_SPACING = "log"

ETLSEGMENT_TEMPLATE_COLUMNS = [
    "tech_or_group",
    "segment",
    "cap_lower",
    "cap_upper",
    "cost_lower_per_km",
    "cost_upper_per_km",
    "data_id",
]

OPEX_COEFFICIENT_COLUMNS = [
    "technology",
    "commodity",
    "cost_type",
    "coefficient_per_km",
    "intercept_cost",
    "units",
    "notes",
    "data_source",
    "dq_cred",
    "dq_geog",
    "dq_struc",
    "dq_tech",
    "dq_time",
    "data_id",
]


class RegressionMetrics(TypedDict):
    """Regression diagnostics evaluated in the original cost units.

    Attributes
    ----------
    r_squared : float
        Coefficient of determination measuring the proportion of observed cost
        variance explained by the fitted model.
    rmse : float
        Root mean squared error between observed and predicted costs.
    mae : float
        Mean absolute error between observed and predicted costs.
    maximum_absolute_error : float
        Largest absolute difference between an observed and predicted cost.
    """

    r_squared: float
    rmse: float
    mae: float
    maximum_absolute_error: float


class FittedCostModel(TypedDict):
    """Coefficients, equation metadata, and diagnostics for one fitted cost model.

    Attributes
    ----------
    model_type : str
        Functional form used for the fitted model, such as ``"linear"`` or
        ``"power"``.
    equation : str
        Human-readable representation of the fitted cost equation.
    slope : float
        Linear-model slope. Set to ``NaN`` when the fitted model is not linear.
    intercept : float
        Linear-model intercept. Set to ``NaN`` when the fitted model is not linear.
    coefficient : float
        Multiplicative coefficient of a power model. Set to ``NaN`` when the fitted
        model is not a power function.
    exponent : float
        Capacity exponent of a power model. Set to ``NaN`` when the fitted model is
        not a power function.
    r_squared : float
        Coefficient of determination evaluated in the original cost units.
    rmse : float
        Root mean squared error evaluated in the original cost units.
    mae : float
        Mean absolute error evaluated in the original cost units.
    maximum_absolute_error : float
        Largest absolute difference between an observed and predicted cost.
    """

    model_type: str
    equation: str
    slope: float
    intercept: float
    coefficient: float
    exponent: float
    r_squared: float
    rmse: float
    mae: float
    maximum_absolute_error: float


# =============================================================================
# Project discovery and paths
# =============================================================================

def find_project_root(start_path: Path | None = None) -> Path:
    """Locate the Geospatial-CANOE repository root.

    The search begins from ``start_path`` when supplied. Otherwise, it begins from
    both the current script location and the current working directory. Each
    starting location and its parent directories are inspected in order, and the
    first directory containing both ``scripts/`` and ``data_files/`` is returned.

    Parameters
    ----------
    start_path : Path | None, optional
        Explicit file or directory from which to begin the upward search. When
        omitted, the script location and current working directory are used.

    Returns
    -------
    Path
        Resolved path to the detected Geospatial-CANOE repository root.

    Raises
    ------
    FileNotFoundError
        If no searched directory contains both ``scripts/`` and ``data_files/``.
    """

    search_starts: list[Path] = []

    if start_path is not None:
        search_starts.append(start_path.resolve())
    else:
        search_starts.extend(
            [
                Path(__file__).resolve(),
                Path.cwd().resolve(),
            ]
        )

    for start in search_starts:
        candidate_start = start if start.is_dir() else start.parent

        for candidate in [candidate_start, *candidate_start.parents]:
            if (
                (candidate / "scripts").is_dir()
                and (candidate / "data_files").is_dir()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate the Geospatial-CANOE project root. "
        "Expected to find both scripts/ and data_files/."
    )


def default_input_path(project_root: Path) -> Path:
    """Return the canonical normalized H2 pipeline capacity-cost CSV path.

Parameters
----------
project_root : Path
    Root directory of the Geospatial-CANOE repository.

Returns
-------
Path
    Path to the processed H2 pipeline capacity-cost CSV used as the input to
    the cost-model fitting workflow.
    """

    return (
        project_root
        / "data_files"
        / "processed"
        / "costs"
        / "transport"
        / "pipelines"
        / "h2_pipeline"
        / "h2_pipeline_normalized_capacity_costs.csv"
    )


def default_cost_directory(project_root: Path) -> Path:
    """Return the canonical processed H2 pipeline cost directory.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the directory containing processed H2 pipeline cost datasets,
        fitted model outputs, and schema-ready cost templates.
    """

    return (
        project_root
        / "data_files"
        / "processed"
        / "costs"
        / "transport"
        / "pipelines"
        / "h2_pipeline"
    )


def default_model_selection_path(project_root: Path) -> Path:
    """Return the canonical selected H2 pipeline cost-model CSV path.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the CSV containing the selected fitted model definition for each
        H2 pipeline cost component.
    """

    return (
        default_cost_directory(project_root)
        / "h2_pipeline_cost_model_selection.csv"
    )


def default_etlsegment_template_path(project_root: Path) -> Path:
    """Return the canonical topology-free H2 pipeline ETLSegment template path.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the CSV containing the capacity and cost breakpoints used to build
        distance-scaled H2 pipeline ETLSegment records downstream.
    """

    return (
        default_cost_directory(project_root)
        / "h2_pipeline_etlsegment_template.csv"
    )


def default_opex_coefficient_path(project_root: Path) -> Path:
    """Return the canonical topology-free H2 pipeline OPEX coefficient path.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the CSV containing the fixed- and variable-OPEX coefficients used
        to construct distance-scaled pipeline cost records downstream.
    """

    return (
        default_cost_directory(project_root)
        / "h2_pipeline_opex_coefficients.csv"
    )


# =============================================================================
# Input loading and validation
# =============================================================================

def load_capacity_cost_table(input_path: Path) -> pd.DataFrame:
    """Load the processed H2 pipeline capacity-cost table.

    Parameters
    ----------
    input_path : Path
        Path to the normalized H2 pipeline capacity-cost CSV.

    Returns
    -------
    pd.DataFrame
        Loaded capacity-cost observations used for downstream model fitting.

    Raises
    ------
    FileNotFoundError
        If ``input_path`` does not exist.
    ValueError
        If the loaded CSV contains no rows.
    """

    if not input_path.exists():
        raise FileNotFoundError(
            "Processed H2 pipeline capacity-cost table not found: "
            f"{input_path}"
        )

    cost_table = pd.read_csv(
        input_path,
        encoding="utf-8",
    )

    if cost_table.empty:
        raise ValueError(
            "Processed H2 pipeline capacity-cost table is empty."
        )

    return cost_table


def validate_capacity_cost_table(
    cost_table: pd.DataFrame,
    required_columns: list[str],
    metadata_columns: list[str],
    capacity_column: str,
    cost_columns: dict[str, str],
) -> None:
    """Validate the processed H2 pipeline capacity-cost input table.

    The validation checks required and duplicate columns, numeric conversion,
    finite and positive engineering values, unique diameter-capacity cases, and
    consistent non-missing metadata across all observations.

    Parameters
    ----------
    cost_table : pd.DataFrame
        Processed H2 pipeline capacity-cost observations to validate.
    required_columns : list[str]
        Column names that must be present in ``cost_table``.
    metadata_columns : list[str]
        Metadata columns expected to contain one consistent non-missing value.
    capacity_column : str
        Name of the annual pipeline-capacity column.
    cost_columns : dict[str, str]
        Mapping from cost-component names to their corresponding numeric columns.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If required columns are missing, column names are duplicated, numeric
        values are missing, non-numeric, non-finite, or non-positive, duplicate
        diameter-capacity cases are present, or metadata values are missing or
        inconsistent.
    """

    missing_columns = [
        column
        for column in required_columns
        if column not in cost_table.columns
    ]

    if missing_columns:
        raise ValueError(
            "Pipeline capacity-cost table is missing required columns: "
            f"{missing_columns}"
        )

    duplicated_columns = (
        cost_table.columns[
            cost_table.columns.duplicated()
        ]
        .tolist()
    )

    if duplicated_columns:
        raise ValueError(
            "Pipeline capacity-cost table contains duplicate columns: "
            f"{duplicated_columns}"
        )

    numeric_columns = [
        "diameter_in",
        capacity_column,
        *cost_columns.values(),
    ]

    for column in numeric_columns:
        numeric_values = pd.to_numeric(
            cost_table[column],
            errors="coerce",
        )

        if numeric_values.isna().any():
            invalid_count = int(numeric_values.isna().sum())
            raise ValueError(
                f"Column '{column}' contains {invalid_count} missing or "
                "non-numeric value(s)."
            )

        if not np.isfinite(numeric_values.to_numpy(dtype=float)).all():
            raise ValueError(
                f"Column '{column}' contains non-finite values."
            )

        if (numeric_values <= 0).any():
            raise ValueError(
                f"Column '{column}' must contain only positive values."
            )

    duplicated_cases = cost_table.duplicated(
        subset=[
            "diameter_in",
            capacity_column,
        ],
        keep=False,
    )

    if duplicated_cases.any():
        duplicate_table = cost_table.loc[
            duplicated_cases,
            [
                "diameter_in",
                capacity_column,
            ],
        ]

        raise ValueError(
            "Duplicate diameter-capacity cases were found:\n"
            f"{duplicate_table.to_string(index=False)}"
        )

    for column in metadata_columns:
        if cost_table[column].isna().any():
            raise ValueError(
                f"Metadata column '{column}' contains missing values."
            )

        unique_values = cost_table[column].unique()

        if len(unique_values) != 1:
            raise ValueError(
                f"Metadata column '{column}' must contain exactly one "
                f"value; found {unique_values.tolist()}."
            )


# =============================================================================
# Regression diagnostics
# =============================================================================

def calculate_regression_metrics(
    observed: np.ndarray,
    predicted: np.ndarray,
) -> RegressionMetrics:
    """Calculate regression diagnostics in the original cost units.

    The observed and predicted arrays are converted to floating-point NumPy arrays,
    validated for matching shape, non-empty contents, and finite values, and then
    used to calculate residual-based goodness-of-fit and error statistics.

    Parameters
    ----------
    observed : np.ndarray
        Observed cost values in the original cost units.
    predicted : np.ndarray
        Model-predicted cost values in the same units and shape as ``observed``.

    Returns
    -------
    RegressionMetrics
        Coefficient of determination, root mean squared error, mean absolute error,
        and maximum absolute error calculated from the residuals.

    Raises
    ------
    ValueError
        If the arrays have different shapes, contain no values, or include
        non-finite values.

    Notes
    -----
    ``r_squared`` is returned as ``NaN`` when all observed values are identical
    because the total sum of squares is zero.
    """

    observed_values = np.asarray(
        observed,
        dtype=float,
    )

    predicted_values = np.asarray(
        predicted,
        dtype=float,
    )

    if observed_values.shape != predicted_values.shape:
        raise ValueError(
            "Observed and predicted arrays must have the same shape."
        )

    if observed_values.size == 0:
        raise ValueError(
            "Regression metrics cannot be calculated from empty arrays."
        )

    if not np.isfinite(observed_values).all():
        raise ValueError(
            "Observed values contain non-finite values."
        )

    if not np.isfinite(predicted_values).all():
        raise ValueError(
            "Predicted values contain non-finite values."
        )

    residuals = observed_values - predicted_values

    residual_sum_of_squares = float(
        np.sum(residuals ** 2)
    )

    total_sum_of_squares = float(
        np.sum(
            (observed_values - observed_values.mean()) ** 2
        )
    )

    r_squared = (
        float("nan")
        if total_sum_of_squares == 0
        else float(
            1.0
            - residual_sum_of_squares
            / total_sum_of_squares
        )
    )

    return {
        "r_squared": r_squared,
        "rmse": float(
            np.sqrt(
                np.mean(residuals ** 2)
            )
        ),
        "mae": float(
            np.mean(
                np.abs(residuals)
            )
        ),
        "maximum_absolute_error": float(
            np.max(
                np.abs(residuals)
            )
        ),
    }


# =============================================================================
# Candidate cost functions
# =============================================================================

def validate_regression_arrays(
    capacity: np.ndarray,
    cost: np.ndarray,
    model_name: str,
    require_positive: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and normalize capacity and cost arrays for regression fitting.

    The input arrays are converted to floating-point NumPy arrays and checked for
    matching shape, sufficient observations, finite values, and at least two unique
    capacity values. Strict positivity is additionally enforced when required by
    the selected regression form.

    Parameters
    ----------
    capacity : np.ndarray
        Pipeline-capacity observations used as the independent variable.
    cost : np.ndarray
        Cost observations used as the dependent variable.
    model_name : str
        Human-readable model name used in validation error messages.
    require_positive : bool, optional
        Whether both capacity and cost values must be strictly positive, as required
        for log-transformed power regression. Defaults to ``False``.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Validated floating-point capacity and cost arrays.

    Raises
    ------
    ValueError
        If the arrays have different shapes, contain fewer than two observations,
        include non-finite values, contain fewer than two unique capacities, or
        include non-positive values when ``require_positive`` is ``True``.
    """

    capacity_values = np.asarray(
        capacity,
        dtype=float,
    )

    cost_values = np.asarray(
        cost,
        dtype=float,
    )

    if capacity_values.shape != cost_values.shape:
        raise ValueError(
            "Capacity and cost arrays must have the same shape."
        )

    if capacity_values.size < 2:
        raise ValueError(
            f"At least two observations are required to fit a {model_name} "
            "model."
        )

    if not np.isfinite(capacity_values).all():
        raise ValueError(
            "Capacity values contain non-finite values."
        )

    if not np.isfinite(cost_values).all():
        raise ValueError(
            "Cost values contain non-finite values."
        )

    if np.unique(capacity_values).size < 2:
        raise ValueError(
            f"{model_name.capitalize()} regression requires at least two "
            "unique capacities."
        )

    if require_positive:
        if np.any(capacity_values <= 0):
            raise ValueError(
                "Power regression requires strictly positive capacity values."
            )

        if np.any(cost_values <= 0):
            raise ValueError(
                "Power regression requires strictly positive cost values."
            )

    return capacity_values, cost_values


def fit_linear_cost_model(
    capacity: np.ndarray,
    cost: np.ndarray,
) -> FittedCostModel:
    """Fit a linear cost model to capacity and cost observations.

    The model is estimated as ``y = slope * x + intercept`` using ordinary
    least-squares polynomial fitting. Predictions are evaluated against the
    observed costs in the original cost units.

    Parameters
    ----------
    capacity : np.ndarray
        Pipeline-capacity observations used as the independent variable.
    cost : np.ndarray
        Cost observations used as the dependent variable.

    Returns
    -------
    FittedCostModel
        Linear-model coefficients, equation metadata, and regression diagnostics.
        The power-model ``coefficient`` and ``exponent`` fields are returned as
        ``NaN``.

    Raises
    ------
    ValueError
        If the capacity and cost arrays fail regression-input validation.
    """

    capacity_values, cost_values = validate_regression_arrays(
        capacity=capacity,
        cost=cost,
        model_name="linear",
    )

    slope, intercept = np.polyfit(
        capacity_values,
        cost_values,
        deg=1,
    )

    predicted = (
        slope * capacity_values
        + intercept
    )

    metrics = calculate_regression_metrics(
        observed=cost_values,
        predicted=predicted,
    )

    return {
        "model_type": "linear",
        "equation": "y = slope * x + intercept",
        "slope": float(slope),
        "intercept": float(intercept),
        "coefficient": float("nan"),
        "exponent": float("nan"),
        **metrics,
    }


def fit_power_cost_model(
    capacity: np.ndarray,
    cost: np.ndarray,
) -> FittedCostModel:
    """Fit a power cost model to capacity and cost observations.

    The model is estimated as ``y = coefficient * x ** exponent`` by applying a
    linear regression to the natural logarithms of capacity and cost. Predictions
    are then transformed back to the original cost scale before regression
    diagnostics are calculated.

    Parameters
    ----------
    capacity : np.ndarray
        Strictly positive pipeline-capacity observations used as the independent
        variable.
    cost : np.ndarray
        Strictly positive cost observations used as the dependent variable.

    Returns
    -------
    FittedCostModel
        Power-model coefficients, equation metadata, and regression diagnostics.
        The linear-model ``slope`` and ``intercept`` fields are returned as ``NaN``.

    Raises
    ------
    ValueError
        If the capacity and cost arrays fail regression-input validation, including
        the strict-positivity requirement for logarithmic transformation.
    """

    capacity_values, cost_values = validate_regression_arrays(
        capacity=capacity,
        cost=cost,
        model_name="power",
        require_positive=True,
    )

    log_capacity = np.log(capacity_values)
    log_cost = np.log(cost_values)

    exponent, log_coefficient = np.polyfit(
        log_capacity,
        log_cost,
        deg=1,
    )

    coefficient = float(
        np.exp(log_coefficient)
    )

    predicted = (
        coefficient
        * capacity_values ** exponent
    )

    metrics = calculate_regression_metrics(
        observed=cost_values,
        predicted=predicted,
    )

    return {
        "model_type": "power",
        "equation": "y = coefficient * x ** exponent",
        "slope": float("nan"),
        "intercept": float("nan"),
        "coefficient": coefficient,
        "exponent": float(exponent),
        **metrics,
    }


# =============================================================================
# Model fitting and selection
# =============================================================================

def fit_pipeline_cost_models(
    cost_table: pd.DataFrame,
    capacity_column: str,
    cost_columns: dict[str, str],
    metadata_columns: list[str],
) -> pd.DataFrame:
    """Fit candidate linear and power models to each pipeline cost component.

    The shared capacity column is converted to a floating-point array, while
    constant metadata values are taken from the first input row. For every
    configured cost component, both linear and power regressions are fitted and
    their coefficients, valid capacity range, observation count, source-column
    metadata, and diagnostics are assembled into a candidate-model table.

    Parameters
    ----------
    cost_table : pd.DataFrame
        Validated pipeline capacity-cost observations containing the capacity,
        cost, and metadata columns.
    capacity_column : str
        Name of the pipeline-capacity column used as the independent variable.
    cost_columns : dict[str, str]
        Mapping from canonical cost-component names to source cost-column names.
    metadata_columns : list[str]
        Metadata columns whose shared values are copied into each fitted-model row.

    Returns
    -------
    pd.DataFrame
        Candidate regression table containing one linear and one power model for
        each configured cost component, sorted by cost type and model type.

    Raises
    ------
    ValueError
        If a candidate model cannot be fitted or if the resulting table does not
        contain exactly two fitted models for each configured cost component.
    """

    capacity = (
        pd.to_numeric(
            cost_table[capacity_column],
            errors="raise",
        )
        .to_numpy(dtype=float)
    )

    metadata = {
        column: cost_table[column].iloc[0]
        for column in metadata_columns
    }

    regression_rows: list[dict[str, object]] = []

    for cost_type, cost_column in cost_columns.items():
        cost = (
            pd.to_numeric(
                cost_table[cost_column],
                errors="raise",
            )
            .to_numpy(dtype=float)
        )

        fitted_models = [
            fit_linear_cost_model(
                capacity=capacity,
                cost=cost,
            ),
            fit_power_cost_model(
                capacity=capacity,
                cost=cost,
            ),
        ]

        for fitted_model in fitted_models:
            regression_rows.append(
                {
                    **metadata,
                    "cost_type": cost_type,
                    "capacity_column": capacity_column,
                    "cost_column": cost_column,
                    "model_type": fitted_model["model_type"],
                    "equation": fitted_model["equation"],
                    "slope": fitted_model["slope"],
                    "intercept": fitted_model["intercept"],
                    "coefficient": fitted_model["coefficient"],
                    "exponent": fitted_model["exponent"],
                    "capacity_min": float(capacity.min()),
                    "capacity_max": float(capacity.max()),
                    "n_observations": int(capacity.size),
                    "r_squared": fitted_model["r_squared"],
                    "rmse": fitted_model["rmse"],
                    "mae": fitted_model["mae"],
                    "maximum_absolute_error": (
                        fitted_model["maximum_absolute_error"]
                    ),
                }
            )

    regression_table = (
        pd.DataFrame(regression_rows)
        .sort_values(
            [
                "cost_type",
                "model_type",
            ]
        )
        .reset_index(drop=True)
    )

    expected_rows = len(cost_columns) * 2

    if len(regression_table) != expected_rows:
        raise ValueError(
            "Candidate regression table has an unexpected row count: "
            f"expected {expected_rows}, found {len(regression_table)}."
        )

    return regression_table


def select_pipeline_cost_models(
    regression_table: pd.DataFrame,
    selected_model_types: dict[str, str],
) -> pd.DataFrame:
    """Select one fitted model for each pipeline cost component.

    For each configured cost component, the candidate regression table is filtered
    to the requested model type. Exactly one matching row must exist for every
    selection. The selected rows are combined, sorted by cost type, and marked with
    an ``is_selected`` flag.

    Parameters
    ----------
    regression_table : pd.DataFrame
        Candidate-model table containing fitted linear and power regressions for
        each pipeline cost component.
    selected_model_types : dict[str, str]
        Mapping from cost-component names to the model type selected for downstream
        use.

    Returns
    -------
    pd.DataFrame
        Table containing one selected fitted model per configured cost component,
        with an ``is_selected`` column set to ``True``.

    Raises
    ------
    ValueError
        If a configured cost component and model-type combination does not match
        exactly one row in ``regression_table``.
    """

    selected_rows: list[pd.DataFrame] = []

    for cost_type, model_type in selected_model_types.items():
        matching_rows = regression_table.loc[
            (
                regression_table["cost_type"]
                == cost_type
            )
            & (
                regression_table["model_type"]
                == model_type
            )
        ].copy()

        if len(matching_rows) != 1:
            raise ValueError(
                "Expected exactly one fitted model for "
                f"cost_type='{cost_type}' and "
                f"model_type='{model_type}', but found "
                f"{len(matching_rows)}."
            )

        selected_rows.append(matching_rows)

    selected_models = (
        pd.concat(
            selected_rows,
            ignore_index=True,
        )
        .sort_values("cost_type")
        .reset_index(drop=True)
    )

    model_type_position = list(
        selected_models.columns
    ).index("model_type")

    selected_models.insert(
        model_type_position + 1,
        "is_selected",
        True,
    )

    return selected_models



# =============================================================================
# Topology-free schema templates
# =============================================================================

def extract_capex_power_model(
    selected_models: pd.DataFrame,
) -> pd.Series:
    """Extract and validate the selected H2 pipeline CAPEX power model.

    The selected-model table is filtered to the CAPEX row, which must contain
    exactly one power-model definition. The required coefficient, exponent, and
    capacity-range parameters are converted to finite floating-point values and
    checked for valid bounds.

    Parameters
    ----------
    selected_models : pd.DataFrame
        Table containing one selected fitted model for each pipeline cost
        component.

    Returns
    -------
    pd.Series
        Validated CAPEX power-model row containing numeric coefficient, exponent,
        minimum capacity, and maximum capacity values.

    Raises
    ------
    ValueError
        If the table does not contain exactly one CAPEX model, the selected CAPEX
        model is not a power function, a required parameter is missing or non-finite,
        the coefficient or exponent is non-positive, or the capacity bounds are
        invalid.
    """

    capex_models = (
        selected_models.loc[
            selected_models["cost_type"] == "capex"
        ]
        .copy()
        .reset_index(drop=True)
    )

    if len(capex_models) != 1:
        raise ValueError(
            "Selected models must contain exactly one CAPEX model."
        )

    capex_model = capex_models.iloc[0].copy()

    if capex_model["model_type"] != "power":
        raise ValueError(
            "Selected CAPEX model must use a power regression, "
            f"but found '{capex_model['model_type']}'."
        )

    for column in [
        "coefficient",
        "exponent",
        "capacity_min",
        "capacity_max",
    ]:
        value = pd.to_numeric(
            capex_model[column],
            errors="coerce",
        )

        if pd.isna(value) or not np.isfinite(float(value)):
            raise ValueError(
                f"CAPEX model parameter '{column}' is invalid."
            )

        capex_model[column] = float(value)

    if capex_model["coefficient"] <= 0:
        raise ValueError(
            "CAPEX power-model coefficient must be positive."
        )

    if capex_model["exponent"] <= 0:
        raise ValueError(
            "CAPEX power-model exponent must be positive."
        )

    if capex_model["capacity_min"] <= 0:
        raise ValueError(
            "CAPEX model capacity_min must be positive."
        )

    if capex_model["capacity_max"] <= capex_model["capacity_min"]:
        raise ValueError(
            "CAPEX model capacity_max must be greater than capacity_min."
        )

    return capex_model


def build_h2_etlsegment_template(
    selected_models: pd.DataFrame,
    segment_count: int = DEFAULT_ETL_SEGMENT_COUNT,
    spacing: str = DEFAULT_ETL_SPACING,
    data_id: str = DEFAULT_DATA_ID,
) -> pd.DataFrame:
    """Build a topology-free H2 pipeline ETLSegment CAPEX template.

    The selected CAPEX power model is converted into contiguous piecewise-linear
    capacity-cost intervals suitable for downstream TEMOA schema construction. The
    first interval begins at zero capacity and zero CAPEX so candidate corridors
    can remain unbuilt. The remaining breakpoints span the fitted model's observed
    capacity range using logarithmic or linear spacing.

    Parameters
    ----------
    selected_models : pd.DataFrame
        Table containing the selected fitted pipeline cost models, including one
        validated CAPEX power model.
    segment_count : int, optional
        Total number of ETL intervals to construct. The first interval spans zero
        to the minimum observed capacity. Defaults to
        ``DEFAULT_ETL_SEGMENT_COUNT``.
    spacing : str, optional
        Spacing method for positive-capacity breakpoints. Must be ``"log"`` or
        ``"linear"``. Defaults to ``DEFAULT_ETL_SPACING``.
    data_id : str, optional
        Data-quality identifier assigned to every generated ETLSegment row.
        Defaults to ``DEFAULT_DATA_ID``.

    Returns
    -------
    pd.DataFrame
        Topology-free ETLSegment template containing contiguous capacity and CAPEX
        bounds per kilometre for the selected H2 pipeline technology.

    Raises
    ------
    ValueError
        If ``segment_count`` is less than one, ``spacing`` is unsupported,
        ``data_id`` is blank, the selected CAPEX model is invalid, any segment has
        non-increasing bounds, adjacent segments are not contiguous, the first
        segment does not begin at zero, or the final segment does not end at the
        fitted model's maximum capacity.
    """

    if segment_count < 1:
        raise ValueError(
            "ETL segment_count must be at least 1."
        )

    if spacing not in {"log", "linear"}:
        raise ValueError(
            "ETL spacing must be either 'log' or 'linear'."
        )

    if not data_id.strip():
        raise ValueError("data_id must not be blank.")

    capex_model = extract_capex_power_model(
        selected_models=selected_models,
    )

    capacity_min = float(capex_model["capacity_min"])
    capacity_max = float(capex_model["capacity_max"])
    coefficient = float(capex_model["coefficient"])
    exponent = float(capex_model["exponent"])

    # TEMOA's ETL formulation requires one segment to be selected for every
    # region-technology pair represented by ETLSegment. The curve must therefore
    # include a valid zero-build point; otherwise the first positive cap_lower
    # would impose a minimum H2 pipeline build on every candidate corridor.
    #
    # Keep ``segment_count`` as the total number of intervals. The first interval
    # spans zero to the smallest observed capacity, while the remaining intervals
    # divide the observed capacity range from capacity_min to capacity_max.
    positive_breakpoint_count = segment_count

    if spacing == "log":
        positive_breakpoints = np.geomspace(
            capacity_min,
            capacity_max,
            positive_breakpoint_count,
        )
    else:
        positive_breakpoints = np.linspace(
            capacity_min,
            capacity_max,
            positive_breakpoint_count,
        )

    capacity_breakpoints = np.concatenate(
        (
            np.array([0.0], dtype=float),
            positive_breakpoints,
        )
    )

    cap_lower = capacity_breakpoints[:-1]
    cap_upper = capacity_breakpoints[1:]

    cost_breakpoints_per_km = np.empty_like(
        capacity_breakpoints,
        dtype=float,
    )
    cost_breakpoints_per_km[0] = 0.0
    cost_breakpoints_per_km[1:] = (
        coefficient
        * positive_breakpoints ** exponent
    )

    cost_lower_per_km = cost_breakpoints_per_km[:-1]
    cost_upper_per_km = cost_breakpoints_per_km[1:]

    etl_template = pd.DataFrame(
        {
            "tech_or_group": str(capex_model["technology"]),
            "segment": np.arange(segment_count, dtype=int),
            "cap_lower": cap_lower,
            "cap_upper": cap_upper,
            "cost_lower_per_km": cost_lower_per_km,
            "cost_upper_per_km": cost_upper_per_km,
            "data_id": data_id,
        }
    )

    if not (
        etl_template["cap_upper"]
        > etl_template["cap_lower"]
    ).all():
        raise ValueError(
            "Every ETL segment must have cap_upper greater than cap_lower."
        )

    if not (
        etl_template["cost_upper_per_km"]
        > etl_template["cost_lower_per_km"]
    ).all():
        raise ValueError(
            "Every ETL segment must have cost_upper_per_km greater than "
            "cost_lower_per_km."
        )

    if len(etl_template) > 1:
        if not np.allclose(
            etl_template["cap_upper"].iloc[:-1],
            etl_template["cap_lower"].iloc[1:],
        ):
            raise ValueError(
                "ETL capacity segments are not contiguous."
            )

        if not np.allclose(
            etl_template["cost_upper_per_km"].iloc[:-1],
            etl_template["cost_lower_per_km"].iloc[1:],
        ):
            raise ValueError(
                "ETL CAPEX bounds are not contiguous."
            )

    if not np.isclose(
        etl_template["cap_lower"].iloc[0],
        0.0,
    ):
        raise ValueError(
            "First ETL segment must begin at zero capacity."
        )

    if not np.isclose(
        etl_template["cost_lower_per_km"].iloc[0],
        0.0,
    ):
        raise ValueError(
            "First ETL segment must begin at zero CAPEX."
        )

    if not np.isclose(
        etl_template["cap_upper"].iloc[0],
        capacity_min,
    ):
        raise ValueError(
            "First ETL segment must end at model capacity_min."
        )

    if not np.isclose(
        etl_template["cap_upper"].iloc[-1],
        capacity_max,
    ):
        raise ValueError(
            "Final ETL segment does not end at model capacity_max."
        )

    return etl_template[ETLSEGMENT_TEMPLATE_COLUMNS].copy()


def build_h2_pipeline_opex_coefficient_template(
    selected_models: pd.DataFrame,
    data_id: str = DEFAULT_DATA_ID,
) -> pd.DataFrame:
    """Build topology-free fixed- and variable-OPEX coefficient templates for H2 pipelines.

    The selected model table is filtered to the fixed- and variable-OPEX rows,
    which must each contain one linear regression for the same technology and
    commodity. Their slopes and intercepts are validated, renamed to
    schema-oriented coefficient fields, annotated with units and provenance, and
    returned in the canonical downstream column order.

    Parameters
    ----------
    selected_models : pd.DataFrame
        Table containing the selected fitted pipeline cost models.
    data_id : str, optional
        Data-quality identifier assigned to each generated OPEX coefficient row.
        Defaults to ``DEFAULT_DATA_ID``.

    Returns
    -------
    pd.DataFrame
        Two-row topology-free OPEX coefficient template containing one fixed-OPEX
        row and one variable-OPEX row, sorted by cost type.

    Raises
    ------
    ValueError
        If required columns are missing, ``data_id`` is blank, the expected OPEX
        cost types are missing or duplicated, either selected OPEX model is not
        linear, the rows refer to different technologies or commodities, or the
        fitted slope or intercept values are invalid or non-finite.
    """

    required_columns = [
        "technology",
        "commodity",
        "cost_type",
        "model_type",
        "slope",
        "intercept",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in selected_models.columns
    ]

    if missing_columns:
        raise ValueError(
            "Selected cost models are missing required columns: "
            f"{missing_columns}"
        )

    if not data_id.strip():
        raise ValueError("data_id must not be blank.")

    opex_models = selected_models.loc[
        selected_models["cost_type"].isin(
            ["fixed_opex", "variable_opex"]
        )
    ].copy()

    expected_cost_types = {
        "fixed_opex",
        "variable_opex",
    }

    actual_cost_types = set(
        opex_models["cost_type"].astype(str)
    )

    if actual_cost_types != expected_cost_types:
        raise ValueError(
            "Expected exactly fixed_opex and variable_opex models, "
            f"but found {sorted(actual_cost_types)}."
        )

    if len(opex_models) != 2:
        raise ValueError(
            "Expected exactly one fixed_opex row and one "
            "variable_opex row."
        )

    if opex_models["cost_type"].duplicated().any():
        raise ValueError(
            "Selected pipeline OPEX models contain duplicate cost_type rows."
        )

    if not (opex_models["model_type"] == "linear").all():
        raise ValueError(
            "H2 pipeline fixed and variable OPEX models must be linear."
        )

    if opex_models["technology"].nunique() != 1:
        raise ValueError(
            "Selected pipeline OPEX models must refer to one technology."
        )

    if opex_models["commodity"].nunique() != 1:
        raise ValueError(
            "Selected pipeline OPEX models must refer to one commodity."
        )

    for column in ["slope", "intercept"]:
        opex_models[column] = pd.to_numeric(
            opex_models[column],
            errors="coerce",
        )

        if opex_models[column].isna().any():
            raise ValueError(
                f"Pipeline OPEX column '{column}' contains invalid values."
            )

        if not np.isfinite(
            opex_models[column].to_numpy(dtype=float)
        ).all():
            raise ValueError(
                f"Pipeline OPEX column '{column}' contains non-finite values."
            )

    opex_template = opex_models.rename(
        columns={
            "slope": "coefficient_per_km",
            "intercept": "intercept_cost",
        }
    )

    opex_template["units"] = opex_template["cost_type"].map(
        {
            "fixed_opex": "CAD2020/(t H2 capacity/year)/km",
            "variable_opex": "CAD2020/t H2/km",
        }
    )

    opex_template["notes"] = opex_template["cost_type"].map(
        {
            "fixed_opex": (
                "Selected linear fixed operating-cost model for H2 pipeline "
                "transport. build_schema.py applies edge distance and maps "
                "the result to CostFixed."
            ),
            "variable_opex": (
                "Selected linear variable operating-cost model for H2 "
                "pipeline transport. build_schema.py applies edge distance "
                "and maps the result to CostVariable."
            ),
        }
    )

    opex_template["data_source"] = (
        "Transition Accelerator H2 pipeline cost model"
    )

    for column in [
        "dq_cred",
        "dq_geog",
        "dq_struc",
        "dq_tech",
        "dq_time",
    ]:
        opex_template[column] = None

    opex_template["data_id"] = data_id

    return (
        opex_template[OPEX_COEFFICIENT_COLUMNS]
        .sort_values("cost_type")
        .reset_index(drop=True)
    )


# =============================================================================
# Export
# =============================================================================

def export_selected_cost_models(
    selected_models: pd.DataFrame,
    output_path: Path,
) -> Path:
    """Validate and export the selected pipeline cost models to CSV.

    The selected-model table is checked for the complete expected set of pipeline
    cost components, unique cost-type rows, and all required export columns. The
    validated rows are reordered into the canonical output schema, sorted by cost
    type, written as a UTF-8 CSV, and confirmed to exist on disk.

    Parameters
    ----------
    selected_models : pd.DataFrame
        Table containing one selected fitted model for each required pipeline cost
        component.
    output_path : Path
        Destination path for the selected pipeline cost-model CSV.

    Returns
    -------
    Path
        Path to the successfully created CSV file.

    Raises
    ------
    ValueError
        If the selected-model table is empty, contains missing, unexpected, or
        duplicate cost types, or lacks required export columns.
    OSError
        If the CSV file is not present after the export operation.
    """

    required_cost_types = set(SELECTED_MODEL_TYPES)

    if selected_models.empty:
        raise ValueError(
            "Selected pipeline cost-model table is empty."
        )

    selected_cost_types = set(
        selected_models["cost_type"].astype(str).tolist()
    )

    missing_cost_types = (
        required_cost_types
        - selected_cost_types
    )

    unexpected_cost_types = (
        selected_cost_types
        - required_cost_types
    )

    if missing_cost_types:
        raise ValueError(
            "Selected pipeline cost-model table is missing cost types: "
            f"{sorted(missing_cost_types)}"
        )

    if unexpected_cost_types:
        raise ValueError(
            "Selected pipeline cost-model table contains unexpected "
            f"cost types: {sorted(unexpected_cost_types)}"
        )

    duplicated_cost_types = (
        selected_models["cost_type"]
        .duplicated(keep=False)
    )

    if duplicated_cost_types.any():
        duplicates = (
            selected_models.loc[
                duplicated_cost_types,
                "cost_type",
            ]
            .astype(str)
            .tolist()
        )

        raise ValueError(
            "Selected pipeline cost-model table contains duplicate "
            f"cost types: {duplicates}"
        )

    missing_export_columns = [
        column
        for column in SELECTED_MODEL_OUTPUT_COLUMNS
        if column not in selected_models.columns
    ]

    if missing_export_columns:
        raise ValueError(
            "Selected pipeline cost-model table is missing export "
            f"columns: {missing_export_columns}"
        )

    export_table = (
        selected_models[SELECTED_MODEL_OUTPUT_COLUMNS]
        .sort_values("cost_type")
        .reset_index(drop=True)
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    export_table.to_csv(
        output_path,
        index=False,
        encoding="utf-8",
    )

    if not output_path.exists():
        raise OSError(
            "Selected pipeline cost-model CSV was not created: "
            f"{output_path}"
        )

    return output_path



def export_h2_pipeline_cost_templates(
    etlsegment_template: pd.DataFrame,
    opex_template: pd.DataFrame,
    etlsegment_path: Path,
    opex_path: Path,
) -> tuple[Path, Path]:
    """Validate and export topology-free H2 pipeline cost templates.

    The ETLSegment and OPEX templates are checked for required columns, non-empty
    contents, unique identifying rows, and valid increasing capacity and CAPEX
    bounds. Each table is reordered into its canonical schema, sorted
    deterministically, written as a UTF-8 CSV, and confirmed to exist on disk.

    Parameters
    ----------
    etlsegment_template : pd.DataFrame
        Topology-free H2 pipeline ETLSegment template containing capacity and CAPEX
        bounds per kilometre.
    opex_template : pd.DataFrame
        Topology-free H2 pipeline fixed- and variable-OPEX coefficient template.
    etlsegment_path : Path
        Destination path for the ETLSegment template CSV.
    opex_path : Path
        Destination path for the OPEX coefficient CSV.

    Returns
    -------
    tuple[Path, Path]
        Paths to the successfully created ETLSegment and OPEX CSV files.

    Raises
    ------
    ValueError
        If either template is missing required columns, is empty, contains duplicate
        identifying rows, or includes invalid capacity or CAPEX bounds.
    OSError
        If either output file is not present after the export operation.
    """

    missing_etl_columns = [
        column
        for column in ETLSEGMENT_TEMPLATE_COLUMNS
        if column not in etlsegment_template.columns
    ]

    missing_opex_columns = [
        column
        for column in OPEX_COEFFICIENT_COLUMNS
        if column not in opex_template.columns
    ]

    if missing_etl_columns:
        raise ValueError(
            "H2 pipeline ETLSegment template is missing required "
            f"columns: {missing_etl_columns}"
        )

    if missing_opex_columns:
        raise ValueError(
            "H2 pipeline OPEX template is missing required "
            f"columns: {missing_opex_columns}"
        )

    etl_export = (
        etlsegment_template[ETLSEGMENT_TEMPLATE_COLUMNS]
        .copy()
        .sort_values(["tech_or_group", "segment"])
        .reset_index(drop=True)
    )

    opex_export = (
        opex_template[OPEX_COEFFICIENT_COLUMNS]
        .copy()
        .sort_values(["technology", "cost_type"])
        .reset_index(drop=True)
    )

    if etl_export.empty:
        raise ValueError(
            "H2 pipeline ETLSegment template is empty."
        )

    if opex_export.empty:
        raise ValueError(
            "H2 pipeline OPEX coefficient template is empty."
        )

    if etl_export[
        ["tech_or_group", "segment"]
    ].duplicated().any():
        raise ValueError(
            "H2 pipeline ETLSegment export contains duplicate "
            "technology-segment rows."
        )

    if opex_export["cost_type"].duplicated().any():
        raise ValueError(
            "H2 pipeline OPEX export contains duplicate cost_type rows."
        )

    if not (
        etl_export["cap_upper"]
        > etl_export["cap_lower"]
    ).all():
        raise ValueError(
            "ETLSegment export contains invalid capacity bounds."
        )

    if not (
        etl_export["cost_upper_per_km"]
        > etl_export["cost_lower_per_km"]
    ).all():
        raise ValueError(
            "ETLSegment export contains invalid CAPEX bounds."
        )

    etlsegment_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    opex_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    etl_export.to_csv(
        etlsegment_path,
        index=False,
        encoding="utf-8",
    )
    opex_export.to_csv(
        opex_path,
        index=False,
        encoding="utf-8",
    )

    if not etlsegment_path.exists():
        raise OSError(
            "H2 pipeline ETLSegment template was not created: "
            f"{etlsegment_path}"
        )

    if not opex_path.exists():
        raise OSError(
            "H2 pipeline OPEX coefficient file was not created: "
            f"{opex_path}"
        )

    return etlsegment_path, opex_path


# =============================================================================
# Orchestration
# =============================================================================

def build_h2_pipeline_cost_models(
    input_path: Path,
    model_selection_path: Path,
    etlsegment_template_path: Path,
    opex_coefficient_path: Path,
    selected_model_types: dict[str, str] | None = None,
    segment_count: int = DEFAULT_ETL_SEGMENT_COUNT,
    spacing: str = DEFAULT_ETL_SPACING,
    data_id: str = DEFAULT_DATA_ID,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    """Fit H2 pipeline cost models and export topology-free schema templates.

    The workflow loads and validates the normalized capacity-cost dataset, fits
    candidate linear and power regressions for each cost component, selects the
    configured model form for each component, constructs topology-free CAPEX and
    OPEX templates, and exports all selected model and template products.

    Parameters
    ----------
    input_path : Path
        Path to the normalized H2 pipeline capacity-cost CSV.
    model_selection_path : Path
        Destination path for the selected cost-model definition CSV.
    etlsegment_template_path : Path
        Destination path for the topology-free ETLSegment CAPEX template CSV.
    opex_coefficient_path : Path
        Destination path for the topology-free fixed- and variable-OPEX
        coefficient CSV.
    selected_model_types : dict[str, str] | None, optional
        Mapping from cost-component names to selected regression forms. When
        omitted, ``SELECTED_MODEL_TYPES`` is used.
    segment_count : int, optional
        Total number of ETLSegment intervals used to approximate the selected CAPEX
        power model. Defaults to ``DEFAULT_ETL_SEGMENT_COUNT``.
    spacing : str, optional
        Positive-capacity breakpoint spacing method, either ``"log"`` or
        ``"linear"``. Defaults to ``DEFAULT_ETL_SPACING``.
    data_id : str, optional
        Data-quality identifier assigned to generated schema-template rows.
        Defaults to ``DEFAULT_DATA_ID``.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]
        Candidate regression results, selected cost models, topology-free
        ETLSegment CAPEX template, and topology-free OPEX coefficient template.

    Raises
    ------
    FileNotFoundError
        If the normalized capacity-cost input file does not exist.
    ValueError
        If the input data, regression configuration, selected models, or generated
        templates fail validation.
    OSError
        If any required output CSV is not created successfully.
    """

    model_selection = (
        SELECTED_MODEL_TYPES
        if selected_model_types is None
        else selected_model_types
    )

    capacity_costs = load_capacity_cost_table(
        input_path=input_path,
    )

    validate_capacity_cost_table(
        cost_table=capacity_costs,
        required_columns=REQUIRED_COLUMNS,
        metadata_columns=METADATA_COLUMNS,
        capacity_column=CAPACITY_COLUMN,
        cost_columns=COST_COLUMNS,
    )

    regressions = fit_pipeline_cost_models(
        cost_table=capacity_costs,
        capacity_column=CAPACITY_COLUMN,
        cost_columns=COST_COLUMNS,
        metadata_columns=METADATA_COLUMNS,
    )

    selected_models = select_pipeline_cost_models(
        regression_table=regressions,
        selected_model_types=model_selection,
    )

    etlsegment_template = build_h2_etlsegment_template(
        selected_models=selected_models,
        segment_count=segment_count,
        spacing=spacing,
        data_id=data_id,
    )

    opex_template = build_h2_pipeline_opex_coefficient_template(
        selected_models=selected_models,
        data_id=data_id,
    )

    export_selected_cost_models(
        selected_models=selected_models,
        output_path=model_selection_path,
    )

    export_h2_pipeline_cost_templates(
        etlsegment_template=etlsegment_template,
        opex_template=opex_template,
        etlsegment_path=etlsegment_template_path,
        opex_path=opex_coefficient_path,
    )

    return (
        regressions,
        selected_models,
        etlsegment_template,
        opex_template,
    )


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the H2 pipeline cost-model workflow.

    The parser accepts optional overrides for the normalized input dataset and each
    export destination, together with settings controlling the ETLSegment
    piecewise approximation and dataset identifier.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments containing input and output paths,
        ``segment_count``, breakpoint ``spacing``, and ``data_id``.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Fit topology-independent H2 pipeline cost functions and build "
            "schema-facing ETLSegment and OPEX templates."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional normalized H2 pipeline capacity-cost CSV path.",
    )

    parser.add_argument(
        "--model-selection-output",
        type=Path,
        default=None,
        help="Optional selected cost-model CSV path.",
    )

    parser.add_argument(
        "--etlsegment-output",
        type=Path,
        default=None,
        help="Optional topology-free ETLSegment template CSV path.",
    )

    parser.add_argument(
        "--opex-output",
        type=Path,
        default=None,
        help="Optional topology-free OPEX coefficient CSV path.",
    )

    parser.add_argument(
        "--segment-count",
        type=int,
        default=DEFAULT_ETL_SEGMENT_COUNT,
        help=(
            "Number of CAPEX piecewise intervals. "
            f"Default: {DEFAULT_ETL_SEGMENT_COUNT}"
        ),
    )

    parser.add_argument(
        "--spacing",
        choices=["log", "linear"],
        default=DEFAULT_ETL_SPACING,
        help=(
            "Capacity-breakpoint spacing method. "
            f"Default: {DEFAULT_ETL_SPACING}"
        ),
    )

    parser.add_argument(
        "--data-id",
        default=DEFAULT_DATA_ID,
        help=f"Dataset identifier. Default: {DEFAULT_DATA_ID}",
    )

    return parser.parse_args()


def main() -> None:
    """Run the H2 pipeline cost-model fitting and template-export workflow.

    Command-line arguments are parsed, the project root and input/output paths are
    resolved, and the configured workflow is executed. The resulting regression
    tables and topology-free templates are summarized in the console together with
    the validated input range and final export locations.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If the project root or normalized capacity-cost input file cannot be found.
    ValueError
        If the input data, fitted models, selected regressions, or generated
        templates fail validation.
    OSError
        If an expected output CSV is not created successfully.
    """

    args = parse_args()
    project_root = find_project_root()

    input_path = (
        args.input.resolve()
        if args.input is not None
        else default_input_path(project_root)
    )

    model_selection_path = (
        args.model_selection_output.resolve()
        if args.model_selection_output is not None
        else default_model_selection_path(project_root)
    )

    etlsegment_path = (
        args.etlsegment_output.resolve()
        if args.etlsegment_output is not None
        else default_etlsegment_template_path(project_root)
    )

    opex_path = (
        args.opex_output.resolve()
        if args.opex_output is not None
        else default_opex_coefficient_path(project_root)
    )

    print("\n" + "=" * 78)
    print("Build H2 pipeline cost models and topology-free templates")
    print("=" * 78)
    print(f"Project root:      {project_root}")
    print(f"Input CSV:         {input_path}")
    print(f"Model selection:   {model_selection_path}")
    print(f"ETLSegment output: {etlsegment_path}")
    print(f"OPEX output:       {opex_path}")
    print(f"ETL segments:      {args.segment_count}")
    print(f"ETL spacing:       {args.spacing}")
    print(f"Data ID:           {args.data_id}")

    (
        regressions,
        selected_models,
        etlsegment_template,
        opex_template,
    ) = build_h2_pipeline_cost_models(
        input_path=input_path,
        model_selection_path=model_selection_path,
        etlsegment_template_path=etlsegment_path,
        opex_coefficient_path=opex_path,
        segment_count=args.segment_count,
        spacing=args.spacing,
        data_id=args.data_id,
    )

    input_table = load_capacity_cost_table(
        input_path=input_path,
    )

    print("\nInput capacity-cost table validated.")
    print(f"  Rows:        {len(input_table):,}")
    print(f"  Technology:  {input_table['technology'].iloc[0]}")
    print(f"  Commodity:   {input_table['commodity'].iloc[0]}")
    print(
        "  Capacity:    "
        f"{input_table[CAPACITY_COLUMN].min():,.0f} to "
        f"{input_table[CAPACITY_COLUMN].max():,.0f} t H2/year"
    )

    print("\nCandidate cost models fitted.")
    print(f"  Models:      {len(regressions):,}")

    for row in regressions.itertuples(index=False):
        print(
            f"  {row.cost_type:<14} {row.model_type:<7} "
            f"R²={row.r_squared:.6f}  "
            f"RMSE={row.rmse:,.6f}"
        )

    print("\nSelected cost models:")
    for row in selected_models.itertuples(index=False):
        print(f"  {row.cost_type:<14} -> {row.model_type}")

    print("\nTopology-free templates built.")
    print(f"  ETLSegment rows: {len(etlsegment_template):,}")
    print(f"  OPEX rows:       {len(opex_template):,}")
    print(
        "  Capacity range:  "
        f"{etlsegment_template['cap_lower'].min():,.0f} to "
        f"{etlsegment_template['cap_upper'].max():,.0f} t H2/year"
    )

    print("\nBuild complete.")
    print(f"  Model selection: {model_selection_path}")
    print(f"  ETLSegment:      {etlsegment_path}")
    print(f"  OPEX:            {opex_path}")


if __name__ == "__main__":
    main()
