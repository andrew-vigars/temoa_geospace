"""Expand model.toml settings into validated Gold scenario documents."""
from copy import deepcopy
import itertools
import json
import math
import re


def model_cases(settings):
    axes = settings.get('axes', {})
    count = math.prod(len(values) for values in axes.values())
    if not count or count > 10000:
        raise ValueError('Choose between 1 and 10,000 matrix combinations.')
    prefix = re.sub('[^a-z0-9]+', '-', settings['matrix']['name'].lower()).strip('-')[:14]
    if not prefix:
        raise ValueError('Experiment name must contain letters or digits.')
    seen = set()
    for values in itertools.product(*axes.values()):
        doc = deepcopy(settings['model'])
        doc.pop('scenario', None)
        for path, value in zip(axes, values):
            section = doc
            parts = path.split('.')
            for part in parts[:-1]:
                section = section[part]
            if parts[-1] not in section:
                raise ValueError(f'Unknown model setting: {path}')
            section[parts[-1]] = value
        # Inactive policy quantities must be zero, as in model.toml's contract.
        if doc['emissions']['policy'] == 'none':
            doc['emissions']['price_per_tonne'] = 0
            doc['emissions']['fallback_gasoline_emission_factor'] = 0
        requirement = doc['storage']['requirement']
        if requirement != 'minimum_point_source_fraction':
            doc['storage']['point_source_fraction'] = 0
        if requirement != 'minimum_cumulative_activity':
            doc['storage']['minimum_cumulative_activity'] = 0
        identity = json.dumps(doc, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        scenario_id = f'{prefix}-{len(seen):04d}'
        doc['scenario'] = {'id': scenario_id, 'description': f"{settings['matrix']['name']}: case {len(seen)}"}
        yield {'stem': scenario_id, 'document': doc,
               'share': doc['synthetic_gasoline']['minimum_national_share'],
               'policy': doc['emissions']['policy'], 'price': doc['emissions']['price_per_tonne'],
               'storage_requirement': requirement,
               'storage_fraction': doc['storage']['point_source_fraction'],
               'storage_cumulative': doc['storage']['minimum_cumulative_activity']}
