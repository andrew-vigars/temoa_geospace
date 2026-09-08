"""
build_h2_pipeline_costs.py

Build the normalized hydrogen-pipeline capacity-cost dataset used by the
Geospatial-CANOE cost workflow.

This script reads the controlled H2 pipeline master cost workbook, validates and
standardizes the normalized CAPEX, fixed-OPEX, and variable-OPEX worksheets,
merges them into one canonical engineering dataset, adds model metadata, and
exports the processed CSV used by downstream cost-model scripts.

Inputs
------
data_files/models/cost_models/transport/master_files/
    h2_pipeline_costs_master_v2.xlsx

Required worksheets
-------------------
- Normalized CAPEX
- Normalized Variable OPEX
- Normalized Fixed OPEX

Output
------
data_files/processed/costs/transport/pipelines/h2_pipeline/
    h2_pipeline_normalized_capacity_costs.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from geocanoe.paths import find_project_root

# =============================================================================
# Canonical configuration
# =============================================================================

NORMALIZED_COST_SHEETS = {
    "capex": "Normalized CAPEX",
    "variable_opex": "Normalized Variable OPEX",
    "fixed_opex": "Normalized Fixed OPEX",
}

COMMON_COLUMN_MAP = {
    "Pipeline sizes (inch, inner diameter)": "diameter_in",
    "Annual Capacity (t H2 / year)": "capacity_t_h2_per_year",
}

COST_COLUMN_MAPS = {
    "capex": {
        "Total CAPEX ($ 2020 CAD / km)": (
            "total_capex_cad2020_per_km"
        ),
    },
    "variable_opex": {
        "Total Variable OPEX ($ 2020 CAD/ km)": (
            "total_variable_opex_cad2020_per_year_per_km"
        ),
    },
    "fixed_opex": {
        "Total Fixed OPEX ($ 2020 CAD per year / km* per year capacity)": (
            "total_fixed_opex_cad2020_per_year_per_km"
        ),
    },
}

KEY_COLUMNS = [
    "diameter_in",
    "capacity_t_h2_per_year",
]

OUTPUT_COLUMNS = [
    "technology",
    "commodity",
    "diameter_in",
    "capacity_t_h2_per_year",
    "total_capex_cad2020_per_km",
    "total_variable_opex_cad2020_per_year_per_km",
    "total_fixed_opex_cad2020_per_year_per_km",
    "currency",
    "currency_year",
    "source_workbook",
    "cost_model_version",
]

DEFAULT_TECHNOLOGY = "H2_PIPE"
DEFAULT_COMMODITY = "h2"
DEFAULT_CURRENCY = "CAD"
DEFAULT_CURRENCY_YEAR = 2020
DEFAULT_COST_MODEL_VERSION = "v1"


# =============================================================================
# Project discovery and paths
# =============================================================================

def default_workbook_path(project_root: Path) -> Path:
    """Construct the canonical H2 pipeline master-workbook path.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the controlled H2 pipeline master cost workbook under
        ``data_files/models/cost_models/transport/master_files/``.
    """

    return (
        project_root
        / "data_files"
        / "models"
        / "cost_models"
        / "transport"
        / "master_files"
        / "h2_pipeline_costs_master_v2.xlsx"
    )


def default_output_path(project_root: Path) -> Path:
    """Construct the canonical processed H2 pipeline capacity-cost CSV path.

    Parameters
    ----------
    project_root : Path
        Root directory of the Geospatial-CANOE repository.

    Returns
    -------
    Path
        Path to the normalized H2 pipeline capacity-cost CSV under
        ``data_files/processed/costs/transport/pipelines/h2_pipeline/``.
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


# =============================================================================
# Workbook loading
# =============================================================================

