"""Single ordered activation transaction for a library."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ActivationReport:
    status: str
    path: Path
    error: str = ""


class LibraryService:
    def __init__(self, *, load_database: Callable, persist_path: Callable,
                 reconcile_mounts: Callable, refresh_modules: Callable,
                 scan: Callable, publish: Callable):
        self.load_database = load_database
        self.persist_path = persist_path
        self.reconcile_mounts = reconcile_mounts
        self.refresh_modules = refresh_modules
        self.scan = scan
        self.publish = publish

    def activate(self, path: str | os.PathLike[str]) -> ActivationReport:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            return ActivationReport("OFFLINE", root, "library directory does not exist")
        try:
            database = self.load_database(root)
            self.persist_path(root)
            self.reconcile_mounts(root)
            self.refresh_modules()
            self.scan(root, database)
            self.publish()
            return ActivationReport("READY", root)
        except Exception as exc:
            return ActivationReport("FAILED", root, str(exc))

