import pandas as pd
import numpy as np
import math
from geopy.distance import distance
from typing import List
from math import ceil


def combine_sites(sites, demand, co2):
    df = pd.concat((sites, demand, co2), ignore_index=True)
    df = df.fillna(0)
    return df


def group_sites(sites: pd.DataFrame, round=0, resolution=None) -> pd.DataFrame:
    df = sites.copy()
    if resolution is not None:
        df['lon'] = (df['lon'] / resolution).round().astype(int) * resolution
        df['lat'] = (df['lat'] / resolution).round().astype(int) * resolution
    else:
        df['lon'] = df['lon'].round(round)
        df['lat'] = df['lat'].round(round)
    df['max_elc'] = df['max_elec']
    df['co2'] = df['CO2']
    # Group and aggregate
    grouped = df.groupby(['lon', 'lat']).agg(
        LCOE=('LCOE', 'mean'),
        max_elc=('max_elc', 'sum'),
        demand=('demand', 'sum'),
        co2=("CO2", 'sum')
    ).reset_index()

    return grouped

def assign_neighbors(sites: pd.DataFrame) -> pd.DataFrame:
    df = sites.copy()

    # Assign unique region IDs
    df = df.reset_index(drop=True)
    df['region'] = [f"R{i}" for i in df.index]

    # df.reset_index(inplace=True)
    lon = df.sort_values(by=['lon'])['lon'].unique().tolist()
    lat = df.sort_values(by=['lat'])['lat'].unique().tolist()
    right_id = {}
    left_id = {}
    for l in lat:
        regions = df[df['lat'] == l]['region'].tolist()
        if len(regions) > 1:
            for i in range(len(regions) - 1):
                right_id[regions[i]] = regions[i+1]
                left_id[regions[i+1]] = regions[i]

    up_id = {}
    down_id = {}
    for l in lon:
        regions = df[df['lon'] == l]['region'].tolist()
        if len(regions) > 1:
            for i in range(len(regions) - 1):
                up_id[regions[i]] = regions[i+1]
                down_id[regions[i+1]] = regions[i]


    df['up_id'] = df['region'].map(up_id)
    df['down_id'] = df['region'].map(down_id)
    df['right_id'] = df['region'].map(right_id)
    df['left_id'] = df['region'].map(left_id)

    # Fill missing with placeholder
    df.fillna('R-999', inplace=True)

    # Compute distances
    directions = ['right', 'left', 'up', 'down']
    for direction in directions:
        df[f'{direction}_distance'] = np.nan
        for i, row in df.iterrows():
            neighbor_id = row[f'{direction}_id']
            if is_valid(neighbor_id):
                coord1 = (row['lat'], row['lon'])
                neighbor_row = df[df['region'] == neighbor_id]
                if not neighbor_row.empty:
                    coord2 = (neighbor_row['lat'].values[0], neighbor_row['lon'].values[0])
                    df.at[i, f'{direction}_distance'] = distance(coord1, coord2).km

    print("Neighbors assigned successfully.")
    df['site_id'] = df['region']
    return df

def is_valid(value) -> bool:
    """
    Checks whether a value is valid (not NaN, None, or placeholder).

    Parameters:
    -----------
    value : any
        Value to validate.

    Returns:
    --------
    bool
        True if value is valid, False otherwise.
    """
    return (
        value is not None
        and not (isinstance(value, float) and math.isnan(value))
        and value != ''
        and value != 'R-999'
    )


def efficiency_transport(
    site: pd.DataFrame,
    tech: str,
    input_comm: str,
    output_comm: str,
    vintage: int = 1,
    eff_value: float = 1
) -> pd.DataFrame:
    """
    Constructs a DataFrame of transport links between neighboring regions with associated efficiency.

    Parameters:
    -----------
    site : pd.DataFrame
        DataFrame from `assign_neighbors` with neighbor IDs and distances.
    tech : str
        Technology name used for transport.
    input_comm : str
        Input commodity.
    output_comm : str
        Output commodity.
    vintage : int, default=1
        Model vintage (typically 1 for static models).
    eff_value : float, default=1
        Efficiency value of the transport technology.

    Returns:
    --------
    pd.DataFrame
        DataFrame with region-to-region transport links, distances, and efficiency values.
    """
    directions = ['right', 'left', 'up', 'down']

    regions = [
        f"R{i}-{site.loc[i, f'{direction}_id']}"
        for i in range(len(site))
        for direction in directions
        if is_valid(site.loc[i, f'{direction}_id'])
    ]
    distances = [
        site.loc[i, f'{direction}_distance']
        for i in range(len(site))
        for direction in directions
        if is_valid(site.loc[i, f'{direction}_distance'])
    ]

    return pd.DataFrame({
        'region': regions,
        'input_comm': input_comm,
        'tech': tech,
        'output_comm': output_comm,
        'vintage': vintage,
        'efficiency': eff_value,
        'distance': distances,
        # 'data_id': 'GEO001'
    })


