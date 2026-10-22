import os
import pandas as pd
import numpy as np
import logging
from pathlib import Path

# Set up logging for documentation
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GoldConnector:
    """
    A utility class for downloading and loading CANOE Data Lake Silver datasets from Google Drive.
    Designed specifically for Gold-layer modelers to access processed datasets.
    """
    
    # File ID specifically for the `silver.zip` file to bypass rate limits
    SILVER_ZIP_FILE_ID = "163zEbqGWosuEih9Al83-MuT_fW7VnZgU"
    RENEWABLES_CACHE_ZIP_FILE_ID = "1U8SxKezPpENb94-OGR-aFoUbq73q-M99"
    
    def __init__(self, cache_dir: str = "./silver_cache", target_date: str = None):
        self.cache_dir = Path(cache_dir)
        self.base_silver_dir = self.cache_dir / "silver"
        self.target_date = target_date
        self._resolve_target_dir()
        
    def _resolve_target_dir(self):
        """
        Determines the correct date folder inside the silver directory to use.
        If target_date is provided, uses that. Otherwise, finds the most recent date folder.
        """
        if not self.base_silver_dir.exists():
            self.silver_dir = self.base_silver_dir
            return
            
        import re
        date_folders = [d for d in self.base_silver_dir.iterdir() if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)]
        
        if self.target_date:
            self.silver_dir = self.base_silver_dir / self.target_date
            if not self.silver_dir.exists():
                logger.warning(f"Target date folder {self.target_date} not found. Data may be missing.")
        elif date_folders:
            latest_date = sorted([d.name for d in date_folders])[-1]
            logger.info(f"Automatically selected most recent dataset: {latest_date}")
            self.silver_dir = self.base_silver_dir / latest_date
        else:
            self.silver_dir = self.base_silver_dir
        
    def sync_from_drive(self):
        """
        Connects to Google Drive using `gdown` and downloads the Silver data layer zip file.
        Unzips it locally to make all files available to the data loaders.
        """
        import gdown
        import zipfile
        
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self.cache_dir / "silver.zip"
        renewables_zip_path = self.cache_dir / "renewables_cache.zip"
        
        logger.info(f"Connecting to Google Drive to download Silver layer zip...")
        # Download the single zip file to bypass folder rate-limits
        gdown.download(id=self.SILVER_ZIP_FILE_ID, output=str(zip_path), quiet=False, use_cookies=False)
        
        logger.info(f"Extracting {zip_path}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # We extract to cache_dir because the zip itself likely contains the "silver" folder
            zip_ref.extractall(self.cache_dir)
            
        logger.info(f"Connecting to Google Drive to download Renewables Cache zip...")
        gdown.download(id=self.RENEWABLES_CACHE_ZIP_FILE_ID, output=str(renewables_zip_path), quiet=False, use_cookies=False)
        
        logger.info(f"Extracting {renewables_zip_path}...")
        with zipfile.ZipFile(renewables_zip_path, 'r') as zip_ref:
            zip_ref.extractall(self.cache_dir)
            
        logger.info("Silver layer synchronization and extraction complete.")
        self._resolve_target_dir()

    def get_weather_matrices(self) -> dict:
        """
        Loads the 8760x8760 weather mapping matrices.
        
        Returns:
            dict: A dictionary where keys are regions (e.g. 'ON', 'AB') and values are
                  Numpy arrays of shape (8760, 8760) containing the temperature mapping weights.
        """
        weather_data = {}
        npz_files = list(self.silver_dir.rglob("weather_maps_*.npz"))
        for file in npz_files:
            # File format: weather_maps_ON_2026-07-01.npz
            region = file.stem.split('_')[2]
            try:
                with np.load(file) as data:
                    weather_data[region] = data['arr_0']
            except Exception as e:
                logger.error(f"Failed to load weather matrix {file.name}: {e}")
        return weather_data

    def get_building_stock(self) -> dict:
        """
        Loads the OEDI building stock datasets (ComStock and ResStock).
        As requested, returns a dictionary separated by state and building type.
        
        Returns:
            dict: A nested dictionary structured as:
                  {
                      "comstock": {"MI": {"hospital": DataFrame, "largehotel": DataFrame, ...}, ...},
                      "resstock": {"MI": {"single-family_detached": DataFrame, ...}, ...}
                  }
        """
        oedi_data = {"comstock": {}, "resstock": {}}
        oedi_files = list(self.silver_dir.rglob("oedi_*.parquet"))
        
        for file in oedi_files:
            # Format: oedi_up39_MI_hospital_2026-07-01.parquet or oedi_up16_ME_multi-family_with_5plus_units_2026-07-01.parquet
            parts = file.stem.split('_')
            if len(parts) < 5: continue
            
            upgrade = parts[1] # up39 or up16
            state = parts[2]
            
            # Rejoin the remaining parts for the building type (excluding the date at the end)
            bldg_type = '_'.join(parts[3:-1])
            
            stock_type = "comstock" if upgrade == "up39" else "resstock"
            
            if state not in oedi_data[stock_type]:
                oedi_data[stock_type][state] = {}
                
            try:
                df = pd.read_parquet(file)
                oedi_data[stock_type][state][bldg_type] = df
            except Exception as e:
                logger.error(f"Failed to load OEDI file {file.name}: {e}")
                
        return oedi_data

    def get_coders_data(self) -> dict:
        """
        Loads all CODERS generator databases (generators, storage, transmission, etc).
        
        Returns:
            dict: A dictionary of DataFrames containing various CODERS tables.
        """
        coders_data = {}
        coders_files = list(self.silver_dir.rglob("coders_*.parquet"))
        
        for file in coders_files:
            name = '_'.join(file.stem.split('_')[:-1])
            try:
                coders_data[name] = pd.read_parquet(file)
            except Exception as e:
                logger.error(f"Failed to load {name}: {e}")
        return coders_data
        
    def get_ieso_generation(self) -> dict:
        """
        Loads the IESO hourly and monthly generation data.
        
        Returns:
            dict: {"hourly": DataFrame, "monthly": DataFrame}
        """
        ieso_data = {}
        hourly = list(self.silver_dir.rglob("ieso_hourly*.parquet"))
        if hourly:
            ieso_data["hourly"] = pd.read_parquet(hourly[0])
            
        monthly_files = list(self.silver_dir.rglob("ieso_monthly*.parquet"))
        if monthly_files:
            dfs = [pd.read_parquet(f) for f in monthly_files]
            ieso_data["monthly"] = pd.concat(dfs, ignore_index=True)
            
        return ieso_data

    def get_macro_indicators(self) -> dict:
        """
        Loads macroeconomic datasets (CER, StatCan, Statcan shares).
        
        Returns:
            dict: Dictionary containing DataFrames like "cer_macro", "statcan_17100009", "statcan_shares", etc.
        """
        macro = {}
        files = list(self.silver_dir.rglob("cer_macro*.parquet")) + \
                list(self.silver_dir.rglob("statcan_*.parquet"))
                
        for file in files:
            name = '_'.join(file.stem.split('_')[:-1])
            try:
                macro[name] = pd.read_parquet(file)
            except Exception as e:
                logger.error(f"Failed to load macro file {file.name}: {e}")
                
        return macro
        
    def get_nrcan_data(self) -> dict:
        """
        Loads all NRCAN aggregate, commercial, residential, and agricultural datasets.
        
        Returns:
            dict: Dictionary containing all NRCAN dataframes.
        """
        nrcan = {}
        files = list(self.silver_dir.rglob("nrcan_*.parquet"))
        for file in files:
            name = '_'.join(file.stem.split('_')[:-1])
            try:
                nrcan[name] = pd.read_parquet(file)
            except Exception as e:
                logger.error(f"Failed to load NRCAN file {file.name}: {e}")
        return nrcan
        
    def get_other_datasets(self) -> dict:
        """
        Loads ATB, EPA, and Renewables Ninja generation data.
        
        Returns:
            dict: Dictionary containing 'atb', 'epa', 'renewables_ninja', etc.
        """
        others = {}
        patterns = ["atb_*.parquet", "epa_*.parquet", "renewables_ninja_*.parquet", "rninja_weather_*.parquet"]
        files = []
        for p in patterns:
            files.extend(list(self.silver_dir.rglob(p)))
            
        for file in files:
            name = '_'.join(file.stem.split('_')[:-1])
            try:
                others[name] = pd.read_parquet(file)
            except Exception as e:
                logger.error(f"Failed to load other dataset {file.name}: {e}")
        return others

    def get_renewables_cache(self) -> dict:
        """
        Loads the renewables cache datasets.
        
        Returns:
            dict: Dictionary containing DataFrames for renewables capacity, cost, etc.
        """
        renewables = {}
        renewables_dir = self.cache_dir / "renewables"
        
        if renewables_dir.exists():
            files = list(renewables_dir.rglob("*.parquet"))
            for file in files:
                name = file.stem
                try:
                    renewables[name] = pd.read_parquet(file)
                except Exception as e:
                    logger.error(f"Failed to load renewables file {file.name}: {e}")
        else:
            logger.warning(f"Renewables cache directory {renewables_dir} not found. Run sync_from_drive() first.")
            
        return renewables

if __name__ == "__main__":
    # Example usage script for Gold Layer modelers
    connector = GoldConnector()
    
    # 1. Sync data from Google Drive (only downloads what's missing)
    connector.sync_from_drive()
    
    # 2. Load the specific datasets you need into DataFrames
    weather = connector.get_weather_matrices()
    buildings = connector.get_building_stock()
    coders = connector.get_coders_data()
    ieso = connector.get_ieso_generation()
    macro = connector.get_macro_indicators()
    nrcan = connector.get_nrcan_data()
    others = connector.get_other_datasets()
    renewables = connector.get_renewables_cache()
    
    logger.info("Successfully loaded all requested datasets into memory!")
    logger.info(f"Loaded {len(weather)} weather region matrices.")
    logger.info(f"Loaded {len(coders)} CODERS tables.")
    logger.info(f"Loaded {len(nrcan)} NRCAN tables.")
    logger.info(f"Loaded {len(renewables)} renewables cache tables.")
