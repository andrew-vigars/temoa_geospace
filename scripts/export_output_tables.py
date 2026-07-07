# =============================================================================
# export_output_tables.py
#
# Export solved CANOE/TEMOA Output* tables from a selected SQLite database to a
# single Excel workbook for quick viewing.
#
# This script supports two workflows:
#   1. CLI mode with numbered selection from output_files/
#   2. Programmatic import from main_run.py after a successful model run
# =============================================================================

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter


# =============================================================================
# Project paths
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "output_files"


# =============================================================================
# Default output table names
# =============================================================================

DEFAULT_OUTPUT_TABLES = [
    "OutputBuiltCapacity",
    "OutputCost",
    "OutputCurtailment",
    "OutputDualVariable",
    "OutputEmission",
    "OutputFlowIn",
    "OutputFlowOut",
    "OutputNetCapacity",
    "OutputObjective",
    "OutputRetiredCapacity",
    "OutputStorageLevel",
    "OutputFlowOutSummary",
]

DEFAULT_WORKBOOK_NAME = "output_tables.xlsx"
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLUMNS = 16_384


# =============================================================================
# Interactive run selection helpers
# =============================================================================

def find_output_runs(output_root: Path = OUTPUT_ROOT) -> list[Path]:
    """Find timestamped output folders containing SQLite run databases."""

    if not output_root.exists():
        raise FileNotFoundError(f"Output root not found: {output_root}")

    runs = sorted(
        [
            path
            for path in output_root.iterdir()
            if path.is_dir() and any(path.glob("*.sqlite"))
        ]
    )

    if not runs:
        raise FileNotFoundError(
            f"No output runs with SQLite files found in {output_root}"
        )

    return runs


def select_from_list(options: list[Path], label: str) -> Path:
    """Prompt the user to select one path from a numbered list."""

    if not options:
        raise FileNotFoundError(f"No {label} options found.")

    print(f"\nAvailable {label}:")
    for index, path in enumerate(options):
        print(f"  [{index}] {path.name}")

    while True:
        choice = input(f"\nSelect {label} index: ").strip()

        try:
            index = int(choice)
        except ValueError:
            print("Please enter an integer.")
            continue

        if 0 <= index < len(options):
            return options[index]

        print("Invalid selection.")


def find_sqlite_files(run_dir: Path) -> list[Path]:
    """Find SQLite files in a selected output run folder."""

    sqlite_files = sorted(run_dir.glob("*.sqlite"))

    if not sqlite_files:
        raise FileNotFoundError(f"No SQLite files found in {run_dir}")

    return sqlite_files


def select_run_database(output_root: Path = OUTPUT_ROOT) -> tuple[Path, Path]:
    """Select an output run and SQLite database using numbered CLI prompts."""

    runs = find_output_runs(output_root)
    selected_run = select_from_list(runs, "model runs")

    sqlite_files = find_sqlite_files(selected_run)

    solved_files = [
        path
        for path in sqlite_files
        if path.name.startswith("solved_")
    ]

    if len(solved_files) == 1:
        selected_db = solved_files[0]
        print(f"\nSelected solved database: {selected_db.name}")
    else:
        selected_db = select_from_list(sqlite_files, "SQLite databases")

    return selected_run, selected_db


# =============================================================================
# SQLite export helpers
# =============================================================================

def list_existing_output_tables(db_path: Path) -> list[str]:
    """Return Output* tables present in a SQLite database."""

    query = """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name LIKE 'Output%'
        ORDER BY name
    """

    with sqlite3.connect(db_path) as connection:
        tables = pd.read_sql_query(query, connection)["name"].tolist()

    return tables


def make_excel_sheet_name(table_name: str, used_sheet_names: set[str]) -> str:
    """Return a valid unique Excel sheet name for a SQLite table name."""

    sheet_name = re.sub(r"[\\/*?:\[\]]", "_", table_name).strip() or "Sheet"
    sheet_name = sheet_name[:31]

    if sheet_name not in used_sheet_names:
        used_sheet_names.add(sheet_name)
        return sheet_name

    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = f"{sheet_name[:31 - len(suffix)]}{suffix}"

        if candidate not in used_sheet_names:
            used_sheet_names.add(candidate)
            return candidate

        counter += 1


