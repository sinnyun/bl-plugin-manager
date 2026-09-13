"""Environment-scoped runtime state for a shared plugin library."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _safe_component(value: str) -> str:
    value = str(value)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in value)
    return safe.strip(".") or "unknown"


class StateStore:
    def __init__(self, library_id: str, environment: str):
        base = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        if not base:
            base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BlenderPluginManager" / "state"
        self.path = Path(base) / _safe_component(library_id) / (_safe_component(environment) + ".json")

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                value = json.load(fh)
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, ValueError, UnicodeDecodeError):
            return {}

    def save(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise TypeError("runtime state must be an object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=self.path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(value, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