def list_workbook_sheets(workbook_path: Path) -> list[str]:
    """Return the worksheet names defined in an Excel workbook.

    The workbook is validated before being opened. Worksheet identifiers are then
    read through ``pandas.ExcelFile`` and checked to ensure every identifier is a
    string before the names are returned in workbook order.

    Parameters
    ----------
    workbook_path : Path
        Path to the Excel workbook to inspect.

    Returns
    -------
    list[str]
        Worksheet names in the order they appear in the workbook.

    Raises
    ------
    FileNotFoundError
        If ``workbook_path`` does not exist.
    ValueError
        If the workbook contains a worksheet identifier that is not a string.
    """

    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    with pd.ExcelFile(workbook_path) as workbook:
        sheet_names = list(workbook.sheet_names)

    non_string_sheets = [
        sheet_name
        for sheet_name in sheet_names
        if not isinstance(sheet_name, str)
    ]

    if non_string_sheets:
        raise ValueError(
            "Workbook contains worksheet identifiers that are not strings: "
            f"{non_string_sheets}"
        )

    return [
        sheet_name
        for sheet_name in sheet_names
        if isinstance(sheet_name, str)
    ]


def validate_required_sheets(
    available_sheets: list[str],
    sheet_map: dict[str, str],
) -> None:
    """Validate that all configured normalized cost worksheets are available.

    Each worksheet name referenced by ``sheet_map`` is checked against the workbook
    worksheet names in ``available_sheets``. Validation succeeds silently when all
    required worksheets are present.

    Parameters
    ----------
    available_sheets : list[str]
        Worksheet names available in the source workbook.
    sheet_map : dict[str, str]
        Mapping from canonical cost-component labels to their required worksheet
        names.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If one or more configured worksheet names are absent from
        ``available_sheets``.
    """

    missing_sheets = [
        sheet_name
        for sheet_name in sheet_map.values()
        if sheet_name not in available_sheets
    ]

    if missing_sheets:
        raise ValueError(
            "Missing required normalized cost worksheet(s): "
            f"{missing_sheets}. Available worksheets: {available_sheets}"
        )


def load_normalized_cost_sheets(
    workbook_path: Path,
    sheet_map: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """Load and lightly clean the configured normalized cost worksheets.

    The source workbook is opened once through ``pandas.ExcelFile``. Each worksheet
    referenced by ``sheet_map`` is validated, loaded into a DataFrame, stripped of
    fully empty rows and columns, and reindexed. The cleaned tables are returned
    using their canonical cost-component labels as dictionary keys.

    Parameters
    ----------
    workbook_path : Path
        Path to the H2 pipeline master cost workbook.
    sheet_map : dict[str, str]
        Mapping from canonical cost-component labels, such as ``"capex"``, to the
        corresponding workbook worksheet names.

    Returns
    -------
    dict[str, pd.DataFrame]
        Cleaned worksheet tables keyed by canonical cost-component label.

    Raises
    ------
    FileNotFoundError
        If ``workbook_path`` does not exist.
    ValueError
        If ``sheet_map`` is empty, a required worksheet is missing, or a loaded
        worksheet contains no usable data after empty rows and columns are removed.
    """

    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Pipeline cost workbook not found: {workbook_path}"
        )

    if not sheet_map:
        raise ValueError("No normalized cost worksheets were specified.")

    cost_tables: dict[str, pd.DataFrame] = {}

    with pd.ExcelFile(workbook_path) as workbook:
        workbook_sheet_names = list(workbook.sheet_names)

        non_string_sheets = [
            sheet_name
            for sheet_name in workbook_sheet_names
            if not isinstance(sheet_name, str)
        ]

        if non_string_sheets:
            raise ValueError(
                "Workbook contains worksheet identifiers that are not strings: "
                f"{non_string_sheets}"
            )

        available_sheets = [
            sheet_name
            for sheet_name in workbook_sheet_names
            if isinstance(sheet_name, str)
        ]

        validate_required_sheets(
            available_sheets=available_sheets,
            sheet_map=sheet_map,
        )

        for cost_type, sheet_name in sheet_map.items():
            cost_table = pd.read_excel(
                workbook,
                sheet_name=sheet_name,
            )

            cost_table = (
                cost_table
                .dropna(axis="index", how="all")
                .dropna(axis="columns", how="all")
                .reset_index(drop=True)
            )

            if cost_table.empty:
                raise ValueError(
                    f"Worksheet '{sheet_name}' contains no usable data."
                )

            cost_tables[cost_type] = cost_table

    return cost_tables


