"""Plan an isolated Gold model experiment, or prepare an existing plan."""
import argparse
from pathlib import Path
import json

from geocanoe.execution.experiment import create_plan, digest, verify_inputs, write_json, write_report
from geocanoe.paths import find_project_root
from prepare_national_gasoline_matrix import prepare, scenario_cases
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--config', type=Path, help='Recipe to plan; also supply --output.')
    action.add_argument('--prepare', type=Path, help='Existing experiment directory; never solves.')
    parser.add_argument('--output', type=Path, help='New directory inside the repository.')
    parser.add_argument('--dialog-prompts', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = find_project_root()
    if args.config:
        if args.output is None:
            parser.error('--config requires --output')
        recipe = root / args.config
        settings = tomllib.loads(recipe.read_text(encoding='utf-8'))
        plan = create_plan(root, recipe, root / args.output, list(scenario_cases(settings)))
        print(f"Planned {len(plan['cases'])} cases: {args.output / 'README.md'}")
        return
    if args.output:
        parser.error('--output is only used with --config')
    destination = (root / args.prepare).resolve()
    destination.relative_to(root)
    plan = json.loads((destination / 'plan.json').read_text(encoding='utf-8'))
    if (destination / 'preparation.json').exists():
        raise FileExistsError('Preparation already attempted; create a new experiment to retry.')
    verify_inputs(root, plan)
    if digest(destination / 'recipe.toml') != plan['prepared_recipe_sha256']:
        raise ValueError('Experiment recipe changed since planning; create a new plan.')
    record = {'status': 'preparing'}
    write_json(destination / 'preparation.json', record)
    try:
        inventory = prepare(destination / 'recipe.toml', build=True, **({'dialog_prompts': True} if args.dialog_prompts else {}))
        artifacts = {path for path in [destination / 'batch.toml', destination / 'inventory.json'] if path.exists()}
        for case in inventory:
            artifacts.update(root / case[key] for key in ('gold_database', 'gold_manifest', 'solver_config') if case.get(key))
        record.update(status='prepared', cases=inventory,
                      artifacts=[{'path': p.relative_to(root).as_posix(), 'sha256': digest(p)}
                                 for p in sorted(artifacts)])
    except Exception as error:
        record.update(status='failed', error=str(error))
        raise
    finally:
        record['decisions'] = [
            {'path': path.relative_to(root).as_posix(), 'sha256': digest(path),
             'responses': json.loads(path.read_text(encoding='utf-8'))}
            for path in sorted((destination / 'logs').glob('*.decisions.json'))
        ]
        write_json(destination / 'preparation.json', record)
        write_report(destination, plan, record)


if __name__ == '__main__':
    main()
