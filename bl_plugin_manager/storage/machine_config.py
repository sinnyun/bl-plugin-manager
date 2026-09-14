"""Machine-local settings; never stored in Blender's synchronized preferences."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
import socket
from pathlib import Path


SCHEMA = 1
_ALLOWED = {"schema", "device_id", "device_name", "library_path",
            "management_enabled"}


def config_path() -> Path:
    override = os.environ.get("BL_PLUGIN_MANAGER_MACHINE_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "BlenderPluginManager" / "machine.json"


def _defaults() -> dict:
    return {"schema": SCHEMA, "device_id": str(uuid.uuid4()),
            "device_name": socket.gethostname(), "library_path": None,
            "management_enabled": False}


def load() -> dict:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ValueError("unsupported machine config")
        result = _defaults()
        result.update({k: data[k] for k in _ALLOWED if k in data})
        if result["library_path"] is not None and not isinstance(result["library_path"], str):
            raise ValueError("library_path must be a string or null")
        if not isinstance(result["device_id"], str) or not result["device_id"]:
            raise ValueError("device_id must be non-empty")
        if not isinstance(result["device_name"], str) or not result["device_name"].strip():
            raise ValueError("device_name must be non-empty")
        if not isinstance(result["management_enabled"], bool):
            raise ValueError("management_enabled must be boolean")
        return result
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
        return _defaults()


def save(data: dict) -> None:
    path = config_path()
    current = load()
    current.update({k: data[k] for k in _ALLOWED if k in data})
    current["schema"] = SCHEMA
    if not isinstance(current['device_name'], str) or not current['device_name'].strip():
        raise ValueError('device_name must be non-empty')
    if current["library_path"] is not None and not isinstance(current["library_path"], str):
        raise ValueError("library_path must be a string or null")
    if not isinstance(current["management_enabled"], bool):
        raise ValueError("management_enabled must be boolean")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="machine.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
