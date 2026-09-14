"""Lifecycle for the narrowly scoped Blender configuration takeover."""

from __future__ import annotations

import os

from . import constants as C
from .db import LibraryDB
from .storage import machine_config
from .storage.device_profiles import DeviceProfileStore


# Loaded lazily so the storage/state logic remains testable outside Blender.
bridge = None


def _bridge():
    global bridge
    if bridge is None:
        from . import bridge as blender_bridge
        bridge = blender_bridge
    return bridge


def _identity() -> tuple[str, str, dict]:
    local = machine_config.load()
    return local["device_id"], local["device_name"], local


def _records(root: str) -> list[dict]:
    return LibraryDB(root).all()


def _current_intent(root: str, existing: set[str] | None = None) -> set[str]:
    api = _bridge()
    intent = set(existing or ())
    for record in _records(root):
        key, module = record.get("key", ""), record.get("module", "")
        if not key or not module:
            continue
        if api.is_module_enabled(module):
            intent.add(key)
        else:
            intent.discard(key)
    return intent


def _ensure_library_bindings(root: str, config: dict) -> dict:
    api = _bridge()
    config = {
        "repositories": [dict(record) for record in config.get("repositories", [])
                         if record.get("module") != C.REPO_MODULE],
        "script_directories": [dict(record) for record in config.get("script_directories", [])],
    }
    ext_dir = os.path.join(os.path.abspath(root), C.DIR_EXTENSIONS)
    official = None
    for record in config["repositories"]:
        if record.get("module") == api.OFFICIAL_REPO_MODULE:
            official = record
            break
    if official is None:
        official = {"name": "extensions.blender.org",
                    "module": api.OFFICIAL_REPO_MODULE}
        config["repositories"].append(official)
    official.update({
        "remote_url": api.OFFICIAL_SOURCE_URL,
        "custom_directory": ext_dir,
        "enabled": True,
        "use_custom_directory": True,
        "use_remote_url": True,
    })
    target = os.path.normcase(os.path.abspath(root))
    if not any(os.path.normcase(os.path.abspath(item.get("directory", ""))) == target
               for item in config["script_directories"] if item.get("directory")):
        config["script_directories"].append({
            "name": f"{C.ADDON_NAME} - {os.path.basename(target)}",
            "directory": os.path.abspath(root),
        })
    return config


def _initial_configuration(root: str) -> dict:
    return _ensure_library_bindings(root, _bridge().capture_scoped_configuration())


def sync_to_profile(root: str) -> dict:
    """Import native-panel changes and runtime activation into this device file."""
    device_id, device_name, _local = _identity()
    store = DeviceProfileStore(root)
    current = store.load(device_id)
    api = _bridge()
    captured = api.capture_scoped_configuration()
    config = _ensure_library_bindings(root, captured)
    if captured != config:
        api.apply_scoped_configuration(config)
    enabled = _current_intent(root, set(current["enabled_plugins"]))
    if (set(current["enabled_plugins"]) == enabled
            and current["repositories"] == config["repositories"]
            and current["script_directories"] == config["script_directories"]
            and current["device_name"] == device_name):
        return current
    return store.save(device_id, device_name, enabled,
                      repositories=config["repositories"],
                      script_directories=config["script_directories"])


def record_activation(root: str, plugin_key: str, enabled: bool) -> dict:
    device_id, device_name, _local = _identity()
    return DeviceProfileStore(root).update_enabled(
        device_id, device_name, plugin_key, enabled)


def _apply_activation(root: str, desired: set[str]) -> list[dict]:
    api = _bridge()
    failures = []
    for record in _records(root):
        key, module = record.get("key", ""), record.get("module", "")
        if not key or not module or module == C.ADDON_ID:
            continue
        target = key in desired
        if api.is_module_enabled(module) == target:
            continue
        ok, error = api.set_enabled(module, target)
        if not ok:
            failures.append({"key": key, "target": target, "error": error})
    return failures


def activate(root: str) -> dict:
    """Apply this device profile, creating it from current scoped state once."""
    root = os.path.abspath(root)
    device_id, device_name, _local = _identity()
    store = DeviceProfileStore(root)
    try:
        if store.exists(device_id):
            profile = store.load(device_id)
            config = _ensure_library_bindings(root, profile)
            if (config["repositories"] != profile["repositories"]
                    or config["script_directories"] != profile["script_directories"]):
                profile = store.save(
                    device_id, device_name, profile["enabled_plugins"],
                    repositories=config["repositories"],
                    script_directories=config["script_directories"],
                )
        else:
            config = _initial_configuration(root)
            profile = store.save(
                device_id, device_name, _current_intent(root),
                repositories=config["repositories"],
                script_directories=config["script_directories"],
            )
        _bridge().apply_scoped_configuration(profile)
        failures = _apply_activation(root, set(profile["enabled_plugins"]))
        setter = getattr(_bridge(), "set_managed_library_root", None)
        if setter:
            setter(root)
        machine_config.save({"library_path": root, "management_enabled": True})
        return {"state": "ACTIVE" if not failures else "ERROR", "failures": failures}
    except Exception as exc:
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "ERROR", "failures": [{"error": str(exc)}]}


def deactivate(root: str) -> dict:
    """Persist current intent, disable managed add-ons, and reset only owned scopes."""
    root = os.path.abspath(root)
    failures = []
    try:
        sync_to_profile(root)
        failures.extend(_apply_activation(root, set()))
        _bridge().reset_scoped_configuration()
        setter = getattr(_bridge(), "set_managed_library_root", None)
        if setter:
            setter("")
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "INACTIVE" if not failures else "ERROR", "failures": failures}
    except Exception as exc:
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "ERROR", "failures": failures + [{"error": str(exc)}]}
