"""Both CLI exclusion decisions reach the UI, with exact audit snapshots."""
import io
import json
import sys

import pytest

from geocanoe.execution.gold_prompts import OUTLIER_PROMPT, PROMPT_PREFIX, run_build


def child_script(root):
    path = root / 'child.py'
    path.write_text('''import json, sys
from pathlib import Path
for label in ['Site points', 'CO2 facilities']:
    audit = Path('audit.csv')
    audit.write_text('dataset,distance\\n' + label + ',150\\n')
    print(label + ': 1 point exceeds snap limit', flush=True)
    print('Audit written to: audit.csv', flush=True)
    print('GOLD_INPUT_REQUEST=' + json.dumps({'prompt': 'Continue the schema build and exclude these outliers? [y/n]: '}), flush=True)
    answer = sys.stdin.readline().strip()
    if answer not in {'y', 'yes'}:
        sys.exit(3)
''', encoding='utf-8')
    return [sys.executable, '-u', str(path)]


def test_both_prompts_relayed_and_audits_copied(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('y\ny\n'))
    log = tmp_path / 'case.log'
    assert run_build(child_script(tmp_path), tmp_path, log, dialog=True) == 0
    prompts = [json.loads(line[len(PROMPT_PREFIX):]) for line in capsys.readouterr().out.splitlines()
               if line.startswith(PROMPT_PREFIX)]
    assert len(prompts) == 2
    assert 'Site points' in prompts[0]['context']
    assert 'CO2 facilities' in prompts[1]['context']
    decisions = json.loads(log.with_suffix('.decisions.json').read_text())
    assert [item['answer'] for item in decisions] == ['y', 'y']
    assert 'Site points' in (tmp_path / decisions[0]['audit_path']).read_text()
    assert 'CO2 facilities' in (tmp_path / decisions[1]['audit_path']).read_text()


def test_declining_stops_build_and_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('n\n'))
    log = tmp_path / 'case.log'
    assert run_build(child_script(tmp_path), tmp_path, log, dialog=True) == 3
    assert json.loads(log.with_suffix('.decisions.json').read_text())[0]['answer'] == 'n'


def test_missing_response_never_approves_exclusions(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    with pytest.raises(ValueError, match='explicit yes or no'):
        run_build(child_script(tmp_path), tmp_path, tmp_path / 'case.log', dialog=True)


def test_auto_exclude_accepts_both_outlier_prompts_and_records_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    log = tmp_path / 'case.log'
    assert run_build(child_script(tmp_path), tmp_path, log, dialog=True, auto_exclude=True) == 0
    assert not any(line.startswith(PROMPT_PREFIX) for line in capsys.readouterr().out.splitlines())
    decisions = json.loads(log.with_suffix('.decisions.json').read_text())
    assert len(decisions) == 2
    assert [decision['answer'] for decision in decisions] == ['y', 'y']
    assert all(decision['decision_source'] == 'auto_exclude_all' for decision in decisions)
    assert 'Site points' in (tmp_path / decisions[0]['audit_path']).read_text()
    assert 'CO2 facilities' in (tmp_path / decisions[1]['audit_path']).read_text()


def test_auto_exclude_does_not_accept_unrelated_prompts(tmp_path, monkeypatch):
    command = child_script(tmp_path)
    from pathlib import Path
    script = Path(command[-1])
    script.write_text(script.read_text(encoding='utf-8').replace(OUTLIER_PROMPT, 'Overwrite existing files? [y/n]: '), encoding='utf-8')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('n\n'))
    log = tmp_path / 'case.log'
    assert run_build(command, tmp_path, log, dialog=True, auto_exclude=True) == 3
    decisions = json.loads(log.with_suffix('.decisions.json').read_text())
    assert decisions[0]['answer'] == 'n'
    assert decisions[0]['decision_source'] == 'interactive'
