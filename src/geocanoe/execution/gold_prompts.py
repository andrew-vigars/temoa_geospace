"""Relay Gold's existing CLI input decisions to a UI or terminal, with audits."""
from collections import deque
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROMPT_PREFIX = 'GOLD_INPUT_REQUEST='
OUTLIER_PROMPT = 'Continue the schema build and exclude these outliers? [y/n]: '


def request_input(prompt):
    print(PROMPT_PREFIX + json.dumps({'prompt': prompt}), flush=True)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('No response supplied to the Gold build decision.')
    return answer.strip()


def run_build(command, root, log_path, *, dialog=False, auto_exclude=False):
    """Forward every prompt, including separate site and CO2 exclusion prompts."""
    context = deque(maxlen=25)
    decisions = []
    if type(auto_exclude) is not bool:
        raise ValueError('Auto-exclude outliers must be true or false.')
    with log_path.open('w', encoding='utf-8') as stream:
        with subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1) as child:
            try:
                for line in child.stdout:
                    stream.write(line)
                    stream.flush()
                    if not line.startswith(PROMPT_PREFIX):
                        context.append(line.rstrip())
                        print(line, end='', flush=True)
                        continue
                    request = json.loads(line[len(PROMPT_PREFIX):])
                    request['context'] = '\n'.join(context)
                    automatic = auto_exclude and request['prompt'] == OUTLIER_PROMPT
                    if automatic:
                        answer = 'y'
                        message = 'Auto-excluding graph-snap outliers as selected in the experiment settings.\n'
                        print(message, end='', flush=True)
                        stream.write(message)
                    elif dialog:
                        print(PROMPT_PREFIX + json.dumps(request), flush=True)
                        answer = sys.stdin.readline().strip().lower()
                    else:
                        answer = input(request['prompt']).strip().lower()
                    if answer not in {'y', 'yes', 'n', 'no'}:
                        raise ValueError('Gold decision requires an explicit yes or no response.')
                    decision = {**request, 'answer': answer,
                                'decision_source': 'auto_exclude_all' if automatic else 'interactive'}
                    # Preserve each exact audit before another case overwrites it.
                    for text in reversed(context):
                        if text.startswith('Audit written to: '):
                            audit = Path(text.removeprefix('Audit written to: '))
                            if not audit.is_absolute():
                                audit = root / audit
                            if audit.is_file():
                                copy = log_path.with_name(f'{log_path.stem}_decision_{len(decisions) + 1}.csv')
                                copy.write_bytes(audit.read_bytes())
                                decision['audit_path'] = copy.relative_to(root).as_posix()
                                decision['audit_sha256'] = hashlib.sha256(copy.read_bytes()).hexdigest()
                            break
                    decisions.append(decision)
                    log_path.with_suffix('.decisions.json').write_text(json.dumps(decisions, indent=2) + '\n', encoding='utf-8')
                    child.stdin.write(answer + '\n')
                    child.stdin.flush()
                return child.wait()
            except BaseException:
                child.terminate()
                child.wait()
                raise