def validate_excel_sheet_size(table: str, df: pd.DataFrame) -> None:
    """Raise a clear error if a table is too large for one Excel sheet."""

    # Excel's row limit includes the header row written by pandas.
    if len(df) + 1 > EXCEL_MAX_ROWS:
        raise ValueError(
            f"{table} has {len(df):,} rows. This exceeds Excel's worksheet "
            f"limit of {EXCEL_MAX_ROWS:,} rows including the header. "
            "Use CSV for this table or filter/split it before export."
        )

    if len(df.columns) > EXCEL_MAX_COLUMNS:
        raise ValueError(
            f"{table} has {len(df.columns):,} columns. This exceeds Excel's "
            f"worksheet limit of {EXCEL_MAX_COLUMNS:,} columns."
        )


def format_excel_sheet(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    """Apply light formatting so exported sheets are easier to inspect."""

    worksheet = writer.sheets[sheet_name]
    worksheet.freeze_panes = "A2"

    if len(df.columns) > 0:
        worksheet.auto_filter.ref = worksheet.dimensions

    for column_index, column_name in enumerate(df.columns, start=1):
        values = df[column_name].dropna().astype(str)
        max_value_width = values.str.len().max() if not values.empty else 0
        width = max(len(str(column_name)), int(max_value_width)) + 2
        width = min(max(width, 10), 40)

        column_letter = get_column_letter(column_index)
        worksheet.column_dimensions[column_letter].width = width


def export_output_tables(
    db_path: Path,
    output_dir: Path,
    tables: list[str] | None = None,
    skip_empty: bool = True,
) -> list[Path]:
    """Export selected Output* tables from a solved SQLite database to Excel.

    When ``tables`` is omitted, all existing ``Output*`` tables in the database
    are exported. Empty tables are skipped by default.

    This function keeps the original public API used by ``main_run.py``:
    ``output_dir`` is still a directory and the function still returns a list of
    paths. The list now contains one Excel workbook path instead of many CSV
    paths.
    """

    db_path = Path(db_path)
    output_dir = Path(output_dir)

    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / DEFAULT_WORKBOOK_NAME

    existing_tables = list_existing_output_tables(db_path)

    if not existing_tables:
        raise ValueError(f"No Output* tables found in database: {db_path}")

    selected_tables = tables if tables is not None else existing_tables

    exported_paths: list[Path] = []
    used_sheet_names: set[str] = set()
    written_tables = 0

    with sqlite3.connect(db_path) as connection:
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            for table in selected_tables:
                if table not in existing_tables:
                    print(f"[Skip] Table not found: {table}")
                    continue

                df = pd.read_sql_query(f'SELECT * FROM "{table}"', connection)

                if df.empty and skip_empty:
                    print(f"[Skip] Empty table: {table}")
                    continue

                validate_excel_sheet_size(table, df)

                sheet_name = make_excel_sheet_name(table, used_sheet_names)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                format_excel_sheet(writer, sheet_name, df)
                written_tables += 1

                print(
                    f"[Exported] {table}: {len(df):,} row(s) "
                    f"→ {output_path.name} [{sheet_name}]"
                )

    if written_tables == 0:
        raise ValueError("No Output* tables were exported. All selected tables were empty or missing.")

    exported_paths.append(output_path)
    return exported_paths


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse CLI options for exporting solved Output* tables to Excel."""

    parser = argparse.ArgumentParser(
        description="Export solved CANOE/TEMOA Output* SQLite tables to Excel."
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT,
        help="Root folder containing timestamped model-run output folders.",
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            "Optional direct path to a solved SQLite database. "
            "If omitted, choose a run interactively."
        ),
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Optional Excel output directory. "
            "Defaults to <selected_run>/excel_outputs in interactive mode, "
            "or <db_parent>/excel_outputs when --db is provided."
        ),
    )

    parser.add_argument(
        "--tables",
        nargs="*",
        default=None,
        help=(
            "Optional list of Output* tables to export. "
            "Defaults to all existing Output* tables."
        ),
    )

    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Write empty Output* tables instead of skipping them.",
    )

    return parser.parse_args()


def main() -> None:
    """Run the Output* table Excel export command-line workflow."""

    args = parse_args()

    if args.db is not None:
        db_path = args.db
        output_dir = args.out or db_path.parent / "excel_outputs"
    else:
        selected_run, db_path = select_run_database(args.output_root)
        output_dir = args.out or selected_run / "excel_outputs"

    print("\nExcel export configuration")
    print("--------------------------")
    print(f"Database: {db_path}")
    print(f"Output:   {output_dir}")

    exported_paths = export_output_tables(
        db_path=db_path,
        output_dir=output_dir,
        tables=args.tables,
        skip_empty=not args.include_empty,
    )

    print("\nExcel export complete.")
    print(f"Exported {len(exported_paths):,} workbook(s) to: {output_dir}")


if __name__ == "__main__":
    main()
