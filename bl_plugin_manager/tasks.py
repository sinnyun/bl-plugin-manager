"""Small worker controller for IO-bound plugin operations.

Workers return data only; callers must apply Blender API changes on the main
thread from a timer/modal callback.
"""

from __future__ import annotations

import threading
from typing import Callable


class TaskController:
    def __init__(self):
        self.state = "idle"
        self.progress = 0.0
        self.result = None
        self.error = ""
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def start(self, job: Callable, *args, **kwargs) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("task already running")
        self.state = "running"
        self.result = None
        self.error = ""
        self._cancel.clear()

        def run():
            try:
                self.result = job(*args, **kwargs)
                self.state = "cancelling" if self._cancel.is_set() else "succeeded"
            except Exception as exc:
                self.error = str(exc)
                self.state = "failed"

        self._thread = threading.Thread(target=run, name="plugin-manager-task", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        if self.state == "running":
            self._cancel.set()
            self.state = "cancelling"

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

