"""Lifecycle for the narrowly scoped Blender configuration takeover."""

from __future__ import annotations

import os
from dataclasses import dataclass

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
    local = machine_config.initialize()
    return local["device_id"], local["device_name"], local


def _records(root: str) -> list[dict]:
    return LibraryDB(root).all()


def _current_intent(root: str, existing: set[str] | None = None) -> set[str]:
    api = _bridge()
    intent = set(existing or ())
    db = LibraryDB(root)
    changed = False
    for record in db.all():
        key, module = record.get("key", ""), record.get("module", "")
        if not key or not module or record.get("missing"):
            continue
        enabled = api.is_module_enabled(module)
        if enabled != bool(record.get("enabled")):
            record["enabled"] = enabled
            changed = True
        if "startup_override" in record:
            if enabled == record["startup_override"]:
                continue
            record.pop("startup_override")
            changed = True
        if enabled:
            intent.add(key)
        elif not (record.get("last_error") or record.get("restore_error")):
            intent.discard(key)
    if changed:
        db.save()
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


def record_activations(root: str, keys, enabled: bool) -> None:
    device_id, device_name, _local = _identity()
    store = DeviceProfileStore(root)
    profile = store.load(device_id)
    intent = set(profile["enabled_plugins"])
    if enabled:
        intent.update(keys)
    else:
        intent.difference_update(keys)
    store.save(device_id, device_name, intent, repositories=profile["repositories"],
               script_directories=profile["script_directories"])


def record_activation(root: str, plugin_key: str, enabled: bool) -> None:
    record_activations(root, (plugin_key,), enabled)


def transfer_activation(root: str, origin: str, target: str) -> None:
    device_id, device_name, _local = _identity()
    store = DeviceProfileStore(root)
    profile = store.load(device_id)
    intent = set(profile["enabled_plugins"])
    if origin in intent:
        intent.discard(origin)
        intent.add(target)
        store.save(device_id, device_name, intent, repositories=profile["repositories"],
                   script_directories=profile["script_directories"])


def _apply_activation(root: str, desired: set[str], *, skip_builtins: bool = False) -> list[dict]:
    api = _bridge()
    failures = []
    db = LibraryDB(root)
    changed = False
    for record in db.all():
        key, module = record.get("key", ""), record.get("module", "")
        if not key or not module or record.get("missing") or module == C.ADDON_ID:
            continue
        if skip_builtins and record.get("kind") == C.KIND_BUILTIN:
            continue
        target = key in desired
        if api.is_module_enabled(module) == target:
            continue
        ok, error = api.set_enabled(module, target)
        if not ok:
            failures.append({"key": key, "target": target, "error": error})
        record["enabled"] = api.is_module_enabled(module)
        record["last_error"] = "" if ok else error or "无法应用启用意图"
        changed = True
    if changed:
        db.save()
    return failures


def activate(root: str, after_mount=None) -> dict:
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
        if after_mount is not None:
            after_mount()
        failures = _apply_activation(root, set(profile["enabled_plugins"]))
        setter = getattr(_bridge(), "set_managed_library_root", None)
        if setter:
            setter(root)
        machine_config.save({"library_path": root, "management_enabled": True})
        return {"state": "ACTIVE" if not failures else "ERROR", "failures": failures}
    except Exception as exc:
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "ERROR", "failures": [{"error": str(exc)}]}


@dataclass
class PreparedLibrary:
    root: str
    previous: dict
    database: LibraryDB
    entries: list[dict] | None


def prepare_library(root: str, previous: dict, load_environment: str, *, resume: bool) -> PreparedLibrary:
    """Validate, migrate, and read plugin metadata without accessing Blender."""
    from . import library

    previous_root = previous.get("library_path")
    is_switch = bool(previous.get("management_enabled") and previous_root
                     and os.path.normcase(os.path.abspath(previous_root)) != os.path.normcase(root))
    unavailable = not os.path.isdir(root)
    if unavailable and (resume or (previous.get("management_enabled") and not is_switch)):
        raise RuntimeError("插件库离线，请恢复目录后重连")
    info = library.inspect_target(root, scan_flat=not resume)
    if error := library.target_error(info):
        raise RuntimeError(error)
    database = LibraryDB(root, use_cache=False, load_environment=load_environment)
    if database.status in {"CORRUPT", "UNSUPPORTED"}:
        raise RuntimeError(f"插件库资料不可写（{database.status}），原文件已保留")
    library.bridge.ensure_library_dirs(root)
    status = database.prepare()
    if status != "OK":
        raise RuntimeError(f"插件库资料不可写（{status}），原文件已保留")
    needs_import = not resume and not info["is_library"] and bool(info["flat_plugins"])
    entries = None if needs_import else library.scan_library_entries(
        root, existing_db=database, skip_unchanged=True,
    )
    if entries is not None:
        database.merge_scan(entries, persist=False)
    return PreparedLibrary(root, previous, database, entries)


def activate_prepared_library(prepared: PreparedLibrary) -> dict:
    """Apply Blender configuration on the main thread and roll back on failure."""
    from . import library

    root, previous, database = prepared.root, prepared.previous, prepared.database
    if machine_config.load() != previous:
        raise RuntimeError("本机插件库配置已变化，请重新连接")
    previous_root = previous.get("library_path")
    is_switch = bool(previous.get("management_enabled") and previous_root
                     and os.path.normcase(os.path.abspath(previous_root)) != os.path.normcase(root))
    result = library.connect_library(root, database, move_flat=False) if prepared.entries is None else {}

    def rescan():
        if prepared.entries is None:
            library.sync_library(root, database)
        else:
            library.apply_scan_runtime(root, database, prepared.entries)

    api = _bridge()
    original_config = api.capture_scoped_configuration()
    original_enabled = api.enabled_modules()
    target_modules = set()
    try:
        if is_switch:
            stopped = deactivate(previous_root)
            if stopped["state"] != "INACTIVE":
                raise RuntimeError(f"停止原插件库失败: {stopped['failures'][:3]}")
        outcome = activate(root, after_mount=rescan)
        target_modules = {rec.get("module") for rec in _records(root)}
        if not api.is_registered(root):
            raise RuntimeError(f"挂载未完成: {outcome['failures'][:3]}")
        # 插件自身加载失败应保留启用意图并报告；目录和仓库仍可正常使用。
        if not machine_config.load().get("management_enabled"):
            raise RuntimeError(f"启用管理失败: {outcome['failures'][:3]}")
    except Exception:
        for module in (api.enabled_modules() - original_enabled) & target_modules:
            api.set_enabled(module, False)
        api.apply_scoped_configuration(original_config)
        for module in original_enabled - api.enabled_modules():
            api.set_enabled(module, True)
        api.set_managed_library_root(previous_root if previous.get("management_enabled") else "")
        machine_config.save(previous)
        raise
    return {**result, **outcome, "migration": getattr(database, "migration_report", None)}


def deactivate(root: str) -> dict:
    """Persist current intent, disable managed add-ons, and reset only owned scopes."""
    root = os.path.abspath(root)
    failures = []
    try:
        if os.path.isdir(root):
            sync_to_profile(root)
            failures.extend(_apply_activation(root, set(), skip_builtins=True))
        _bridge().reset_scoped_configuration()
        setter = getattr(_bridge(), "set_managed_library_root", None)
        if setter:
            setter("")
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "INACTIVE" if not failures else "ERROR", "failures": failures}
    except Exception as exc:
        machine_config.save({"library_path": root, "management_enabled": False})
        return {"state": "ERROR", "failures": failures + [{"error": str(exc)}]}
