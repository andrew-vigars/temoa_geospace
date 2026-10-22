import os
import json
import time
import logging
from pathlib import Path
import requests
import pandas as pd
from canoe_lake.catalog import get_connection, PROJECT_ROOT

logger = logging.getLogger(__name__)

def ingest_renewables_ninja(output_dir: Path) -> dict:
    """Ingests Renewables Ninja capacity factors and country weather CSVs."""
    api_key = os.environ.get("RENEWABLES_NINJA_API_KEY")
    if not api_key:
        raise ValueError("Missing RENEWABLES_NINJA_API_KEY in .env")
        
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Token {api_key}"})
    
    # 1. Download Country CSVs
    country_files = [
        ("ca_temperature.csv", "https://www.renewables.ninja/country_downloads/CA/ninja-weather-country-CA-temperature_pop_wtd-merra2.csv"),
        ("ca_humidity.csv", "https://www.renewables.ninja/country_downloads/CA/ninja-weather-country-CA-humidity_pop_wtd-merra2.csv"),
        ("us_temperature.csv", "https://www.renewables.ninja/country_downloads/US/ninja-weather-country-US-temperature_pop_wtd-merra2.csv"),
        ("us_humidity.csv", "https://www.renewables.ninja/country_downloads/US/ninja-weather-country-US-humidity_pop_wtd-merra2.csv")
    ]
    
    for filename, url in country_files:
        logger.info(f"Downloading {filename}")
        r = sess.get(url)
        r.raise_for_status()
        with open(output_dir / filename, "wb") as f:
            f.write(r.content)
            
    # 2. Get CODERS generators from Bronze
    tasks = []
    conn = get_connection()
    res = conn.execute("SELECT file_path FROM bronze_sources WHERE source_name = 'coders' ORDER BY collection_date DESC LIMIT 1").fetchone()
    if res:
        coders_path = PROJECT_ROOT / res[0]
        with open(coders_path, "r", encoding="utf-8") as f:
            coders_data = json.load(f)
            
        generators = coders_data.get("generators", [])
        
        # Solar
        solar_gens = [g for g in generators if g.get('gen_type_copper') == 'solar']
        for g in solar_gens:
            tasks.append(('pv', g.get('latitude'), g.get('longitude')))
            
        # Wind (Onshore and Offshore)
        wind_gens = [g for g in generators if g.get('gen_type_copper') and 'wind' in str(g.get('gen_type_copper'))]
        for g in wind_gens:
            tasks.append(('wind', g.get('latitude'), g.get('longitude')))
            
    # Deduplicate to save precious API calls (many generators share exact coordinates)
    tasks = list(set(tasks))
            
    limit = os.environ.get("CANOE_TEST_LIMIT")
    if limit:
        tasks = tasks[:int(limit)]
        
    file_path = output_dir / "rninja_snapshot.json"
    results = []
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                results = json.load(f)
                logger.info(f"Loaded {len(results)} existing R-Ninja generators from snapshot.")
        except json.JSONDecodeError:
            pass

    completed = set((r['type'], r['lat'], r['lon']) for r in results)
    
    api_url = "https://www.renewables.ninja/api/"
    
    from canoe_lake.ingestors import get_config
    
    cfg = get_config().get("renewables_ninja", {})
    date_from = cfg.get("date_from", "2022-01-01")
    date_to = cfg.get("date_to", "2022-12-31")
    
    for t_type, lat, lon in tasks:
        if (t_type, lat, lon) in completed:
            continue
            
        ep = f"data/{t_type}"
        params = {
            'lat': lat, 'lon': lon, 'date_from': date_from, 'date_to': date_to, 'format': 'json'
        }
        if t_type == 'pv':
            params.update({'dataset': 'merra2', 'capacity': 1.0, 'system_loss': 0.1, 'tracking': 0, 'tilt': 35, 'azim': 180})
        elif t_type == 'wind':
            params.update({'dataset': 'merra2', 'capacity': 1.0, 'height': 80, 'turbine': 'Vestas V80 2000'})
            
        logger.info(f"Querying R-Ninja API {t_type} for lat={lat}, lon={lon}")
        while True:
            try:
                r = sess.get(api_url + ep, params=params, timeout=(15, 120))
                if r.ok:
                    j = r.json()
                    results.append({
                        "type": t_type,
                        "lat": lat,
                        "lon": lon,
                        "data": j['data']
                    })
                    # Save incrementally so we don't lose progress on crash!
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(results, f)
                        
                    time.sleep(1) # Be polite
                    break
                elif r.status_code == 429:
                    logger.warning("Rate limit hit. Sleeping for 1 hour")
                    time.sleep(3605)
                else:
                    logger.error(f"Failed to fetch {t_type} for {lat}, {lon}: {r.status_code}")
                    break
            except Exception as e:
                logger.warning(f"Connection dropped or timed out ({e}). Sleeping 10s and retrying...")
                time.sleep(10)
        
    return {
        "file_path": str(output_dir), # Return directory because there are multiple files
        "source_type": "api",
        "source_url_or_doc_id": "renewables.ninja",
        "file_format": "dir",
        "notes": f"R-Ninja snapshot and weather files"
    }