def var_cost_transport(
    transp_site_eff_df: pd.DataFrame,
    transport_techs: pd.DataFrame
) -> pd.DataFrame:
    """
    Computes variable transport cost for each region-tech pair based on distance.

    Parameters:
    -----------
    transp_site_eff_df : pd.DataFrame
        DataFrame containing region-to-region transport links with distances.
    transport_techs : pd.DataFrame
        DataFrame with cost parameters: ['tech', 'cost_per_km', 'intercept_cost_per_km']

    Returns:
    --------
    pd.DataFrame
        DataFrame with transport cost per region-tech, including metadata (period, vintage, units).
    """
    df = transp_site_eff_df.copy()
    avg_distance = df.groupby(['region', 'tech'], as_index=False)['distance'].mean()

    # Merge cost parameters
    cost_params = transport_techs.set_index('tech')
    avg_distance['cost'] = (
        avg_distance['distance'] * cost_params.loc[avg_distance['tech'], 'cost_per_km'].values +
        cost_params.loc[avg_distance['tech'], 'intercept_cost_per_km'].values
    )

    avg_distance['period'] = 1
    avg_distance['vintage'] = 1
    avg_distance['units'] = "M$/unit"
    avg_distance['data_id'] = "GEO001"
    avg_distance = avg_distance.loc[avg_distance['cost']>0]
    avg_distance.reset_index(inplace=True)
    return avg_distance[['region', 'tech', 'cost', 'period', 'vintage', 'units', 'data_id']]


def var_cost_production(var_cost, site):

    elc_cost = pd.DataFrame()
    elc_cost['region'] = site['site_id']
    elc_cost['tech'] = 'ELC_GEN'
    elc_cost['vintage'] = 1
    elc_cost['cost'] = site['LCOE']
    elc_cost['period'] = 1
    elc_cost['units'] = 'M$/MWh'
    elc_cost['data_id'] = 'GEO001'

    co2_cost = pd.DataFrame()
    co2_cost['region'] = site['site_id']
    co2_cost['tech'] = 'CO2_CAP'
    co2_cost['vintage'] = 1
    co2_cost['cost'] = site['co2_cost']
    co2_cost['period'] = 1
    co2_cost['units'] = 'M$/t'
    co2_cost['data_id'] = 'GEO001'

    gsl_backup_cost = pd.DataFrame()
    gsl_backup_cost['region'] = site['site_id']
    gsl_backup_cost['tech'] = 'GSL_BACKUP'
    gsl_backup_cost['vintage'] = 1
    gsl_backup_cost['cost'] = 500000
    gsl_backup_cost['period'] = 1
    gsl_backup_cost['units'] = 'M$/MWh'
    gsl_backup_cost['data_id'] = 'GEO001'


    df = pd.concat([var_cost, elc_cost, co2_cost, gsl_backup_cost], ignore_index=True)

    return df

def input_split_fillout(
    site: List[str],
    tech: str,
    input_comm: List[str],
    proportion: List[float],
    operator: str = 'ge',
    vintage: int = 1,
    eff_value: float = 1
    ) -> pd.DataFrame:
    """
    Generates a DataFrame that maps input commodities and proportions to regions and technologies.

    Parameters:
    -----------
    site : List[str]
        List of site identifiers (usually region codes like 'R0', 'R1', etc.).
    tech : str
        Technology name associated with the input.
    input_comm : List[str]
        List of input commodities.
    proportion : List[float]
        List of proportions corresponding to input_comm.
    operator : str, default='ge'
        Mathematical operator for constraints (e.g., 'ge', 'eq').
    vintage : int, default=1
        Model vintage.
    eff_value : float, default=1
        Efficiency value (currently unused).

    Returns:
    --------
    pd.DataFrame
        Structured DataFrame for model input splitting.
    """
    if len(input_comm) != len(proportion):
        raise ValueError("Length of input_comm and proportion must be the same.")

    num_sites = len(site)
    num_inputs = len(input_comm)

    return pd.DataFrame({
        'region': [f'R{n}' for n in range(num_sites)] * num_inputs,
        'period': [1] * (num_sites * num_inputs),
        'input_comm': sum([[input_comm[i]] * num_sites for i in range(num_inputs)], []),
        'tech': [tech] * (num_sites * num_inputs),
        'operator': [operator] * (num_sites * num_inputs),
        'proportion': sum([[proportion[i]] * num_sites for i in range(num_inputs)], []),
        'data_id': 'GEO001'
    })



