"""SQLite import, export, and configuration helpers for CANOE databases."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import pandas as pd


SheetSelection = Sequence[str] | Literal["all"]


def convert_sql_to_sqlite(
    sql_file_path: str | Path,
    sqlite_file_path: str | Path,
) -> None:
    """Convert a SQL script containing schema and data to a SQLite database.

    Parameters
    ----------
    sql_file_path : str | Path
        Path to the source ``.sql`` script.
    sqlite_file_path : str | Path
        Path to the destination SQLite database. SQLite creates the file when
        it does not already exist.

    Returns
    -------
    None
    """

    # Read SQL file
    with open(sql_file_path, "r", encoding="utf-8") as f:
        sql_script = f.read()

    # Connect to SQLite database (creates if not exists)
    conn = sqlite3.connect(sqlite_file_path)
    try:
        conn.executescript(sql_script)
    finally:
        conn.close()


def update_database_from_excel(
    excel_path: str | Path,
    db_path: str | Path,
) -> None:
    """Append worksheet rows from an Excel workbook to matching SQLite tables.

    The ``instructions`` and ``sheet1`` worksheets are ignored. A failure in
    one worksheet is reported to standard output without preventing subsequent
    worksheets from being processed.

    Parameters
    ----------
    excel_path : str | Path
        Source Excel workbook.
    db_path : str | Path
        SQLite database to create or update.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If ``excel_path`` does not exist.
    """

    # Check if the Excel file exists
    if not Path(excel_path).exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    # Ensure database directory exists
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Load the Excel file and get all sheet names
    xls = pd.ExcelFile(excel_path, engine="openpyxl")
    sheet_names = [
        sheet
        for sheet in xls.sheet_names
        if sheet.lower() not in ["instructions", "sheet1"]
    ]
    print("Filtered sheet names:", sheet_names)

    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)

    # Iterate over the filtered sheet names
    for sheet in sheet_names:
        # print(f"Processing sheet: {sheet}")
        try:
            # Load each sheet into a DataFrame
            df = pd.read_excel(xls, sheet_name=sheet, engine="openpyxl")
            # print(df.head())  # Print a preview of the data

            # Replace the corresponding table in the SQLite database
            df.to_sql(sheet, conn, if_exists="append", index=False)
            print(f"Table '{sheet}' has been appended in the database.")
        except Exception as e:
            print(f"Error processing sheet '{sheet}': {e}")

    # Close the database connection
    conn.close()
    print("Database update complete.")


def update_db_paths(
    config_path: str | Path, new_file: str, create_backup: bool = True
) -> None:
    """Update the input and output database paths in a TOML configuration.

    Existing formatting and inline comments are preserved. When neither key is
    present, both are appended to the file.

    Parameters
    ----------
    config_path : str | Path
        TOML configuration file to update.
    new_file : str
        Database path to assign to ``input_database`` and ``output_database``.
    create_backup : bool
        Create a sibling file with a ``.bak`` suffix before writing changes.

    Returns
    -------
    None
    """
    p = Path(config_path)
    text = p.read_text(encoding="utf-8")

    def dq(s: str) -> str:
        # TOML double-quoted string with minimal escaping
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    # Match start of line, optional indent, the exact key, =, old value, optional trailing comment
    def replace_key(src: str, key: str) -> tuple[str, int]:
        pattern = re.compile(
            rf"^(?P<indent>\s*){re.escape(key)}\s*=\s*"
            r'(?P<val>(?:"[^"]*"|\'.*?\'|[^#\n\r]+))(?P<comment>\s*#.*)?$',
            re.MULTILINE,
        )

        def repl(m):
            return (
                f"{m.group('indent')}{key} = {dq(new_file)}{m.group('comment') or ''}"
            )

        return pattern.subn(repl, src)

    new_text, n1 = replace_key(text, "input_database")
    new_text, n2 = replace_key(new_text, "output_database")

    # If neither key was present, append them at the end
    if n1 == 0 and n2 == 0:
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += (
            f"input_database = {dq(new_file)}\noutput_database = {dq(new_file)}\n"
        )

    if create_backup:
        Path(p.with_suffix(p.suffix + ".bak")).write_text(text, encoding="utf-8")

    p.write_text(new_text, encoding="utf-8")


def sqlite_to_excel(
    sqlite_file: str | Path,
    excel_file: str | Path,
    sheets: SheetSelection = "all",
) -> None:
    """Export selected SQLite tables to worksheets in an Excel workbook.

    Tables that exceed Excel's row or column limits are skipped and reported to
    standard output.

    Parameters
    ----------
    sqlite_file : str | Path
        Source SQLite database.
    excel_file : str | Path
        Destination Excel workbook.
    sheets : SheetSelection
        Table names to export, or ``"all"`` to export every table.

    Returns
    -------
    None
    """

    MAX_ROWS = 1_048_576
    MAX_COLS = 16_384
    # Connect to the SQLite database
    conn = sqlite3.connect(sqlite_file)
    # Get all table names
    if sheets != "all":
        tables = sheets
    else:
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table';", conn
        )
        tables = tables["name"].tolist()
        tables.sort()

    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        for table_name in tables:
            print(f"Exporting table: {table_name}")
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            n_rows = pd.read_sql_query(
                f'SELECT COUNT(*) AS n FROM "{table_name}"', conn
            )["n"].iat[0]
            n_cols = pd.read_sql_query(
                f'PRAGMA table_info("{table_name}")', conn
            ).shape[0]
            if (n_rows > MAX_ROWS) or (n_cols > MAX_COLS):
                print(
                    f"Skipping table {table_name} due to size ({n_rows} rows, {n_cols} cols)"
                )
            else:
                df.to_excel(writer, sheet_name=table_name, index=False)

    conn.close()


def sqlite_to_dfs(sqlite_file: str | Path) -> dict[str, pd.DataFrame]:
    """Read all user tables and views from a SQLite database.

    Parameters
    ----------
    sqlite_file : str | Path
        Source SQLite database.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping from table or view name to its complete contents.
    """
    conn = sqlite3.connect(sqlite_file)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%';"
        )
        tables = [row[0] for row in cur.fetchall()]
        dfs = {}
        for tbl in tables:
            # quote table name to handle odd characters
            query = f'SELECT * FROM "{tbl}"'
            dfs[tbl] = pd.read_sql_query(query, conn)
        return dfs
    finally:
        conn.close()


def update_sqlite(
    db_path: str | Path,
    data_dict: Mapping[str, pd.DataFrame],
) -> None:
    """Insert or replace rows from DataFrames in an existing SQLite database.

    Parameters
    ----------
    db_path : str | Path
        SQLite database to update.
    data_dict : Mapping[str, pd.DataFrame]
        DataFrames keyed by destination table name.

    Returns
    -------
    None
    """

    conn = sqlite3.connect(db_path)
    try:
        for table_name, df in data_dict.items():
            insert_or_replace(df, table_name, conn)
    finally:
        conn.close()


def insert_or_replace(
    df: pd.DataFrame,
    table: str,
    conn: sqlite3.Connection,
) -> None:
    """Insert DataFrame records with SQLite ``INSERT OR REPLACE`` semantics.

    Parameters
    ----------
    df : pd.DataFrame
        Rows to write. DataFrame columns are used as destination column names.
    table : str
        Destination SQLite table name.
    conn : sqlite3.Connection
        Open database connection. The transaction is committed before return.

    Returns
    -------
    None
    """

    cols = df.columns.tolist()
    col_str = ",".join(cols)
    placeholders = ",".join(["?"] * len(cols))

    sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"

    data = df.to_records(index=False).tolist()

    conn.executemany(sql, data)
    conn.commit()


if __name__ == "__main__":
    sqlite_file = "data_files/CANOE_geospatial.sqlite"
    excel_file = "data_files/CANOE_geospatial.xlsx"
    sqlite_to_excel(sqlite_file, excel_file)
