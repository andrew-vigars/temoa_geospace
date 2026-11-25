"""Desktop cancellation must stop descendants and release the workflow UI."""
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

import pytest

from geocanoe.execution.configure_workflow import GoldConfigurator
from geocanoe.execution.workflow_process import WorkflowProcess

ROOT = Path(__file__).resolve().parents[1]


def test_cancel_before_launch_does_not_start_work():
    job = WorkflowProcess()
    job.cancel()
    with pytest.raises(RuntimeError, match='cancelled before launch'):
        job.popen([sys.executable, '-c', 'raise AssertionError("launched")'])


def test_cancel_stops_silent_process_and_descendant():
    job = WorkflowProcess()
    code = (
        'import subprocess,sys,time; '
        'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        'print("ready",flush=True); time.sleep(60)'
    )
    process = job.popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        job.cancel()
        # A live descendant would keep the inherited stdout pipe open.
        process.communicate(timeout=10)
        assert process.poll() is not None
    finally:
        job.cancel()
        if process.poll() is None:
            process.kill()


def pump(window, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        window.update()
        time.sleep(.01)
    assert predicate()


@pytest.mark.parametrize('silver', [False, True])
@pytest.mark.parametrize('close', [False, True])
def test_workflow_can_cancel_or_close_active_operation(tmp_path, silver, close):
    # Exercise one Tk application per interpreter, as in normal desktop use.
    code = (f"import runpy; from pathlib import Path; "
            f"runpy.run_path({str(Path(__file__).resolve())!r})['_exercise_workflow']("
            f"Path({str(tmp_path)!r}), {silver!r}, {close!r})")
    subprocess.run([sys.executable, '-c', code], check=True, timeout=30,
                   capture_output=True, text=True)


def _exercise_workflow(tmp_path, silver, close):
    original_popen = WorkflowProcess.popen
    def launch_test_work(self, command, **kwargs):
        return original_popen(self, [sys.executable, '-u', '-c',
            'import time; print("ready",flush=True); time.sleep(60)'], **kwargs)
    WorkflowProcess.popen = launch_test_work
    window = tk.Tk()
    window.withdraw()
    destroyed = []
    window.bind('<Destroy>', lambda event: destroyed.append(True) if event.widget == window else None)
    app = GoldConfigurator(window, ROOT, ROOT / 'registry/model.toml')
    owner = app.silver_panel if silver else app
    try:
        assert app.cancel_button.instate(['disabled'])
        if silver:
            owner.start(True)
        else:
            app.start([], 'Test operation', tmp_path)
        pump(window, lambda: 'ready' in owner.log.get('1.0', 'end'))
        assert app.cancel_button.instate(['!disabled'])
        if close:
            app.close()
            pump(window, lambda: bool(destroyed))
        else:
            (owner.cancel_button if silver else app.cancel_button).invoke()
            pump(window, lambda: not owner.busy)
            assert 'cancelled' in owner.status.get()
            assert app.cancel_button.instate(['disabled'])
            assert app.buttons['Review & generate'].instate(['!disabled'])
        assert owner.job.process.poll() is not None
    finally:
        if owner.job is not None:
            owner.job.cancel()
        if not destroyed:
            window.destroy()