def set_limit_capacity(site_max_cap):
    
    co2 = site_max_cap.copy()
    # co2 = co2.loc[co2.co2!=0, :]

    elc = site_max_cap.copy()
    # elc = elc.loc[elc.max_elc!=0, :]

    caps = [co2, elc]
    cols = ['co2', 'max_elc']
    tech_name = ['CO2_CAP', 'ELC_GEN']

    regions = []
    techs = []
    capacities = []

    for i in range(len(caps)):
        cap = caps[i]
        tech = tech_name[i]
        col = cols[i]
        regions += cap['site_id'].tolist()
        techs += [tech] * len(cap)
        capacities += cap[col].tolist()
    
    df = pd.DataFrame({
        'region': regions,
        'period': 1,
        'tech_or_group': techs,
        'operator': 'le',
        'capacity': capacities,
        'data_id': 'GEO001'
    })
    
    return df

def set_demand(demand):
    df_demand = demand.loc[demand['demand'] > 0].copy()

    df = pd.DataFrame()
    df['region'] = df_demand['site_id']
    df['period'] = 1
    df['commodity'] = 'd_gsl'
    df['demand'] = df_demand['demand']
    df['data_id'] = 'GEO001'

    return df

def fix_cost(site, tech, capital_cost):
    df = pd.DataFrame()
    df['region'] = site['site_id']
    df['tech'] = tech
    df['vintage'] = 1
    df['cost'] = capital_cost
    df['data_id'] = 'GEO001'

    return df

def gen_efficiency(site, gen_efficiencies):
    Efficiency = pd.DataFrame()

    for i in range(len(gen_efficiencies)):
        tech = gen_efficiencies.loc[i, 'tech']
        input_comm = gen_efficiencies.loc[i, 'input_comm']
        output_comm = gen_efficiencies.loc[i, 'output_comm']
        eff = gen_efficiencies.loc[i, 'efficiency']

        if tech =='GSL_BACKUP':

            df = pd.DataFrame({
                'region': site.loc[site['demand']>0, 'site_id'],
                'tech': tech,
                'input_comm': input_comm,
                'output_comm': output_comm,
                'efficiency': eff,
                'data_id': 'GEO001'
            })
        
        else:
            df = pd.DataFrame({
                'region': site['site_id'],
                'tech': tech,
                'input_comm': input_comm,
                'output_comm': output_comm,
                'efficiency': eff,
                'data_id': 'GEO001'
            })

        Efficiency = pd.concat([Efficiency, df], ignore_index=True)

    df_demand = set_demand(site)
    df = pd.DataFrame({
        'region': df_demand['region'],
        'tech': 'GSL_DEMAND',
        'input_comm': "gsl",
        'output_comm': "d_gsl",
        'efficiency': 1.0,
        'data_id': 'GEO001'
    })
    
    
    Efficiency = pd.concat([Efficiency, df], ignore_index=True)
    Efficiency['vintage'] = 1

    return Efficiency

