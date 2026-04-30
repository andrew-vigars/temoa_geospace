import numpy as np
import pandas as pd
import sqlite3
import os
from datetime import datetime
import shutil
import subprocess
from pathlib import Path
import re
from db_mgmt import update_database_from_excel, update_db_paths, convert_sql_to_sqlite
import model_run as mod_run


main_path = os.path.join('temoa/', "main.py")

output_path = 'output_files'
output_dir = f'{output_path}/{datetime.today().strftime('%Y-%m-%d %H%M')}'
config_path = 'temoa/data_files/my_configs/config_sample.toml'

sql_file_path = 'data_files/canoe_dataset_schema.sql'
db_path= f'data_files/CANOE_geospatial.sqlite'

os.remove(db_path) if os.path.exists(db_path) else None
convert_sql_to_sqlite(sql_file_path, db_path)
# write data base
grid_res = 1
etl_res = 5
mod_run.write_db(db_path, grid_res=grid_res, etl_res=etl_res)

update_db_paths(config_path, db_path, False) 


if not os.path.exists(output_dir):
    os.makedirs(output_dir)

shutil.copy2(db_path, output_dir)
subprocess.run(["python", main_path, "--config", config_path, "-o", output_dir])
shutil.copy2(db_path, output_dir)

