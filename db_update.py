import pandas as pd
import sqlite3






def excel_to_sqlite(excel_path, sqlite_path):
    # Load all sheets
    xls = pd.ExcelFile(excel_path)
    sheet_names = xls.sheet_names

    # Connect to SQLite (this creates the file if it doesn't exist)
    conn = sqlite3.connect(sqlite_path)

    # Write each sheet as a table in the SQLite DB
    for sheet in sheet_names:
        df = xls.parse(sheet)
        df.to_sql(sheet, conn, if_exists='replace', index=False)

    conn.close()


    # Connect to SQLite (this creates the file if it doesn't exist)
    conn = sqlite3.connect(sqlite_path)

    # Write each sheet as a table in the SQLite DB
    for sheet in sheet_names:
        df = xls.parse(sheet)
        df.to_sql(sheet, conn, if_exists='replace', index=False)

    conn.close()

def update_excel_sheets(file_path, db):
    # Load existing Excel file
    with pd.ExcelFile(file_path) as xls:
        # Read all sheets into a dictionary
        all_sheets = {sheet_name: xls.parse(sheet_name) for sheet_name in xls.sheet_names}
    
    # Overwrite only sheets that exist in db
    for sheet_name, df in db.items():
        all_sheets[sheet_name] = df

    # Write back to the Excel file
    with pd.ExcelWriter(file_path, engine='openpyxl', mode='w') as writer:
        for sheet_name, df in all_sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)




