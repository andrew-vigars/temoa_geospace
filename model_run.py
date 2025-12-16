import pandas as pd
import numpy as np
import model_rules as mr
import db_update
import subprocess
import os
import sys
import db_mgmt as mgmt

def write_db(db_path, grid_res=5, etl_res=5):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    sites = pd.read_csv('data_files/sites_full.csv')
    co2 = pd.read_csv('data_files/co2.csv')
    transport_techs = pd.read_csv('data_files/transport_techs.csv')
    demand = pd.read_csv('data_files/demand.csv')
    gen_efficiencies = pd.read_csv('data_files/generation_efficiency.csv')
    technologies = pd.read_csv('data_files/techs.csv')
    commodity = pd.read_csv('data_files/commodities.csv')


    print("\nData loaded successfully.\n")

    ## Combine all regions
    df = mr.combine_sites(sites, demand, co2)

    sites_small = mr.group_sites(df, resolution=grid_res)

    site = mr.assign_neighbors(sites_small)
    site['co2_cost'] = 50

    print("Sites combined and grouped successfully.")
    site.to_csv('sites_dict.csv', index=False)
    site.to_csv(f'sites_dict_{grid_res}.csv', index=False)


    Efficiency = pd.DataFrame()
    for i in range(len(transport_techs)):
        tech = transport_techs.loc[i, 'tech']
        input_comm = transport_techs.loc[i, 'input_comm']
        output_comm = transport_techs.loc[i, 'output_comm']

        df = mr.efficiency_transport(site, tech, input_comm, output_comm)
        Efficiency = pd.concat([Efficiency, df], ignore_index=True)
        print(f'Efficiency for {tech} calculated successfully.')


    CostVariable = mr.var_cost_transport(Efficiency, transport_techs)
    CostVariable = mr.var_cost_production(CostVariable, site)
    GSL_PLANT_input_split = mr.input_split_fillout(site, 'GSL_PLANT', ['ch3oh', 'h2'], [0.997782705, 0.002217295], operator='ge', vintage=1, eff_value=1)
    METOH_PLANT_input_split = mr.input_split_fillout(site, 'METOH_PLANT', ['co2', 'h2', 'elc'], [ 0.79230333899, 0.10865874363, 0.09903791737 ], operator='ge', vintage=1, eff_value=1)

    LimitTechInputSplit = pd.concat([GSL_PLANT_input_split, METOH_PLANT_input_split], ignore_index=True)

    LimitCapacity = mr.set_limit_capacity(site)
    Demand = mr.set_demand(site) 

    df_gsl = mr.invest_costs(site, 23334, -0.4, 'GSL_PLANT', resolution=etl_res)
    df_met = mr.invest_costs(site, 4500, -0.3663, 'METOH_PLANT', resolution=etl_res)
    df_gas_pipe = mr.invest_costs(site, 4.5, -0.3, 'GSL_PIPE', resolution=etl_res, transp_tech=True) # dummy values for economies of scale of pipes
    df_co2_pipe = mr.invest_costs(site, 4.5, -0.3, 'CO2_PIPE', resolution=etl_res, transp_tech=True) # dummy values for economies of scale of pipes
    df_metoh_pipe = mr.invest_costs(site, 4.5, -0.3, 'METOH_PIPE', resolution=etl_res, transp_tech=True) # dummy values for economies of scale of pipes
    df_h2_pipe = mr.invest_costs(site, 4.5, -0.3, 'H2_PIPE', resolution=etl_res, transp_tech=True) # dummy values for economies of scale 
    df_elc_trans = mr.invest_costs(site, 1.5, -0.3, 'ELC_TRANS', resolution=etl_res, transp_tech=True) # dummy values for economies of scale of transmission lines
    
    df_elc = mr.fix_cost(site, 'ELC_GEN', 1000)
    df_co2 = mr.fix_cost(site, 'CO2_CAP', 1000)

    CostInvest = pd.concat([df_elc, df_co2], ignore_index=True)
    ETLSegment = pd.concat([df_gsl, df_met, df_gas_pipe, df_co2_pipe, df_metoh_pipe, df_h2_pipe, df_elc_trans], ignore_index=True)

    gen_eff = mr.gen_efficiency(site, gen_efficiencies)

    Efficiency = pd.concat([Efficiency, gen_eff], ignore_index=True)
    Efficiency = Efficiency.drop('distance', axis=1)

    Technology = mr.set_techs(technologies)
    TechnologyType, TimePeriod, SectorLabel, Dataset = mr.set_supporting_tables()
    Commodity = commodity.copy()


    data = {
        'Efficiency': Efficiency,
        'CostVariable': CostVariable,
        'LimitTechInputSplitAnnual': LimitTechInputSplit,
        'LimitCapacity': LimitCapacity,
        'Demand': Demand,
        'CostInvest': CostInvest,
        'ETLSegment': ETLSegment,
        'Region': pd.DataFrame({'region': site['site_id']}),
        'Technology': Technology,
        'TechnologyType': TechnologyType,
        'TimePeriod': TimePeriod,
        'SectorLabel': SectorLabel,
        'Commodity': Commodity,
        'DataSet': Dataset
        }


    with pd.ExcelWriter('data_files/CANOE_geospatial.xlsx') as writer:
        for key, df in sorted(data.items()):
            df.to_excel(writer, sheet_name=key, index=False)
            print(f'Written sheet: {key}')
        print('Data saved to CANOE_geospatial.xlsx successfully.')
    

    mgmt.update_sqlite(db_path, data)