def invest_costs(site, a, b, tech, resolution=5, upper_vol=1_000_000, spacing="log", transp_tech=False):
    """
    Build ETL rows with segment edges determined by `resolution` points in [0, upper_vol].
    - spacing="log": logarithmic spacing of capacity points (edges), including 0 and upper_vol.
    - resolution: number of edge points; segments = resolution - 1
    """
    if resolution < 2:
        raise ValueError("resolution must be at least 2 (need at least [0, upper_vol]).")
    if upper_vol <= 0:
        raise ValueError("upper_vol must be > 0.")
    if b <= -1:
        raise ValueError("b must be > -1 so that F(q) is finite.")

    # --- Build capacity edges ---
    if spacing == "log":
        # logspace trick: logspace(0,1,n) -> [1, ..., 10]; subtract 1 -> [0, ..., 9]
        # scale so last point is exactly upper_vol
        edges = upper_vol * (np.logspace(0, 1, resolution) - 1.0) / 9.0
        edges[0] = 0.0
        edges[-1] = float(upper_vol)
    elif spacing == "linear":
        edges = np.linspace(0.0, float(upper_vol), resolution)
    else:
        raise ValueError("spacing must be 'log' or 'linear'.")

    # Monotone safety
    if not np.all(np.diff(edges) > 0):
        raise ValueError("Capacity edges must be strictly increasing.")

    # --- Segments from edges ---
    cap_lower = edges[:-1]
    cap_upper = edges[1:]

    # Antiderivative of marginal cost m(q)=a*q^b
    F = lambda q: (a / (b + 1.0)) * (q ** (b + 1.0))

    cost_lower = F(cap_lower)
    cost_upper = F(cap_upper)

    df = pd.DataFrame({
        "tech_or_group": tech,
        "cap_lower": cap_lower,
        "cap_upper": cap_upper,
        "segment_cap": cap_upper - cap_lower,
        "cost_lower": cost_lower,
        "cost_upper": cost_upper,
    })

    df["seg_cost"] = df["cost_upper"] - df["cost_lower"]
    df["cum_cap"]  = df["cap_upper"]
    df["cum_cost"] = df["cost_upper"]
    df["segment"]  = np.arange(len(df))

    if transp_tech:
        neighbors = site[['up_id', 'down_id', 'right_id', 'left_id']].values.flatten().tolist()
        site_trans = pd.DataFrame()
        site_trans['region_to'] = neighbors
        site_trans['region'] = np.repeat([f'R{i}' for i in range(int(len(site_trans)/4))], 4)[:len(site_trans)]
        site_trans = site_trans.loc[site_trans['region_to'] != 'R-999']
        site_trans.reset_index(inplace=True)
        site_trans['region_from-to'] = [f'{site_trans.loc[i,'region']}-{site_trans.loc[i, 'region_to']}' for i in range(len(site_trans))]
        etl = (
            df.assign(key=1)
            .merge(pd.DataFrame({"region": site_trans['region_from-to'], "key": 1}), on="key")
            .drop(columns="key")
        )
        etl["data_id"] = "GEO001"
        etl = etl.loc[:, ['region', 'tech_or_group', 'segment', 'cap_lower', 'cap_upper', 'cost_lower', 'cost_upper', 'data_id']].copy()
        return etl


    else:
        # Cross-join with regions in `site`
        site_ids = pd.Index(site["site_id"])
        etl = (
            df.assign(key=1)
            .merge(pd.DataFrame({"region": site_ids, "key": 1}), on="key")
            .drop(columns="key")
        )
        etl["data_id"] = "GEO001"
        etl = etl.loc[:, ['region', 'tech_or_group', 'segment', 'cap_lower', 'cap_upper', 'cost_lower', 'cost_upper', 'data_id']].copy()
        return etl

    


def set_techs(technologies):
    df = technologies.copy()
    df['sector'] = 'insdustrial'
    df['reserve'] = 0
    df['curtail'] = 0
    df['retire'] = 0
    df['flex'] = 0
    df['data_id'] = 'GEO001'

    return df

def set_supporting_tables(): 
    TechnologyType = pd.DataFrame({'label': ['p','t'], 'description': ['production','transport']})
    TimePeriod = pd.DataFrame({'sequence': [1,2], 'period': [1,2], 'flag': ['f','f']})
    SectorLabel = pd.DataFrame({'sector': ['insdustrial'], 'notes': ['industrial sector']})
    DataSet = pd.DataFrame({'data_id': ['GEO001'], 
                            'label': ['Geospatial Renewable Gas Data'], 
                            'version': ['O001'], 
                            'description': ['Geospatial data for renewable gas model'], 
                            'status': ['active'], 
                            'author': ['Thiago A. Rodrigues'], 
                            'date': ['2025-10-22'],
                            'parent_id': [None],
                            'changelog': [None],
                            'notes': [None]}
                            )
    

    return TechnologyType, TimePeriod, SectorLabel, DataSet