"""Per-device activation intent stored inside the portable plugin library."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = 1
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class CorruptDeviceProfileError(RuntimeError):
    pass


class DeviceProfileStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.directory = Path(root) / ".pm" / "devices"

    def _path(self, device_id: str) -> Path:
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("invalid device id")
        return self.directory / f"{device_id}.json"

    def exists(self, device_id: str) -> bool:
        return self._path(device_id).is_file()

    def load(self, device_id: str) -> dict:
        path = self._path(device_id)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = json.load(fh)
        except FileNotFoundError:
            return {"schema": SCHEMA, "device_id": device_id,
                    "device_name": "", "enabled_plugins": [],
                    "repositories": [], "script_directories": []}
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise CorruptDeviceProfileError(f"设备配置损坏: {path}") from exc
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                or value.get("device_id") != device_id
                or not isinstance(value.get("device_name"), str)
                or not isinstance(value.get("enabled_plugins"), list)
                or not all(isinstance(item, str) for item in value["enabled_plugins"])
                or not self._valid_records(value.get("repositories"))
                or not self._valid_records(value.get("script_directories"))):
            raise CorruptDeviceProfileError(f"设备配置结构无效: {path}")
        return value

    def save(self, device_id: str, device_name: str,
             enabled_plugins: set[str] | list[str], *,
             repositories: list[dict] | None = None,
             script_directories: list[dict] | None = None) -> dict:
        path = self._path(device_id)
        if not isinstance(device_name, str) or not device_name.strip():
            raise ValueError("device name must be non-empty")
        if repositories is None or script_directories is None:
            current = self.load(device_id)
            repositories = current["repositories"] if repositories is None else repositories
            script_directories = (current["script_directories"]
                                  if script_directories is None else script_directories)
        if not self._valid_records(repositories):
            raise ValueError("repositories must be a list of objects")
        if not self._valid_records(script_directories):
            raise ValueError("script_directories must be a list of objects")
        payload = {
            "schema": SCHEMA,
            "device_id": device_id,
            "device_name": device_name.strip(),
            "enabled_plugins": sorted(set(enabled_plugins)),
            "repositories": repositories,
            "script_directories": script_directories,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f"{device_id}.", suffix=".tmp", dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return payload

    @staticmethod
    def _valid_records(value: object) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)

    def update_enabled(self, device_id: str, device_name: str,
                       plugin_key: str, enabled: bool) -> dict:
        current = self.load(device_id)
        intent = set(current["enabled_plugins"])
        if enabled:
            intent.add(plugin_key)
        else:
            intent.discard(plugin_key)
        return self.save(device_id, device_name, intent)

    def list_devices(self) -> list[dict]:
        if not self.directory.is_dir():
            return []
        devices = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                devices.append(self.load(path.stem))
            except (ValueError, CorruptDeviceProfileError):
                continue
        return devices