# =============================================================================
# Standardization and validation
# =============================================================================

def standardize_cost_table_columns(
    cost_tables: dict[str, pd.DataFrame],
    common_column_map: dict[str, str],
    cost_column_maps: dict[str, dict[str, str]],
) -> dict[str, pd.DataFrame]:
    """Standardize column names across normalized pipeline cost tables.

    For each cost component, this function combines the shared engineering-column
    mapping with the corresponding cost-specific mapping, validates that all source
    columns are present, and selects and renames only those required columns. The
    standardized tables retain the same dictionary keys as the input tables.

    Parameters
    ----------
    cost_tables : dict[str, pd.DataFrame]
        Normalized cost tables keyed by canonical cost-component label, such as
        ``"capex"``, ``"fixed_opex"``, or ``"variable_opex"``.
    common_column_map : dict[str, str]
        Mapping from source column names shared by all cost tables to their
        canonical column names.
    cost_column_maps : dict[str, dict[str, str]]
        Cost-component-specific mappings from source cost-column names to canonical
        cost-column names.

    Returns
    -------
    dict[str, pd.DataFrame]
        Standardized cost tables keyed by their original cost-component labels.

    Raises
    ------
    ValueError
        If a cost component has no corresponding cost-column mapping, a required
        source column is missing, or a combined mapping produces duplicate
        standardized column names.
    """

    standardized_tables: dict[str, pd.DataFrame] = {}

    for cost_type, cost_table in cost_tables.items():
        if cost_type not in cost_column_maps:
            raise ValueError(
                f"No cost-column mapping defined for '{cost_type}'."
            )

        column_map = {
            **common_column_map,
            **cost_column_maps[cost_type],
        }

        missing_columns = [
            source_column
            for source_column in column_map
            if source_column not in cost_table.columns
        ]

        if missing_columns:
            raise ValueError(
                f"{cost_type} table is missing required columns: "
                f"{missing_columns}"
            )

        standardized_columns = list(column_map.values())

        if len(standardized_columns) != len(set(standardized_columns)):
            raise ValueError(
                f"{cost_type} column mapping produces duplicate "
                "standardized column names."
            )

        standardized_table = (
            cost_table
            .loc[:, list(column_map)]
            .rename(columns=column_map)
            .copy()
        )

        standardized_tables[cost_type] = standardized_table

    return standardized_tables


