"""Translate the Gold configurator's human-facing fields into matrix recipes."""
from __future__ import annotations

from copy import deepcopy
import math
import re


def validate_model_structure(document, defaults, *, allow_scenario=False):
    """Reject unrelated TOMLs before they can contaminate a scenario template."""
    if not isinstance(document, dict) or not document:
        raise ValueError('Choose registry/model.toml, a scenario overlay, or an experiment recipe.')
    allowed = set(defaults) | ({'scenario'} if allow_scenario else set())
    unknown = set(document) - allowed
    if unknown:
        hint = ('This appears to be an economic conversion configuration. '
                if {'target_currency', 'exchange_path', 'index_path'} & unknown else '')
        raise ValueError(
            hint + 'This file is not a compatible Gold model or scenario. '
            f'Unsupported model sections: {sorted(unknown)}. '
            'Choose registry/model.toml, a registry/scenarios overlay, or a model-based experiment recipe.'
        )
    def check(table, reference, path):
        if not isinstance(table, dict):
            raise ValueError(f'{path} must be a TOML table.')
        unknown_keys = set(table) - set(reference)
        if unknown_keys:
            raise ValueError(f'Unsupported settings in {path}: {sorted(unknown_keys)}')
        for key, value in table.items():
            if isinstance(reference[key], dict):
                check(value, reference[key], f'{path}.{key}')
    for section, table in document.items():
        check(table, {'id': '', 'description': ''} if section == 'scenario' else defaults[section], section)


def numbers(text, label, *, empty=False):
    try:
        result = [float(item.strip()) for item in text.split(',') if item.strip()]
    except ValueError as error:
        raise ValueError(f'{label}: enter numbers separated by commas.') from error
    if not result and not empty:
        raise ValueError(f'{label}: enter at least one number.')
    if any(not math.isfinite(value) for value in result):
        raise ValueError(f'{label}: values must be finite.')
    if len(set(result)) != len(result):
        raise ValueError(f'{label}: remove duplicate values.')
    return result


def integer(text, label, minimum=1):
    try:
        value = int(text)
    except ValueError as error:
        raise ValueError(f'{label}: enter a whole number.') from error
    if value < minimum:
        raise ValueError(f'{label}: minimum is {minimum}.')
    return value


def recipe_from_form(values, template):
    """Mirror model.toml; comma-separated numeric fields become sensitivity axes."""
    result = deepcopy(template)
    model = result['model']
    name = values['name'].strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('Experiment name: use 1–80 letters, digits, underscores or hyphens.')
    result['matrix'].update(name=name, build_profile=values['build_profile'].strip(), interaction='prompt',
                            defer_temoa_configs=True)
    auto_exclude = values.get('auto_exclude_outliers', False)
    if type(auto_exclude) is not bool:
        raise ValueError('Auto-exclude outliers must be true or false.')
    result['matrix']['auto_exclude_outliers'] = auto_exclude
    result['matrix'].pop('confirmed_co2_outlier_facility_ids', None)
    axes = {}
    single = values['mode'] == 'Single scenario'
    def assign(path, options):
        if single and len(options) != 1:
            raise ValueError(f'Single scenario: choose one value for {path}.')
        section = model
        parts = path.split('.')
        for part in parts[:-1]:
            section = section[part]
        section[parts[-1]] = options[0]
        if len(options) > 1:
            axes[path] = options
    fields = {
        'shares': ('synthetic_gasoline.minimum_national_share', 100, 0, 100),
        'prices': ('emissions.price_per_tonne', 1, 0, None),
        'storage': ('storage.point_source_fraction', 100, 0, 100),
        'cumulative': ('storage.minimum_cumulative_activity', 1, 0, None),
        'resolution': ('basemap.resolution', 1, 0, None),
        'fallback': ('emissions.fallback_gasoline_emission_factor', 1, 0, None),
        'global_discount_rate': ('finance.global_discount_rate', 100, 0, 100),
        'default_loan_rate': ('finance.default_loan_rate', 100, 0, 100),
    }
    for key, (path, scale, low, high) in fields.items():
        active_policy_field = {
            'prices': 'emissions_price' in values['emissions_policy'],
            'fallback': 'emissions_price' in values['emissions_policy'],
            'storage': 'minimum_point_source_fraction' in values['storage_requirement'],
            'cumulative': 'minimum_cumulative_activity' in values['storage_requirement'],
        }.get(key, True)
        if not active_policy_field:
            assign(path, [0])
            continue
        options = numbers(values[key], path)
        if any(v < low or (high is not None and v > high) for v in options):
            raise ValueError(f'{path}: values are outside the supported range.')
        if key in {'resolution', 'prices', 'storage', 'cumulative'}:
            if any(v <= 0 for v in options):
                raise ValueError(f'{path}: active values must be positive.')
        if key in {'global_discount_rate', 'default_loan_rate'} and any(v >= 100 for v in options):
            raise ValueError(f'{path}: rates must be less than 100%.')
        assign(path, [v / scale for v in options])
    for key, path in [('emissions_policy', 'emissions.policy'), ('storage_requirement', 'storage.requirement'),
                      ('grid_type', 'basemap.grid_type'), ('projection', 'emissions.projection_method'),
                      ('impedance', 'pipeline_costs.impedance_scope')]:
        assign(path, [model['pipeline_costs']['impedance_scope']] if key == 'impedance' and not values['pipelines']
               else values[key].split(' + '))
    start, end = integer(values['start'], 'Start year'), integer(values['end'], 'End year')
    if end <= start:
        raise ValueError('End year must be after start year.')
    model['time'].update(start_year=start, end_year=end)
    model['transport_modes'].update(roads_enabled=values['roads'], pipelines_enabled=values['pipelines'],
                                   disabled_technologies=[s.strip() for s in values['disabled'].split(',') if s.strip()])
    for tech in ('co2', 'gsl', 'h2', 'metoh'):
        key = f'{tech}_pipeline_eos_stack_count'
        path = f'pipeline_costs.eos_stack_counts.{tech.upper()}_PIPE'
        assign(path, [integer(s.strip(), key) for s in values[key].split(',')] if values['pipelines']
               else [model['pipeline_costs']['eos_stack_counts'][f'{tech.upper()}_PIPE']])
    result['axes'] = axes
    result['batch'].update({key: values[key] for key in ('continue_on_failure', 'skip_completed', 'retry_failed')})
    return result
