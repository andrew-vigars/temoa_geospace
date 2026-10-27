from pathlib import Path
import csv
import hashlib
import json
import re
from pypdf import PdfReader

folder = Path('data_files/models/cost_models/transmission/miso')
pdf = folder / 'MISO_MTEP24_2024-05-01.pdf'
reader = PdfReader(pdf)
pages = [' '.join(p.extract_text().split()) for p in reader.pages]
volts = [69, 115, 138, 161, 230, 345, 500, 765]
states = ['Arkansas', 'Illinois', 'Indiana', 'Iowa', 'Kentucky', 'Louisiana',
          'Michigan', 'Minnesota', 'Mississippi', 'Missouri', 'Montana',
          'North Dakota', 'South Dakota', 'Texas', 'Wisconsin']

def write(name, rows):
    with (folder / name).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

rows = []
for index, circuits, table, n in [(38, 1, '4.1-1', 8), (39, 2, '4.1-2', 6)]:
    text = pages[index].split('Table ' + table + ':', 1)[1]
    if circuits == 2:
        text = text.split('Table 4.1-3:', 1)[0]
    for state in states:
        match = re.search(re.escape(state) + r'\s+((?:\$[\d.]+M\s*){' + str(n) + r'})', text)
        if not match:
            raise ValueError((table, state))
        values = [float(x) * 1e6 for x in re.findall(r'\$([\d.]+)M', match[1])]
        assert len(values) == n
        for voltage, value in zip(volts, values):
            rows.append(dict(state=state, voltage_kv=voltage, circuits=circuits,
                             cost_usd_per_mile=value, source_table=table,
                             source_pdf_page=index + 1, source_printed_page=index,
                             cost_scope='exploratory new AC line including 30% contingency and 7.5% AFUDC'))
assert len(rows) == 210
write('ac_line_costs.csv', rows)

# Table 3.1-5 is visually verified against PDF page 34, printed p. 33.
ratings = []
for voltage, size, kind, bundle, amps, mva, row_ft in zip(
    volts, [477,795,795,795,795,795,954,795],
    ['ACSS']*6 + ['ACSR']*2, [1,1,1,1,1,2,3,6],
    [1175,1650,1650,1650,1650,3000,3000,5000],
    [140,329,394,460,657,1792,2598,6625], [80,90,95,100,125,175,200,225]):
    ratings.append(dict(voltage_kv=voltage, conductor_kcmil=size, conductor_type=kind,
        subconductors_per_phase=bundle, circuit_current_a=amps,
        capacity_mva_per_circuit=mva, row_width_ft=row_ft,
        source_table='3.1-5; ROW from 3.1-1', source_pdf_page=34,
        source_printed_page=33, rating_basis='MISO indicative per-circuit rating; weather/temperature not specified here'))
write('ac_design_ratings.csv', ratings)

terrain = []
for state in states:
    m = re.search(re.escape(state) + r'\s+((?:\d+%\s*){6})', pages[34])
    if not m:
        raise ValueError(('3.1-7', state))
    fractions = [int(x)/100 for x in re.findall(r'(\d+)%', m[1])]
    assert abs(sum(fractions[:3])-1) < 1e-9 and abs(sum(fractions[3:])-1) < 1e-9
    terrain.append(dict(state=state, **dict(zip(
        ['pasture_fraction','cropland_fraction','urban_fraction','level_fraction','forest_fraction','wetland_fraction'],
        fractions)), source_table='3.1-7', source_pdf_page=35, source_printed_page=34))
write('state_land_terrain.csv', terrain)

