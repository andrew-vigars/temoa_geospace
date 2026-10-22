import os
import json
import logging
import requests
from pathlib import Path
from canoe_lake.bronze import register_ingestor
from canoe_lake.rninja_ingestor import ingest_renewables_ninja

import yaml

logger = logging.getLogger(__name__)

def get_config():
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return {}
@register_ingestor("eia")
def ingest_eia(output_dir: Path) -> dict:
    """Ingests EIA AEO table 3."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        raise ValueError("EIA_API_KEY not found in environment.")

    cfg = get_config().get("eia", {})
    year = cfg.get("year", 2025)
    scenario = cfg.get("scenario", "ref2025")
    base_url = (
        f"https://api.eia.gov/v2/aeo/{year}/data/?frequency=annual&data[0]=value"
        f"&facets[regionId][]=1-0&facets[scenario][]={scenario}&facets[tableId][]=3"
        "&start=2023&end=2050&sort[0][column]=period&sort[0][direction]=desc&offset=0&length=5000"
    )
    
    logger.info(f"Requesting EIA data from: {base_url}")
    resp = requests.get(base_url, params={'api_key': api_key}, timeout=60)
    resp.raise_for_status()
    
    data = resp.json()
    file_path = output_dir / "eia_aeo_table3.json"
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
        
    return {
        "file_path": str(file_path),
        "source_type": "api",
        "source_url_or_doc_id": base_url,
        "file_format": "json",
        "notes": f"EIA AEO {year} Table 3"
    }

@register_ingestor("coders")
def ingest_coders(output_dir: Path) -> dict:
    """Ingests CODERS reference data.
    
    For a full data lake snapshot, we download several key endpoints.
    """
    api_key = os.environ.get("CODERS_API_KEY")
    if not api_key:
        raise ValueError("CODERS_API_KEY not found in environment.")

    coders_root = "https://api.sesit.ca/"
    endpoints = [
        "generators",
        "storage",
        "transmission_losses",
        "reserve_requirements",
        "interface_capacities",
        "generation_generic",
        "interprovincial_transfers",
        "international_transfers",
        "historic_and_forecasted_annual_energy_demand",
        "provincial_demand"
    ]
    
    snapshot = {}
    for ep in endpoints:
        logger.info(f"Fetching CODERS endpoint: {ep}")
        try:
            # Add parameters like year=2025 if needed, but we pull base for now
            url = f"{coders_root}{ep}?key={api_key}"
            resp = requests.get(url, timeout=60)
            if resp.status_code == 200:
                snapshot[ep] = resp.json()
            else:
                logger.warning(f"Failed to fetch {ep}: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Exception fetching {ep}: {e}")

    file_path = output_dir / "coders_snapshot.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f)

    return {
        "file_path": str(file_path),
        "source_type": "api",
        "source_url_or_doc_id": coders_root,
        "file_format": "json",
        "notes": "Full CODERS database snapshot"
    }

@register_ingestor("statcan")
def ingest_statcan(output_dir: Path) -> dict:
    """Ingests StatCan Tables: 25-10-0029, 17-10-0009, 17-10-0057, 38-10-0048."""
    tables = ["25100029", "17100009", "17100057", "38100048"]
    
    # StatCan blocks default python-requests user agents.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    for tbl in tables:
        url = f"https://www150.statcan.gc.ca/n1/tbl/csv/{tbl}-eng.zip"
        logger.info(f"Downloading StatCan data from {url}")
        
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        
        file_path = output_dir / f"{tbl}-eng.zip"
        with open(file_path, "wb") as f:
            f.write(resp.content)
            
    return {
        "file_path": str(output_dir),
        "source_type": "url",
        "source_url_or_doc_id": "multiple_statcan_tables",
        "file_format": "dir",
        "notes": "StatCan Tables 25-10-0029, 17-10-0009, 17-10-0057, 38-10-0048"
    }

@register_ingestor("cer")
def ingest_cer(output_dir: Path) -> dict:
    """Ingests CER Macro-Indicators CSV."""
    year = get_config().get("cer", {}).get("year", 2023)
    url = f"https://www.cer-rec.gc.ca/open/energy/energyfutures{year}/macro-indicators-{year}.csv"
    logger.info(f"Downloading CER data from {url}")
    
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    
    file_path = output_dir / "macro-indicators-2023.csv"
    with open(file_path, "wb") as f:
        f.write(resp.content)
        
    return {
        "file_path": str(file_path),
        "source_type": "url",
        "source_url_or_doc_id": url,
        "file_format": "csv",
        "notes": "CER Macro-Indicators 2023"
    }

@register_ingestor("nrcan")
def ingest_nrcan(output_dir: Path) -> dict:
    """Ingests NRCan Comprehensive Energy Use Database HTML tables and Handbook Excel files."""
    import time
    
    url = "https://oee.nrcan.gc.ca/corporate/statistics/neud/dpa/showTable.cfm"
    PROV_CODES = ['on', 'ab', 'qc', 'bct', 'mb', 'sk', 'atl']
    cfg = get_config().get("nrcan", {})
    YEAR = cfg.get("year", 2022)
    HANDBOOK_YEAR = cfg.get("handbook_year", 2021)
    
    # We need agr (1), agg (2..12), com (1, 24, 32), res (3, 4, 8, 10, 13, 14, 21, 26, 27, 28, 31)
    requests_to_make = [
        ("agr", 1),
        *[( "agg", rn ) for rn in range(2, 13)],
        *[( "com", rn ) for rn in [1, 24, 32]],
        *[( "res", rn ) for rn in [3, 4, 8, 10, 13, 14, 21, 26, 27, 28, 31]]
    ]
    
    results = {}
    
    sess = requests.Session()
    sess.headers.update({"User-Agent": "canoe-etl/1.0"})
    
    for sector, rn in requests_to_make:
        if sector not in results:
            results[sector] = {}
        results[sector][str(rn)] = {}
        
        for code in PROV_CODES:
            params = {
                "type": "CP",
                "sector": sector,
                "juris": code,
                "year": YEAR,
                "rn": rn,
                "page": "0",
            }
            logger.info(f"Downloading NRCan HTML for sector={sector}, rn={rn}, juris={code}, year={YEAR}")
            r = sess.get(url, params=params, timeout=45)
            r.raise_for_status()
            results[sector][str(rn)][code] = r.text
            time.sleep(0.5) # Be polite
            
    file_path_json = output_dir / "nrcan_html.json"
    with open(file_path_json, "w", encoding="utf-8") as f:
        json.dump(results, f)
        
    # Download res_00_16_e.xls handbook file
    # Note: If a link is broken, the user can configure a different year for the handbook in config.yaml
    handbook_url = f"https://oee.nrcan.gc.ca/corporate/statistics/neud/dpa/data_e/downloads/handbook/Excel/{HANDBOOK_YEAR}/res_00_16_e.xls"
    logger.info(f"Downloading NRCan Handbook {handbook_url}")
    r_hb = sess.get(handbook_url, timeout=45)
    r_hb.raise_for_status()
    file_path_xls = output_dir / "res_00_16_e.xls"
    with open(file_path_xls, "wb") as f:
        f.write(r_hb.content)
        
    return {
        "file_path": str(output_dir), # return the dir because we have multiple files
        "source_type": "url",
        "source_url_or_doc_id": "multiple_nrcan_urls",
        "file_format": "dir",
        "notes": f"NRCan HTML tables for year {YEAR} and handbook"
    }

# Register the imported rninja ingestor
register_ingestor("renewables_ninja")(ingest_renewables_ninja)

@register_ingestor("oedi_stock")
def ingest_oedi_stock(output_dir: Path) -> dict:
    import requests
    states = ['MI', 'MT', 'WA', 'ME', 'ND']
    
    cfg = get_config().get("oedi", {})
    year = cfg.get("year", 2024)
    c_release = cfg.get("comstock_release", "comstock_amy2018_release_2")
    c_up = cfg.get("comstock_upgrade", 39)
    
    # ComStock (up39)
    buildings = [
        'fullservicerestaurant', 'hospital', 'largehotel', 'largeoffice', 'mediumoffice', 
        'outpatient', 'primaryschool', 'quickservicerestaurant', 'retailstandalone', 
        'retailstripmall', 'secondaryschool', 'smallhotel', 'smalloffice', 'warehouse'
    ]
    for st in states:
        for bldg in buildings:
            file_path = output_dir / f"up{c_up}-{st.lower()}-{bldg}.csv"
            # Resume capability: skip if already downloaded (files are typically >1MB)
            if file_path.exists() and file_path.stat().st_size > 100000:
                continue
                
            url = f"https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/{year}/{c_release}/timeseries_aggregates/by_state/upgrade={c_up}/state={st}/up{c_up}-{st.lower()}-{bldg}.csv"
            logger.info(f"Downloading {url}")
            try:
                r = requests.get(url, timeout=(15, 120))
                if r.status_code == 200:
                    with open(file_path, "wb") as f:
                        f.write(r.content)
                else:
                    logger.warning(f"Failed to fetch {url}: {r.status_code}")
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}")
                
    r_release = cfg.get("resstock_release", "resstock_amy2018_release_2")
    r_up = cfg.get("resstock_upgrade", 16)
    
    # ResStock (up16)
    housing_files = [
        'mobile_home', 'multi-family_with_5plus_units', 
        'single-family_attached', 'single-family_detached'
    ]
    for st in states:
        for h in housing_files:
            file_path = output_dir / f"up{r_up}-{st.lower()}-{h}.csv"
            if file_path.exists() and file_path.stat().st_size > 100000:
                continue
                
            url = f"https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/{year}/{r_release}/timeseries_aggregates/by_state/upgrade={r_up}/state={st}/up{r_up}-{st.lower()}-{h}.csv"
            logger.info(f"Downloading {url}")
            try:
                r = requests.get(url, timeout=(15, 120))
                if r.status_code == 200:
                    with open(file_path, "wb") as f:
                        f.write(r.content)
                else:
                    logger.warning(f"Failed to fetch {url}: {r.status_code}")
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}")

    return {
        "file_path": str(output_dir),
        "source_type": "url",
        "source_url_or_doc_id": "oedi_data_lake_s3",
        "file_format": "dir",
        "notes": "OEDI ComStock up39 and ResStock up16"
    }

@register_ingestor("ieso")
def ingest_ieso(output_dir: Path) -> dict:
    import requests
    from datetime import date
    weather_year = get_config().get("ieso", {}).get("weather_year", 2023)
    
    # PUB_GenOutputbyFuelHourly
    url_hourly = f"http://reports.ieso.ca/public/GenOutputbyFuelHourly/PUB_GenOutputbyFuelHourly_{weather_year}.xml"
    r = requests.get(url_hourly, timeout=45)
    if r.status_code == 200:
        with open(output_dir / f"PUB_GenOutputbyFuelHourly_{weather_year}.xml", "wb") as f:
            f.write(r.content)

    # PUB_GenOutputCapabilityMonth
    for m in range(1, 13):
        mm = f"{m:02d}"
        url_mon = f"http://reports.ieso.ca/public/GenOutputCapabilityMonth/PUB_GenOutputCapabilityMonth_{weather_year}{mm}.csv"
        r = requests.get(url_mon, timeout=45)
        if r.status_code == 200:
            with open(output_dir / f"PUB_GenOutputCapabilityMonth_{weather_year}{mm}.csv", "wb") as f:
                f.write(r.content)

    return {
        "file_path": str(output_dir),
        "source_type": "url",
        "source_url_or_doc_id": "ieso_reports",
        "file_format": "dir",
        "notes": "IESO Hourly and Monthly Reports"
    }

@register_ingestor("atb")
def ingest_atb(output_dir: Path) -> dict:
    import requests
    cfg = get_config().get("atb", {})
    year = cfg.get("year", 2024)
    version = cfg.get("version", "v3.0.0")
    url = f"https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/{year}/{version}/ATBe.csv"
    r = requests.get(url, timeout=45)
    if r.status_code == 200:
        with open(output_dir / "ATBe.csv", "wb") as f:
            f.write(r.content)
            
    return {
        "file_path": str(output_dir),
        "source_type": "url",
        "source_url_or_doc_id": url,
        "file_format": "dir",
        "notes": "NREL ATB 2024"
    }

@register_ingestor("epa")
def ingest_epa(output_dir: Path) -> dict:
    import requests
    url = get_config().get("epa", {}).get("url", "https://www.epa.gov/system/files/other-files/2025-01/ghg-emission-factors-hub-2025.xlsx")
    r = requests.get(url, timeout=45)
    if r.status_code == 200:
        with open(output_dir / "ghg-emission-factors-hub-2025.xlsx", "wb") as f:
            f.write(r.content)
            
    return {
        "file_path": str(output_dir),
        "source_type": "url",
        "source_url_or_doc_id": url,
        "file_format": "dir",
        "notes": "EPA GHG Emission Factors 2025"
    }
