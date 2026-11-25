"""Cancel a desktop workflow and its child processes without blocking Tk."""
from __future__ import annotations

import os
import signal
import subprocess
import threading


class WorkflowProcess:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self.process = None

    def popen(self, command, **kwargs):
        with self._lock:
            if self.cancelled.is_set():
                raise RuntimeError('Operation cancelled before launch.')
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            else:
                kwargs['start_new_session'] = True
            self.process = subprocess.Popen(command, **kwargs)
            return self.process

    def cancel(self):
        if self.cancelled.is_set():
            return
        self.cancelled.set()
        threading.Thread(target=self.stop_tree, daemon=True).start()

    def stop_tree(self):
        with self._lock:
            process = self.process
        if process is None:
            return
        if os.name == 'nt':
            result = subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode and process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