station_rows = []
text = pages[41]
blocks = [
    ('4.2-1', 'upgrade', 1, 'ring', 'Add 1 position (ring bus)'),
    ('4.2-1', 'upgrade', 1, 'breaker_and_half', 'Add 1 position (breaker-and-a-half bus)'),
    ('4.2-1', 'upgrade', 1, 'double_breaker', 'Add 1 position (double-breaker bus)'),
    ('4.2-1', 'upgrade', 2, 'ring', 'Add 2 positions(ring bus)'),
    ('4.2-1', 'upgrade', 2, 'breaker_and_half', 'Add 2 positions (breaker-and-a-half bus)'),
    ('4.2-1', 'upgrade', 2, 'double_breaker', 'Add 2 positions (double-breaker bus)'),
    ('4.2-2', 'new', 4, 'ring', '4 positions (ring bus)'),
    ('4.2-2', 'new', 4, 'breaker_and_half', '4 positions (breaker-and-a-half bus)'),
    ('4.2-2', 'new', 4, 'double_breaker', '4 positions (double-breaker bus)'),
    ('4.2-2', 'new', 6, 'ring', '6 positions (ring bus)'),
    ('4.2-2', 'new', 6, 'breaker_and_half', '6 positions (breaker-and-a-half bus)'),
    ('4.2-2', 'new', 6, 'double_breaker', '6 positions (double-breaker bus)'),
]
# Normalize word-wrap hyphens in station arrangements, not prices.
text = text.replace('breaker-and-a- half', 'breaker-and-a-half')
for table, scope, positions, arrangement, label in blocks:
    section = text.split('Table '+table+':',1)[1]
    if table == '4.2-1':
        section = section.split('Table 4.2-2:',1)[0]
    m = re.search(re.escape(label) + r'\s+((?:\$[\d.]+M\s*){8})', section)
    if not m:
        raise ValueError(label)
    values = [float(x)*1e6 for x in re.findall(r'\$([\d.]+)M',m[1])]
    for voltage,value in zip(volts,values):
        station_rows.append(dict(scope=scope, positions=positions, bus_arrangement=arrangement,
            voltage_kv=voltage, cost_usd_per_station=value, source_table=table,
            source_pdf_page=42, source_printed_page=41,
            cost_scope='exploratory station including 30% contingency and 7.5% AFUDC'))
assert len(station_rows)==96
write('ac_station_costs.csv',station_rows)

write('terrain_adders.csv', [dict(terrain=kind, usd_per_acre=value,
    source_table='2.2-9', source_pdf_page=19, source_printed_page=18,
    cost_scope='additional structure installation; do not add wholesale to state exploratory costs')
    for kind,value in [('forest_clearing',5995),('wetland_matting',69975),
                       ('wetland_mitigation',56132),('mountainous',7794)]])
files = ['ac_line_costs.csv','ac_design_ratings.csv','state_land_terrain.csv',
         'ac_station_costs.csv','terrain_adders.csv']
manifest = {
    'publisher':'MISO', 'edition':'MTEP24', 'publication_date':'2024-05-01',
    'url':'https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf',
    'pdf_file':pdf.name,'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
    'local_snapshot_date':'2026-10-27', 'draft_used_for_prices':False,
    'extraction':'Line, station and state tables parsed from clean PDF text with expected row counts; rating and terrain-adder values transcribed and relevant pages visually checked.',
    'price_year_working_interpretation':2024,
    'price_year_ambiguity':'Printed p. 1 says all costs are 2023 USD, then says escalated from 2023 to 2024 by 5%. Working interpretation is 2024; retain 2023 sensitivity.',
    'price_year_support_url':'https://cdn.misoenergy.org/20240131%20PSC%20Item%2005%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24631525.pdf',
    'price_year_support':'Official January 2024 update presentation: unit costs escalated to 2024 dollars.',
    'sample_unit':'engineered state/design scenario, not actual independent project',
    'distance_basis_assumption':'$/mile for a single/double-circuit line interpreted as route-mile; no extra circuit multiplier on line cost',
    'capacity_basis':'MISO rounded MVA per circuit; double-circuit total is analyst sum of two circuits, not firm or N-1 capacity',
    'files':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in files},
}
(folder/'source_provenance.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
print('Extracted 210 line scenarios, 8 ratings, 15 state assumptions, 96 station options, 4 terrain adders.')
