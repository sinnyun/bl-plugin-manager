"""Machine-local settings; never stored in Blender's synchronized preferences."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from pathlib import Path


SCHEMA = 1
_SETTINGS = ("device_name", "library_path", "management_enabled")
_ALLOWED = ("schema", "device_id", *_SETTINGS)


def config_path() -> Path:
    override = os.environ.get("BL_PLUGIN_MANAGER_MACHINE_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "BlenderPluginManager" / "machine.json"


def _defaults() -> dict:
    return {"schema": SCHEMA, "device_id": "",
            "device_name": socket.gethostname(), "library_path": None,
            "management_enabled": False}


def _validate(data: dict) -> None:
    if not isinstance(data["device_id"], str) or not data["device_id"].strip():
        raise ValueError("device_id must be non-empty")
    if not isinstance(data["device_name"], str) or not data["device_name"].strip():
        raise ValueError("device_name must be non-empty")
    if data["library_path"] is not None and not isinstance(data["library_path"], str):
        raise ValueError("library_path must be a string or null")
    if not isinstance(data["management_enabled"], bool):
        raise ValueError("management_enabled must be boolean")


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unsupported machine config")
    result = _defaults()
    result.update({k: data[k] for k in _ALLOWED if k in data})
    _validate(result)
    return result


def load() -> dict:
    """Read the saved configuration, with empty identity on unavailable storage."""
    try:
        return _read(config_path())
    except (OSError, ValueError):
        return _defaults()


def initialize() -> dict:
    """Persist the first device identity and reuse it on later launches."""
    path = config_path()
    try:
        return _read(path)
    except FileNotFoundError:
        current = _defaults()
        current["device_id"] = str(uuid.uuid4())
        try:
            _write(path, current, create=True)
        except FileExistsError:
            return _read(path)
        return current


def save(data: dict) -> None:
    current = initialize()
    current.update({key: data[key] for key in _SETTINGS if key in data})
    _validate(current)
    _write(config_path(), current)


def _write(path: Path, current: dict, *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="machine.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if not create:
            os.replace(tmp, path)
        # Publish the complete file without replacing another process's identity.
        elif os.name == "nt":
            os.rename(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
