# Gold Layer Connector

Welcome to the CANOE Data Lake Gold Connector! This utility script is designed specifically for Gold-layer modelers to access the processed Silver datasets without having to worry about complex parsing, nested folders, or rate-limited APIs.

## 📥 1. Automated Download

Because the Silver layer contains highly compressed Parquet files that are queried across Canada and the US, downloading them individually via scripts often hits Google Drive's API rate limits. 

To completely bypass this, we have uploaded the datasets as highly-compressed `.zip` files on Google Drive. The `GoldConnector` Python class is now programmed to automatically:
1. Connect to Google Drive
2. Download the `silver.zip` and `renewables_cache.zip` files
3. Extract them locally into `./silver_cache/`

You don't need to manually click anything in your browser!

## 🚀 2. Install Requirements

Ensure you have the required data processing libraries installed:
```bash
pip install pandas numpy pyarrow gdown
```

## 🛠️ 3. Load the Data in Python

Drop `gold_connector.py` into your working directory. You can now instantly load any of the hundreds of datasets into native Pandas DataFrames or Numpy Arrays!

```python
from gold_connector import GoldConnector

# Initialize the connector (make sure your data is in ./silver_cache/silver)
# By default, it will automatically find and use the most recent dataset available.
# You can also specify an exact date string to use an older dataset: GoldConnector(target_date="2026-07-01")
connector = GoldConnector()

# ---------------------------------------------------------
# 1. Weather Maps
# ---------------------------------------------------------
# Returns a dictionary of 8760x8760 Numpy arrays
weather = connector.get_weather_matrices()
print("Alberta Weather Matrix:", weather["AB"].shape)

# ---------------------------------------------------------
# 2. OEDI Building Stock (ComStock & ResStock)
# ---------------------------------------------------------
# Returns a nested dictionary: {stock_type} -> {US_State} -> {Building_Type}
buildings = connector.get_building_stock()
hospital_df = buildings["comstock"]["MI"]["hospital"]
print("Michigan Hospital Rows:", len(hospital_df))

# ---------------------------------------------------------
# 3. CODERS Generator Database
# ---------------------------------------------------------
# Returns a dictionary of all CODERS tables
coders = connector.get_coders_data()
generators_df = coders["coders_generators"]

# ---------------------------------------------------------
# 4. NRCAN Data
# ---------------------------------------------------------
# Returns a dictionary of all commercial, residential, agricultural, and aggregated tables
nrcan = connector.get_nrcan_data()
commercial_bc_df = nrcan["nrcan_com_BC_32"]

# ---------------------------------------------------------
# 5. Macro Indicators & Other Data
# ---------------------------------------------------------
macro = connector.get_macro_indicators() # CER and StatCan
ieso = connector.get_ieso_generation()   # IESO hourly/monthly
others = connector.get_other_datasets()  # ATB, EPA, Renewables Ninja

# ---------------------------------------------------------
# 6. Renewables Cache
# ---------------------------------------------------------
renewables = connector.get_renewables_cache()
capacity_factor_df = renewables.get("capacity_factor")
```

### Processing Data
No aggregations or alterations are made by this script. The `GoldConnector` simply loads the pure Silver `.parquet` and `.npz` files and serves them natively as Pandas DataFrames and Numpy arrays. You have complete flexibility to clean, aggregate, roll up, and model the data however you need for the Gold layer.
