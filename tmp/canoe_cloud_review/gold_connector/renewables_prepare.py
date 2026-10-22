# -*- coding: utf-8 -*-
"""
Created on Tue Aug 18 12:20:27 2026

@author: david
"""

import pandas as pd
from pathlib import Path


def shift_years(input_file, output_file):
    """
    Subtract 5 years from period and/or vintage columns if present,
    then remove rows where period or vintage equals 2020.
    """

    # Read parquet file
    df = pd.read_parquet(input_file)

    print(f"\nProcessing: {input_file}")
    print(f"Original rows: {len(df):,}")

    # Track which time columns actually exist
    time_columns = []

    # Shift period if it exists
    if "period" in df.columns:
        df["period"] = df["period"] - 5
        time_columns.append("period")
        print("  Shifted period by -5 years")

    # Shift vintage if it exists
    if "vintage" in df.columns:
        df["vintage"] = df["vintage"] - 5
        time_columns.append("vintage")
        print("  Shifted vintage by -5 years")

    # Remove rows containing 2020 in any existing time column
    for column in time_columns:
        df = df[df[column] != 2020]

    print(f"Rows after removing 2020: {len(df):,}")

    # Save updated parquet
    df.to_parquet(output_file, index=False)

    print(f"Saved to: {output_file}")


# Input/output files
files = [
    ("cost_fixed.parquet", "cost_fixed_shifted.parquet"),
    ("capacity_credit.parquet", "capacity_credit_shifted.parquet"),
    ("capacity_factor.parquet", "capacity_factor_shifted.parquet"),
    ("cost_invest.parquet", "cost_invest_shifted.parquet"),
    ("max_capacity.parquet", "max_capacity_shifted.parquet"),
]


# Process all files
for input_name, output_name in files:
    shift_years(
        Path(input_name),
        Path(output_name)
    )