def coerce_and_validate_numeric_columns(
    cost_tables: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Coerce standardized engineering and cost columns to positive numeric values.

    Each column in every cost table is converted with ``pandas.to_numeric``. Values
    that cannot be converted are treated as missing and rejected. The function also
    requires every numeric value to be strictly greater than zero before returning
    validated copies of the input tables.

    Parameters
    ----------
    cost_tables : dict[str, pd.DataFrame]
        Standardized cost tables keyed by canonical cost-component label.

    Returns
    -------
    dict[str, pd.DataFrame]
        Validated numeric cost tables keyed by their original cost-component labels.

    Raises
    ------
    ValueError
        If any column contains missing or non-numeric values after coercion, or if
        any value is zero or negative.
    """

    validated_tables: dict[str, pd.DataFrame] = {}

    for cost_type, cost_table in cost_tables.items():
        validated = cost_table.copy()

        for column in validated.columns:
            validated[column] = pd.to_numeric(
                validated[column],
                errors="coerce",
            )

            if validated[column].isna().any():
                invalid_count = int(validated[column].isna().sum())
                raise ValueError(
                    f"{cost_type} column '{column}' contains "
                    f"{invalid_count} missing or non-numeric value(s)."
                )

            if (validated[column] <= 0).any():
                raise ValueError(
                    f"{cost_type} column '{column}' must contain only "
                    "strictly positive values."
                )

        validated_tables[cost_type] = validated

    return validated_tables


def validate_matching_cost_table_keys(
    cost_tables: dict[str, pd.DataFrame],
    reference_cost_type: str,
    key_columns: list[str],
) -> None:
    """Validate that all cost tables describe the same engineering cases.

    The table identified by ``reference_cost_type`` defines the expected set of
    engineering cases. Each table is checked for duplicate key combinations and
    then compared against the reference using ``key_columns``. When a mismatch is
    found, the error reports cases missing from the comparison table and cases that
    appear only in that table.

    Parameters
    ----------
    cost_tables : dict[str, pd.DataFrame]
        Cost tables keyed by canonical cost-component label.
    reference_cost_type : str
        Cost-component label identifying the table used as the reference case set.
    key_columns : list[str]
        Column names that jointly identify one engineering case, such as pipeline
        diameter and annual capacity.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the reference table is absent, any table contains duplicate engineering
        cases based on ``key_columns``, or a table's key set differs from the
        reference key set.
    """

    if reference_cost_type not in cost_tables:
        raise ValueError(
            f"Reference cost table '{reference_cost_type}' was not found."
        )

    reference_table = cost_tables[reference_cost_type]

    duplicated_reference = reference_table.duplicated(
        subset=key_columns,
        keep=False,
    )

    if duplicated_reference.any():
        raise ValueError(
            f"Reference table '{reference_cost_type}' contains duplicate "
            f"engineering cases based on {key_columns}."
        )

    reference_keys = (
        reference_table[key_columns]
        .sort_values(key_columns)
        .reset_index(drop=True)
    )

    for cost_type, cost_table in cost_tables.items():
        duplicated_cases = cost_table.duplicated(
            subset=key_columns,
            keep=False,
        )

        if duplicated_cases.any():
            raise ValueError(
                f"Table '{cost_type}' contains duplicate engineering cases "
                f"based on {key_columns}."
            )

        if cost_type == reference_cost_type:
            continue

        comparison_keys = (
            cost_table[key_columns]
            .sort_values(key_columns)
            .reset_index(drop=True)
        )

        if reference_keys.equals(comparison_keys):
            continue

        missing_from_comparison = (
            reference_keys
            .merge(
                comparison_keys,
                on=key_columns,
                how="left",
                indicator=True,
            )
            .query("_merge == 'left_only'")
            .drop(columns="_merge")
        )

        additional_in_comparison = (
            comparison_keys
            .merge(
                reference_keys,
                on=key_columns,
                how="left",
                indicator=True,
            )
            .query("_merge == 'left_only'")
            .drop(columns="_merge")
        )

        raise ValueError(
            f"Engineering cases in '{cost_type}' do not match "
            f"'{reference_cost_type}'.\n"
            f"Missing from '{cost_type}':\n"
            f"{missing_from_comparison.to_string(index=False)}\n"
            f"Additional in '{cost_type}':\n"
            f"{additional_in_comparison.to_string(index=False)}"
        )


# =============================================================================
# Transformation
# =============================================================================

def merge_normalized_cost_tables(
    cost_tables: dict[str, pd.DataFrame],
    key_columns: list[str],
) -> pd.DataFrame:
    """Merge normalized pipeline cost components into one canonical table.

    The CAPEX table provides the initial engineering cases and key columns.
    Variable- and fixed-OPEX columns are then joined sequentially using validated
    one-to-one inner merges. The completed table is sorted by annual hydrogen
    capacity and returned with a clean integer index.

    Parameters
    ----------
    cost_tables : dict[str, pd.DataFrame]
        Standardized and validated cost tables keyed by canonical cost-component
        labels. The mapping must include ``"capex"``, ``"variable_opex"``, and
        ``"fixed_opex"``.
    key_columns : list[str]
        Column names that jointly identify one engineering case and are shared
        across all cost tables.

    Returns
    -------
    pd.DataFrame
        Canonical capacity-cost table containing the engineering keys and all CAPEX,
        variable-OPEX, and fixed-OPEX columns, sorted by
        ``capacity_t_h2_per_year``.

    Raises
    ------
    ValueError
        If one or more required cost-component tables are absent.
    pandas.errors.MergeError
        If a merge violates the required one-to-one relationship between
        engineering cases.
    KeyError
        If a required key or sorting column is absent.
    """

    required_cost_types = [
        "capex",
        "variable_opex",
        "fixed_opex",
    ]

    missing_cost_types = [
        cost_type
        for cost_type in required_cost_types
        if cost_type not in cost_tables
    ]

    if missing_cost_types:
        raise ValueError(
            f"Missing required cost tables: {missing_cost_types}"
        )

    combined_costs = cost_tables["capex"].copy()

    for cost_type in ["variable_opex", "fixed_opex"]:
        cost_columns = [
            column
            for column in cost_tables[cost_type].columns
            if column not in key_columns
        ]

        combined_costs = combined_costs.merge(
            cost_tables[cost_type][key_columns + cost_columns],
            on=key_columns,
            how="inner",
            validate="one_to_one",
        )

    return (
        combined_costs
        .sort_values("capacity_t_h2_per_year")
        .reset_index(drop=True)
    )


def add_cost_model_metadata(
    cost_table: pd.DataFrame,
    technology: str,
    commodity: str,
    currency: str,
    currency_year: int,
    source_workbook: str,
    cost_model_version: str,
) -> pd.DataFrame:
    """Add provenance and model-identification metadata to a cost table.

    The input table is copied before modification. Technology and commodity labels
    are inserted as the leading columns, while currency, source-workbook, and
    cost-model version fields are appended. The completed table is validated
    against ``OUTPUT_COLUMNS`` and returned in that canonical column order.

    Parameters
    ----------
    cost_table : pd.DataFrame
        Merged pipeline capacity-cost table containing the standardized engineering
        and cost columns.
    technology : str
        Canonical technology identifier associated with the cost model.
    commodity : str
        Canonical commodity identifier transported by the technology.
    currency : str
        Currency code used for all monetary values.
    currency_year : int
        Reference year of the monetary values.
    source_workbook : str
        Name or identifier of the workbook from which the cost data were derived.
    cost_model_version : str
        Version identifier for the processed cost model.

    Returns
    -------
    pd.DataFrame
        Metadata-enriched pipeline capacity-cost table restricted to the canonical
        ``OUTPUT_COLUMNS`` order.

    Raises
    ------
    ValueError
        If one or more columns required by ``OUTPUT_COLUMNS`` are absent after the
        metadata fields are added.
    """

    output_table = cost_table.copy()

    output_table.insert(0, "technology", technology)
    output_table.insert(1, "commodity", commodity)

    output_table["currency"] = currency
    output_table["currency_year"] = int(currency_year)
    output_table["source_workbook"] = source_workbook
    output_table["cost_model_version"] = cost_model_version

    missing_output_columns = [
        column
        for column in OUTPUT_COLUMNS
        if column not in output_table.columns
    ]

    if missing_output_columns:
        raise ValueError(
            "Final pipeline capacity-cost table is missing columns: "
            f"{missing_output_columns}"
        )

    return output_table[OUTPUT_COLUMNS].copy()


# =============================================================================
# Export
# =============================================================================

def export_cost_table(
    cost_table: pd.DataFrame,
    output_path: Path,
) -> Path:
    """Validate and export a processed pipeline capacity-cost table to CSV.

    The table is checked for non-empty content, duplicate column names, and exact
    agreement with the canonical ``OUTPUT_COLUMNS`` schema before export. The
    destination directory is created when necessary, the table is written as a
    UTF-8 CSV without an index, and the resulting file is verified to exist.

    Parameters
    ----------
    cost_table : pd.DataFrame
        Processed pipeline capacity-cost table to export.
    output_path : Path
        Destination path for the output CSV file.

    Returns
    -------
    Path
        Path to the successfully created CSV file.

    Raises
    ------
    ValueError
        If ``cost_table`` is empty, contains duplicate column names, or does not
        match the canonical output-column order.
    OSError
        If the CSV file does not exist after the export operation completes.
    """

    if cost_table.empty:
        raise ValueError("Cannot export an empty pipeline cost table.")

    duplicated_columns = (
        cost_table.columns[
            cost_table.columns.duplicated()
        ]
        .tolist()
    )

    if duplicated_columns:
        raise ValueError(
            "Pipeline cost table contains duplicate column names: "
            f"{duplicated_columns}"
        )

    if list(cost_table.columns) != OUTPUT_COLUMNS:
        raise ValueError(
            "Pipeline cost-table columns do not match the canonical output "
            f"schema. Expected {OUTPUT_COLUMNS}, found "
            f"{cost_table.columns.tolist()}."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    cost_table.to_csv(
        output_path,
        index=False,
        encoding="utf-8",
    )

    if not output_path.exists():
        raise OSError(
            f"Pipeline cost table was not created: {output_path}"
        )

    return output_path


# =============================================================================
# Orchestration
# =============================================================================

def build_h2_pipeline_capacity_costs(
    workbook_path: Path,
    output_path: Path,
    technology: str = DEFAULT_TECHNOLOGY,
    commodity: str = DEFAULT_COMMODITY,
    currency: str = DEFAULT_CURRENCY,
    currency_year: int = DEFAULT_CURRENCY_YEAR,
    cost_model_version: str = DEFAULT_COST_MODEL_VERSION,
) -> pd.DataFrame:
    """Build, validate, and export the normalized H2 pipeline cost dataset.

    This orchestration function runs the complete preprocessing workflow for the
    controlled H2 pipeline cost workbook. It verifies the required worksheets,
    loads and standardizes the CAPEX and OPEX tables, validates their numeric values
    and engineering-case keys, merges the cost components, adds model provenance,
    and exports the canonical processed CSV.

    Parameters
    ----------
    workbook_path : Path
        Path to the controlled H2 pipeline master cost workbook.
    output_path : Path
        Destination path for the processed capacity-cost CSV.
    technology : str, optional
        Canonical pipeline technology identifier added to each output row.
    commodity : str, optional
        Canonical transported-commodity identifier added to each output row.
    currency : str, optional
        Currency code associated with the monetary values.
    currency_year : int, optional
        Reference year associated with the monetary values.
    cost_model_version : str, optional
        Version identifier assigned to the processed cost dataset.

    Returns
    -------
    pd.DataFrame
        Canonical H2 pipeline capacity-cost table containing standardized
        engineering cases, CAPEX, fixed OPEX, variable OPEX, and provenance
        metadata.

    Raises
    ------
    FileNotFoundError
        If the source workbook does not exist.
    ValueError
        If required worksheets or columns are missing, worksheet data are empty,
        numeric values are invalid, engineering cases are duplicated or
        inconsistent across cost components, or the final table does not match the
        canonical output schema.
    pandas.errors.MergeError
        If the normalized cost tables cannot be merged using a one-to-one
        engineering-case relationship.
    OSError
        If the processed CSV is not created successfully.
    """

    available_sheets = list_workbook_sheets(workbook_path)

    validate_required_sheets(
        available_sheets=available_sheets,
        sheet_map=NORMALIZED_COST_SHEETS,
    )

    normalized_cost_tables = load_normalized_cost_sheets(
        workbook_path=workbook_path,
        sheet_map=NORMALIZED_COST_SHEETS,
    )

    standardized_cost_tables = standardize_cost_table_columns(
        cost_tables=normalized_cost_tables,
        common_column_map=COMMON_COLUMN_MAP,
        cost_column_maps=COST_COLUMN_MAPS,
    )

    standardized_cost_tables = coerce_and_validate_numeric_columns(
        cost_tables=standardized_cost_tables,
    )

    validate_matching_cost_table_keys(
        cost_tables=standardized_cost_tables,
        reference_cost_type="capex",
        key_columns=KEY_COLUMNS,
    )

    combined_costs = merge_normalized_cost_tables(
        cost_tables=standardized_cost_tables,
        key_columns=KEY_COLUMNS,
    )

    pipeline_cost_table = add_cost_model_metadata(
        cost_table=combined_costs,
        technology=technology,
        commodity=commodity,
        currency=currency,
        currency_year=currency_year,
        source_workbook=workbook_path.name,
        cost_model_version=cost_model_version,
    )

    export_cost_table(
        cost_table=pipeline_cost_table,
        output_path=output_path,
    )

    return pipeline_cost_table


def run_h2_pipeline_cost_build(
    workbook_path: Path | None = None,
    output_path: Path | None = None,
    technology: str = DEFAULT_TECHNOLOGY,
    commodity: str = DEFAULT_COMMODITY,
    currency: str = DEFAULT_CURRENCY,
    currency_year: int = DEFAULT_CURRENCY_YEAR,
    cost_model_version: str = DEFAULT_COST_MODEL_VERSION,
) -> pd.DataFrame:
    """Run the normalized H2 pipeline capacity-cost build.

    The workflow resolves the Geospatial-CANOE repository root, selects either
    user-supplied or canonical workbook and output paths, reports the active
    configuration, inspects the available workbook worksheets, and executes the
    complete normalized H2 pipeline cost-processing workflow.

    Data loading, validation, column standardization, engineering-case matching,
    cost-component merging, metadata assignment, and CSV export are delegated to
    ``build_h2_pipeline_capacity_costs``. The completed canonical cost table is
    returned after export.

    Parameters
    ----------
    workbook_path : Path | None, optional
        Path to the controlled H2 pipeline master workbook. When omitted, the
        canonical project workbook path is resolved automatically.
    output_path : Path | None, optional
        Destination path for the processed H2 pipeline capacity-cost CSV. When
        omitted, the canonical processed cost-layer path is used.
    technology : str, optional
        Canonical pipeline technology identifier assigned to each output row.
    commodity : str, optional
        Canonical transported-commodity identifier assigned to each output row.
    currency : str, optional
        Currency code associated with the monetary values.
    currency_year : int, optional
        Reference year associated with the monetary values.
    cost_model_version : str, optional
        Version identifier assigned to the processed cost dataset.

    Returns
    -------
    pd.DataFrame
        Canonical H2 pipeline capacity-cost table containing standardized
        engineering cases, CAPEX, fixed OPEX, variable OPEX, and provenance
        metadata.

    Raises
    ------
    FileNotFoundError
        If the Geospatial-CANOE repository root or source workbook cannot be
        located.
    ValueError
        If required worksheets or columns are missing, worksheet data are empty,
        numeric values are invalid, engineering cases are duplicated or
        inconsistent across cost components, or the final table does not match
        the canonical output schema.
    pandas.errors.MergeError
        If the normalized cost tables violate the required one-to-one merge
        relationship.
    OSError
        If the processed output CSV cannot be created or inspected after export.
    """

    project_root = find_project_root()

    workbook_path = (
        workbook_path.resolve()
        if workbook_path is not None
        else default_workbook_path(project_root)
    )

    output_path = (
        output_path.resolve()
        if output_path is not None
        else default_output_path(project_root)
    )

    print("\n" + "=" * 78)
    print("Build H2 pipeline normalized capacity-cost dataset")
    print("=" * 78)
    print(f"Project root: {project_root}")
    print(f"Workbook:     {workbook_path}")
    print(f"Output CSV:   {output_path}")

    available_sheets = list_workbook_sheets(workbook_path)

    print("\nWorkbook worksheets:")
    for index, sheet_name in enumerate(available_sheets):
        print(f"  [{index}] {sheet_name}")

    print("\nSelected normalized cost worksheets:")
    for cost_type, sheet_name in NORMALIZED_COST_SHEETS.items():
        print(f"  {cost_type}: {sheet_name}")

    pipeline_cost_table = build_h2_pipeline_capacity_costs(
        workbook_path=workbook_path,
        output_path=output_path,
        technology=technology,
        commodity=commodity,
        currency=currency,
        currency_year=currency_year,
        cost_model_version=cost_model_version,
    )

    print("\nBuild complete.")
    print(f"  Rows:       {len(pipeline_cost_table):,}")
    print(f"  Columns:    {len(pipeline_cost_table.columns):,}")
    print(f"  Output:     {output_path}")
    print(f"  Size:       {output_path.stat().st_size:,} bytes")
    print(
        "  Capacity:   "
        f"{pipeline_cost_table['capacity_t_h2_per_year'].min():,.0f} to "
        f"{pipeline_cost_table['capacity_t_h2_per_year'].max():,.0f} "
        "t H2/year"
    )

    return pipeline_cost_table


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line options for the H2 pipeline cost-build workflow.

    The command-line interface allows callers to override the canonical source
    workbook and output CSV paths and to customize the technology, commodity,
    currency, currency year, and processed cost-model version metadata assigned to
    the exported dataset.

    Returns
    -------
    argparse.Namespace
        Parsed command-line arguments with the following attributes:

        ``workbook``
            Optional path to the H2 pipeline master workbook.
        ``output``
            Optional destination path for the processed CSV.
        ``technology``
            Technology identifier written to the output table.
        ``commodity``
            Commodity identifier written to the output table.
        ``currency``
            Currency code associated with the cost values.
        ``currency_year``
            Reference year associated with the currency values.
        ``cost_model_version``
            Version identifier assigned to the processed cost dataset.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Build the normalized H2 pipeline capacity-cost dataset from the "
            "controlled master Excel workbook."
        )
    )

    parser.add_argument(
        "--workbook",
        type=Path,
        default=None,
        help=(
            "Optional path to the H2 pipeline master workbook. "
            "Defaults to the canonical project location."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Optional output CSV path. Defaults to the canonical processed "
            "cost-layer location."
        ),
    )

    parser.add_argument(
        "--technology",
        default=DEFAULT_TECHNOLOGY,
        help=f"Technology identifier. Default: {DEFAULT_TECHNOLOGY}",
    )

    parser.add_argument(
        "--commodity",
        default=DEFAULT_COMMODITY,
        help=f"Commodity identifier. Default: {DEFAULT_COMMODITY}",
    )

    parser.add_argument(
        "--currency",
        default=DEFAULT_CURRENCY,
        help=f"Currency code. Default: {DEFAULT_CURRENCY}",
    )

    parser.add_argument(
        "--currency-year",
        type=int,
        default=DEFAULT_CURRENCY_YEAR,
        help=f"Currency base year. Default: {DEFAULT_CURRENCY_YEAR}",
    )

    parser.add_argument(
        "--cost-model-version",
        default=DEFAULT_COST_MODEL_VERSION,
        help=(
            "Processed cost-model version identifier. "
            f"Default: {DEFAULT_COST_MODEL_VERSION}"
        ),
    )

    return parser.parse_args()


def main() -> None:
    """Parse CLI arguments and run the H2 pipeline capacity-cost build."""

    args = parse_args()

    run_h2_pipeline_cost_build(
        workbook_path=args.workbook,
        output_path=args.output,
        technology=args.technology,
        commodity=args.commodity,
        currency=args.currency,
        currency_year=args.currency_year,
        cost_model_version=args.cost_model_version,
    )


if __name__ == "__main__":
    main()
