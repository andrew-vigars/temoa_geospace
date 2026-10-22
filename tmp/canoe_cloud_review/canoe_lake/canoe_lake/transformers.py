import json
import pandas as pd
from pathlib import Path
import yaml
from canoe_lake.silver import register_silver

def get_config():
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return {}

@register_silver("eia_table3", bronze_source="eia")
def transform_eia_table3(bronze_path: Path) -> pd.DataFrame:
    """Transforms raw EIA Bronze JSON into a structured Pandas DataFrame."""
    with open(bronze_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    df = pd.DataFrame(data['response']['data'])
    
    # Optionally, we could add data type enforcement, column renaming, and cleaning here.
    # For now, we replicate the exact previous behavior to ensure seamless compatibility.
    
    return df

@register_silver("coders", bronze_source="coders")
def transform_coders(bronze_path: Path) -> dict[str, pd.DataFrame]:
    """Transforms raw CODERS Bronze snapshot into individual Pandas DataFrames."""
    with open(bronze_path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)
        
    dfs = {}
    for endpoint, data_json in snapshot.items():
        if isinstance(data_json, dict) and 'message' in data_json:
            continue
        if data_json is not None:
            dfs[endpoint] = pd.DataFrame(index=range(len(data_json)), data=data_json)
            
    return dfs

@register_silver("statcan_shares", bronze_source="statcan")
def transform_statcan(bronze_path: Path) -> pd.DataFrame:
    """Computes regional sector shares from StatCan Table 25-10-0029."""
    import zipfile
    
    with zipfile.ZipFile(bronze_path / "25100029-eng.zip") as zf:
        with zf.open("25100029.csv") as csvf:
            df = pd.read_csv(csvf)
            
    REGION_LIST = ['Newfoundland and Labrador', 'New Brunswick', 'Nova Scotia', 'Prince Edward Island']
    SECTOR_LIST = [
        'Agriculture, fishing, hunting and trapping',
        'Total mining and oil and gas extraction', 'Pulp and paper manufacturing', ' Iron and steel manufacturing',
        'Cement manufacturing', 'Aluminum and non-ferrous metal manufacturing', 'Refined petroleum products manufacturing',
        'Chemicals manufacturing', 'All other manufacturing', 'Forestry, logging and support activities', 'Construction'
    ]
    FUEL_LIST = ['Total primary and secondary energy']
    
    statcan_year = get_config().get("statcan", {}).get("year", 2023)
    
    df1 = df[['REF_DATE', 'GEO', 'Fuel type', 'Supply and demand characteristics', 'VALUE']].copy()
    df1 = df1[df1['REF_DATE'] == statcan_year]
    df1 = df1[df1['GEO'].isin(REGION_LIST)]
    df1 = df1[df1['Fuel type'].isin(FUEL_LIST)]
    df1 = df1[df1['Supply and demand characteristics'].isin(SECTOR_LIST)]
    df1 = df1[df1['VALUE'] != 0]

    # Compute shares
    records = []
    for sector in SECTOR_LIST:
        sub = df1[df1['Supply and demand characteristics'] == sector]
        total = float(sub['VALUE'].sum())
        for reg, reg_df in sub.groupby('GEO'):
            val = float(reg_df['VALUE'].sum())
            share = (val / total) if total else 0.0
            records.append({"Sector": sector, "Region": reg, "Share": share})
            
    return pd.DataFrame(records)

@register_silver("cer_macro", bronze_source="cer")
def transform_cer_macro(bronze_path: Path) -> pd.DataFrame:
    """Reads CER Macro-Indicators CSV and returns a Pandas DataFrame."""
    return pd.read_csv(bronze_path)

@register_silver("nrcan_agr", bronze_source="nrcan")
def transform_nrcan_agr(bronze_path: Path) -> dict[str, pd.DataFrame]:
    from io import StringIO
    json_path = bronze_path if bronze_path.is_file() else bronze_path / "nrcan_html.json"
    with open(json_path, "r", encoding="utf-8") as f:
        html_dict = json.load(f)
        
    mapping = {"ab": "AB", "on": "ON", "bct": "BC", "mb": "MB", "sk": "SK", "qc": "QC", "atl": "ATL"}
    dfs = {}
    
    # agr is rn=1
    for code, html in html_dict.get("agr", {}).get("1", {}).items():
        tables = pd.read_html(StringIO(html))
        dfs[mapping[code]] = tables[0]
        
    return dfs

@register_silver("nrcan_agg", bronze_source="nrcan")
def transform_nrcan_agg(bronze_path: Path) -> dict[str, pd.DataFrame]:
    from io import StringIO
    json_path = bronze_path if bronze_path.is_file() else bronze_path / "nrcan_html.json"
    with open(json_path, "r", encoding="utf-8") as f:
        html_dict = json.load(f)
        
    mapping = {"ab": "AB", "on": "ON", "bct": "BC", "mb": "MB", "sk": "SK", "qc": "QC", "atl": "ATL"}
    dfs = {}
    
    # agg is rn=2..12
    agg_dict = html_dict.get("agg", {})
    for rn_str, code_dict in agg_dict.items():
        for code, html in code_dict.items():
            tables = pd.read_html(StringIO(html))
            # key e.g. AB_2
            key = f"{mapping[code]}_{rn_str}"
            dfs[key] = tables[0]
            
    return dfs

@register_silver("renewables_ninja", bronze_source="renewables_ninja")
def transform_renewables_ninja(bronze_path: Path) -> pd.DataFrame:
    # bronze_path is a directory
    snapshot_path = bronze_path / "rninja_snapshot.json"
    if not snapshot_path.exists():
        return pd.DataFrame(columns=['timestamp', 'ninja_type', 'lat', 'lon'])
        
    with open(snapshot_path, "r", encoding="utf-8") as f:
        results = json.load(f)
        
    all_dfs = []
    for loc in results:
        t_type = loc['type']
        lat = loc['lat']
        lon = loc['lon']
        data = loc['data']
        
        df = pd.DataFrame(data).transpose()
        df.index.name = 'timestamp'
        df.reset_index(inplace=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        
        # Add metadata columns
        df['ninja_type'] = t_type
        df['lat'] = lat
        df['lon'] = lon
        all_dfs.append(df)
        
    if not all_dfs:
        # Return empty DF with expected schema
        return pd.DataFrame(columns=['timestamp', 'ninja_type', 'lat', 'lon'])
        
    return pd.concat(all_dfs, ignore_index=True)

@register_silver("rninja_weather", bronze_source="renewables_ninja")
def transform_rninja_weather(bronze_path: Path) -> dict[str, pd.DataFrame]:
    # bronze_path is a directory
    dfs = {}
    files = ["ca_temperature", "ca_humidity", "us_temperature", "us_humidity"]
    for f in files:
        csv_path = bronze_path / f"{f}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path, skiprows=3, index_col=0)
            # Retain datetime index as column
            df.index.name = 'timestamp'
            df = df.reset_index()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            dfs[f] = df
    return dfs

@register_silver("nrcan_com", bronze_source="nrcan")
def transform_nrcan_com(bronze_path: Path) -> dict[str, pd.DataFrame]:
    from io import StringIO
    json_path = bronze_path if bronze_path.is_file() else bronze_path / "nrcan_html.json"
    with open(json_path, "r", encoding="utf-8") as f:
        html_dict = json.load(f)
        
    mapping = {"ab": "AB", "on": "ON", "bct": "BC", "mb": "MB", "sk": "SK", "qc": "QC", "atl": "ATL"}
    dfs = {}
    
    com_dict = html_dict.get("com", {})
    for rn_str, code_dict in com_dict.items():
        for code, html in code_dict.items():
            tables = pd.read_html(StringIO(html))
            key = f"{mapping[code]}_{rn_str}"
            dfs[key] = tables[0]
            
    return dfs

@register_silver("nrcan_res", bronze_source="nrcan")
def transform_nrcan_res(bronze_path: Path) -> dict[str, pd.DataFrame]:
    from io import StringIO
    json_path = bronze_path if bronze_path.is_file() else bronze_path / "nrcan_html.json"
    with open(json_path, "r", encoding="utf-8") as f:
        html_dict = json.load(f)
        
    mapping = {"ab": "AB", "on": "ON", "bct": "BC", "mb": "MB", "sk": "SK", "qc": "QC", "atl": "ATL"}
    dfs = {}
    
    res_dict = html_dict.get("res", {})
    for rn_str, code_dict in res_dict.items():
        for code, html in code_dict.items():
            tables = pd.read_html(StringIO(html))
            key = f"{mapping[code]}_{rn_str}"
            dfs[key] = tables[0]
            
    return dfs

@register_silver("nrcan_res_handbook", bronze_source="nrcan")
def transform_nrcan_res_handbook(bronze_path: Path) -> pd.DataFrame:
    # bronze_path is a directory
    xls_path = bronze_path / "res_00_16_e.xls"
    if xls_path.exists():
        df = pd.read_excel(xls_path, skiprows=7)
        return df
    return pd.DataFrame()



@register_silver("oedi_up39", bronze_source="oedi_stock")
def transform_oedi_up39(bronze_path: Path) -> dict[str, pd.DataFrame]:
    """Transforms OEDI up39 data into DataFrames by building type."""
    dfs = {}
    for file in bronze_path.glob("up39-*.csv"):
        # filename is up39-state-building.csv
        parts = file.stem.split("-", 2)
        if len(parts) == 3:
            state, bldg = parts[1].upper(), parts[2]
            key = f"{state}_{bldg}"
            dfs[key] = pd.read_csv(file)
    return dfs

@register_silver("oedi_up16", bronze_source="oedi_stock")
def transform_oedi_up16(bronze_path: Path) -> dict[str, pd.DataFrame]:
    """Transforms OEDI up16 data into DataFrames by housing type."""
    dfs = {}
    for file in bronze_path.glob("up16-*.csv"):
        parts = file.stem.split("-", 2)
        if len(parts) == 3:
            state, housing = parts[1].upper(), parts[2]
            key = f"{state}_{housing}"
            dfs[key] = pd.read_csv(file)
    return dfs

@register_silver("statcan_25100029", bronze_source="statcan")
def transform_statcan_25100029(bronze_path: Path) -> pd.DataFrame:
    import zipfile
    with zipfile.ZipFile(bronze_path / "25100029-eng.zip") as zf:
        with zf.open("25100029.csv") as f:
            return pd.read_csv(f)

@register_silver("statcan_17100009", bronze_source="statcan")
def transform_statcan_17100009(bronze_path: Path) -> pd.DataFrame:
    import zipfile
    with zipfile.ZipFile(bronze_path / "17100009-eng.zip") as zf:
        with zf.open("17100009.csv") as f:
            return pd.read_csv(f)

@register_silver("statcan_17100057", bronze_source="statcan")
def transform_statcan_17100057(bronze_path: Path) -> pd.DataFrame:
    import zipfile
    with zipfile.ZipFile(bronze_path / "17100057-eng.zip") as zf:
        with zf.open("17100057.csv") as f:
            return pd.read_csv(f)

@register_silver("statcan_38100048", bronze_source="statcan")
def transform_statcan_38100048(bronze_path: Path) -> pd.DataFrame:
    import zipfile
    with zipfile.ZipFile(bronze_path / "38100048-eng.zip") as zf:
        with zf.open("38100048.csv") as f:
            return pd.read_csv(f)

@register_silver("ieso_hourly", bronze_source="ieso")
def transform_ieso_hourly(bronze_path: Path) -> pd.DataFrame:
    # IESO Hourly XML
    import xml.etree.ElementTree as ET
    files = list(bronze_path.glob("PUB_GenOutputbyFuelHourly_*.xml"))
    if not files: return pd.DataFrame()
    
    with open(files[0], "r", encoding="utf-8") as f:
        xml_content = f.read()
    return pd.DataFrame([{"xml_data": xml_content}])

@register_silver("ieso_monthly", bronze_source="ieso")
def transform_ieso_monthly(bronze_path: Path) -> dict[str, pd.DataFrame]:
    dfs = {}
    for file in bronze_path.glob("PUB_GenOutputCapabilityMonth_*.csv"):
        # e.g. 202301
        month = file.stem.split("_")[-1]
        dfs[month] = pd.read_csv(file, skiprows=3, index_col=False)
    return dfs

@register_silver("atb", bronze_source="atb")
def transform_atb(bronze_path: Path) -> pd.DataFrame:
    return pd.read_csv(bronze_path / "ATBe.csv", dtype="unicode")

@register_silver("epa", bronze_source="epa")
def transform_epa(bronze_path: Path) -> pd.DataFrame:
    df = pd.read_excel(bronze_path / "ghg-emission-factors-hub-2025.xlsx", skiprows=13, nrows=76)
    return df.astype(str)

@register_silver('weather_maps', bronze_source='renewables_ninja')
def transform_weather_maps(bronze_path: Path) -> dict:
    import numpy as np
    import yaml
    import logging
    logger = logging.getLogger(__name__)
    from canoe_lake.catalog import PROJECT_ROOT
    
    with open(PROJECT_ROOT / 'canoe_lake' / 'config.yaml', 'r') as f:
        cfg = yaml.safe_load(f).get('weather_mapping', {})
        
    weather_year = cfg.get('weather_year', 2018)
    regions = cfg.get('regions', {})
    
    if not regions:
        logger.warning('No regions defined for weather_mapping in config.yaml')
        return {}
        
    def read_weather_csv(filename):
        df = pd.read_csv(bronze_path / filename, skiprows=3, index_col=0)
        df.index = pd.to_datetime(df.index)
        return df.loc[df.index.year == weather_year]
        
    logger.info(f'Loading Renewables Ninja weather files for year {weather_year}...')
    df_us_tmp = read_weather_csv('us_temperature.csv')
    df_us_hum = read_weather_csv('us_humidity.csv')
    df_ca_tmp = read_weather_csv('ca_temperature.csv')
    df_ca_hum = read_weather_csv('ca_humidity.csv')
    
    # We must reset index or just extract values
    results = {}
    
    for reg, mapping in regions.items():
        ca_col = mapping['ca']
        us_state = mapping['us']
        us_col = f'US.{us_state}'
        
        logger.info(f'Generating weather map for {reg} ({us_col} -> {ca_col})...')
        
        df_ca = pd.concat([df_ca_tmp[ca_col], df_ca_hum[ca_col]], axis=1).astype(float)
        df_us = pd.concat([df_us_tmp[us_col], df_us_hum[us_col]], axis=1).astype(float)
        df_ca.columns = ['temp', 'hum']
        df_us.columns = ['temp', 'hum']
        
        us_temps = df_us['temp'].values
        us_hums = df_us['hum'].values
        us_temp_max = np.max(us_temps)
        us_temp_min = np.min(us_temps)
        
        map_matrix = np.zeros((len(df_ca), len(df_us)))
        unmatched = 0.0
        
        for h in range(len(df_ca)):
            ca_temp = df_ca.iloc[h]['temp']
            ca_hum = df_ca.iloc[h]['hum']
            
            row_map = 1.0 * ((ca_temp <= us_temps + 1) & (ca_temp >= us_temps - 1) & (ca_hum == us_hums))
            
            if np.sum(row_map) == 0:
                unmatched += 1
                if ca_temp > us_temp_max:
                    row_map = 1.0 * (us_temps == us_temp_max)
                elif ca_temp < us_temp_min:
                    row_map = 1.0 * (us_temps == us_temp_min)
                    
            if np.sum(row_map) != 0:
                row_map = row_map / np.sum(row_map)
            else:
                row_map = row_map * np.nan
                
            map_matrix[h, :] = row_map
            
        logger.info(f'{round((1 - unmatched/len(df_ca))*100, 1)}% of hours found +-1C temperature match for {reg}.')
        results[reg] = map_matrix
        
    return results

