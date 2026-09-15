"""针对已确认缺陷的隔离回归测试。

该脚本只在临时目录中创建插件库；运行器会把插件包放入隔离的 Blender
user-scripts 目录，因此不会修改真实插件库、启用状态或用户偏好。

用法（Blender 后台）：
    blender --background --factory-startup --python test_regressions.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import bpy  # type: ignore

from bl_plugin_manager import bridge, constants as C, library, ui
from bl_plugin_manager.db import LibraryDB
from bl_plugin_manager.storage.shared_db import SharedDatabase


def _assert_isolated_runtime() -> None:
    expected = os.path.realpath(os.environ["BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT"])
    paths = {
        "Blender config": bpy.utils.user_resource("CONFIG"),
        "Blender scripts": bpy.utils.user_resource("SCRIPTS"),
        "machine config": os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"],
        "local state": os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"],
        "temp": tempfile.gettempdir(),
    }
    for label, value in paths.items():
        actual = os.path.realpath(value)
        if os.path.commonpath((expected, actual)) != expected:
            raise RuntimeError(f"unsafe test path for {label}: {actual}")


_assert_isolated_runtime()


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: object, detail: str = "") -> None:
    ok = bool(condition)
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def _write_legacy(path: str, name: str = "Legacy Regression", version=(1, 0, 0)) -> None:
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, C.LEGACY_INIT), "w", encoding="utf-8") as fh:
        fh.write(
            "bl_info = " + repr({
                "name": name,
                "author": "regression",
                "version": version,
                "blender": (4, 2, 0),
                "description": "regression fixture",
            }) + "\n"
            "def register():\n    pass\n"
            "def unregister():\n    pass\n"
        )


def _repo_snapshot(repo):
    if repo is None:
        return None
    fields = (
        "name", "module", "directory", "custom_directory", "remote_url",
        "use_custom_directory", "use_remote_url", "enabled",
    )
    return {field: getattr(repo, field, None) for field in fields}


def _restore_official(snapshot):
    """Restore the official repo in the isolated preference collection."""
    if not snapshot:
        return
    _, repo = bridge.find_repo(module=bridge.OFFICIAL_REPO_MODULE)
    if repo is None:
        try:
            repo = bpy.context.preferences.extensions.repos.new(
                name=snapshot.get("name") or "extensions.blender.org",
                module=bridge.OFFICIAL_REPO_MODULE,
            )
        except Exception:
            return
    for field, value in snapshot.items():
        if field in ("name", "module") or value is None:
            continue
        try:
            setattr(repo, field, value)
        except Exception:
            pass


def case_failed_trash_move(root: str) -> None:
    """A failed move must keep both the source directory and DB record."""
    # Start outside the library so import_plugin_dir places it at the exact
    # path recorded in the DB rather than creating a ``_2`` duplicate.
    plugin = os.path.join(os.path.dirname(root), "TrashFailure")
    _write_legacy(plugin)
    db = LibraryDB(root, use_cache=False)
    rec = library.import_plugin_dir(plugin, root, db, move=True)
    installed = os.path.join(root, rec["rel"].replace("/", os.sep))

    original_move = library.shutil.move

    def fail_move(*_args, **_kwargs):
        raise OSError("injected move failure")

    library.shutil.move = fail_move
    try:
        result = library.remove_plugin(root, db, rec["key"], to_trash=True)
    finally:
        library.shutil.move = original_move
    check("failed trash move returns failure", result is None)
    check("failed trash move keeps source", os.path.isdir(installed))
    check("failed trash move keeps record", LibraryDB(root, use_cache=False).get(rec["key"]) is not None)


def case_same_version_metadata_refresh(root: str) -> None:
    """Metadata changes must be observed even when the plugin version is unchanged."""
    plugin = os.path.join(root, C.DIR_ADDONS, "SameVersion")
    _write_legacy(plugin, "Old Regression Name")
    db = LibraryDB(root, use_cache=False)
    first = library.sync_library(root, db)
    key = "addons/SameVersion"
    old = LibraryDB(root, use_cache=False).get(key)
    check("same-version fixture imported", old is not None, str(first))

    # Ensure both mtime and size differ on filesystems with coarse timestamps.
    time.sleep(0.02)
    _write_legacy(plugin, "New Regression Name")
    os.utime(os.path.join(plugin, C.LEGACY_INIT), None)
    stats = library.sync_library(root, LibraryDB(root, use_cache=False))
    fresh = LibraryDB(root, use_cache=False).get(key)
    check("same-version metadata refreshed", fresh and fresh.get("name") == "New Regression Name",
          f"name={fresh.get('name') if fresh else None}, stats={stats}")


def case_cache_isolation(root: str) -> None:
    """Mutating a saved DB object after save must not mutate the global cache."""
    db = LibraryDB(root, use_cache=True)
    rec = db.upsert("addons/cache_fixture", {"name": "saved value"})
    db.save()
    rec["name"] = "UNSAVED mutation"
    fresh = LibraryDB(root, use_cache=True)
    loaded = fresh.get("addons/cache_fixture")
    check("DB cache isolates unsaved mutation",
          loaded is not None and loaded.get("name") == "saved value",
          str(loaded))


def case_schema2_scan_persists_local_module(root: str) -> None:
    """A fresh schema-2 scan must resolve module names into local state."""
    schema_root = root + "_schema2"
    SharedDatabase(schema_root).initialize()
    plugin = os.path.join(schema_root, C.DIR_ADDONS, "blender_mcp")
    _write_legacy(plugin, "MCP for Blender", (1, 6))

    library.sync_library(schema_root, LibraryDB(schema_root, use_cache=False))
    record = LibraryDB(schema_root, use_cache=False).get("addons/blender_mcp")
    check("schema2 scan resolves MCP module locally",
          record is not None and record.get("module") == "blender_mcp", str(record))

    with open(os.path.join(schema_root, C.DIR_META, C.DB_FILENAME),
              "r", encoding="utf-8") as fh:
        shared_record = json.load(fh)["plugins"]["addons/blender_mcp"]
    check("schema2 shared record excludes machine module",
          "module" not in shared_record, str(shared_record))


def case_mount_state_signature(root: str) -> None:
    """Changing registered paths without changing collection sizes invalidates state."""
    if not hasattr(bpy.context.preferences, "extensions"):
        check("mount state API available", False, "Blender extensions preferences unavailable")
        return
    other = root + "_other"
    os.makedirs(os.path.join(other, C.DIR_EXTENSIONS), exist_ok=True)
    bridge.invalidate_state_cache()
    try:
        config = bridge.capture_scoped_configuration()
        for rec in config["repositories"]:
            if rec.get("module") == bridge.OFFICIAL_REPO_MODULE:
                rec.update({"custom_directory": os.path.join(root, C.DIR_EXTENSIONS),
                            "use_custom_directory": True})
        config["script_directories"].append({"name": "Plugin Library", "directory": root})
        bridge.apply_scoped_configuration(config)
        script_index, script_item = bridge.find_script_dir(root)
        repo_index, repo = bridge.find_repo(directory=os.path.join(root, C.DIR_EXTENSIONS))
        check("mount state fixture registered", script_index >= 0 and repo is not None)
        if script_item is None or repo is None:
            return
        old_script_path = script_item.directory
        old_custom_path = getattr(repo, "custom_directory", "")
        script_item.directory = other
        try:
            repo.custom_directory = os.path.join(other, C.DIR_EXTENSIONS)
        except Exception:
            pass
        changed = bridge.library_state(root)
        check("mount state cache tracks path-only changes",
              not changed["script_dir"] and not changed["repo"], str(changed))
        script_item.directory = old_script_path
        try:
            repo.custom_directory = old_custom_path
        except Exception:
            pass
    finally:
        # Restore the fixture even if the assertion or Blender RNA assignment failed.
        bridge.invalidate_state_cache()
        try:
            bridge.reset_scoped_configuration()
        except Exception as exc:
            print("[WARN] mount fixture cleanup:", exc)


def case_repository_target_selection(root: str) -> None:
    """Scoped reset restores the official repo without touching system preferences."""
    if not hasattr(bpy.context.preferences, "extensions"):
        check("repository API available", False, "Blender extensions preferences unavailable")
        return
    official = bridge.find_official_repo()[1]
    created_official = official is None
    if official is None:
        try:
            official = bpy.context.preferences.extensions.repos.new(
                name="extensions.blender.org", module=bridge.OFFICIAL_REPO_MODULE)
            official.enabled = False
            official.use_custom_directory = False
            official.use_remote_url = True
            official.remote_url = bridge.OFFICIAL_SOURCE_URL
        except Exception as exc:
            check("official repository fixture created", False, repr(exc))
            return
    online_before = bpy.context.preferences.system.use_online_access
    bridge.invalidate_state_cache()
    try:
        official.use_custom_directory = True
        official.custom_directory = os.path.join(root, C.DIR_EXTENSIONS)
        bpy.context.preferences.extensions.repos.new(name="Team", module="team_repo")
        bridge.reset_scoped_configuration()
        restored = bridge.find_official_repo()[1]
        check("scoped reset restores official repository defaults",
              restored is not None
              and not restored.use_custom_directory
              and restored.remote_url == bridge.OFFICIAL_SOURCE_URL,
              str(_repo_snapshot(restored)))
        check("scoped reset removes custom repositories",
              bridge.find_repo(module="team_repo")[1] is None)
        check("scoped reset preserves online access",
              bpy.context.preferences.system.use_online_access == online_before)
    finally:
        if created_official:
            # This object was only a test fixture in the isolated process.
            _, fixture = bridge.find_official_repo()
            if fixture is not None:
                try:
                    bpy.context.preferences.extensions.repos.remove(fixture)
                except Exception:
                    pass
        bridge.invalidate_state_cache()


def case_list_compatibility_display(_root: str) -> None:
    """List rows expose current compatibility and declared maximum support."""
    ok = SimpleNamespace(
        compat="ok", supported="yes", load_state="ok", compat_detail="支持 4.2.0 ~ 5.2.0",
        max_version_text="≤ 5.2.0")
    too_new = SimpleNamespace(
        compat="too_new", supported="no", load_state="", compat_detail="最高仅支持 5.0.0",
        max_version_text="≤ 5.0.0")
    unknown = SimpleNamespace(
        compat="unknown", supported="unknown", load_state="", compat_detail="未声明支持的 Blender 版本",
        max_version_text="不限")
    failed = SimpleNamespace(
        compat="ok", supported="no", load_state="failed", compat_detail="支持 4.2.0 ~ 5.2.0",
        max_version_text="≤ 5.2.0")
    untested = SimpleNamespace(
        compat="ok", supported="unknown", load_state="", compat_detail="支持 4.2.0 ~ 5.2.0",
        max_version_text="≤ 5.2.0")
    check("list display compatible", ui._compat_display(ok) ==
          ("✓ 已实测可启动", "最高 ≤ 5.2.0", False))
    check("list display too-new", ui._compat_display(too_new) ==
          ("✗ 当前 Blender 不兼容", "最高 ≤ 5.0.0", True))
    check("list display unknown", ui._compat_display(unknown) ==
          ("? 未声明，未实测", "最高 不限", False))
    check("list display load-failed", ui._compat_display(failed) ==
          ("✗ 启动测试失败", "最高 ≤ 5.2.0", True))
    check("list display untested", ui._compat_display(untested) ==
          ("? 声明兼容，未实测", "最高 ≤ 5.2.0", False))


def main() -> int:
    base = tempfile.mkdtemp(prefix="pmlib_regressions_")
    root = os.path.join(base, "library")
    os.makedirs(root, exist_ok=True)
    cases = (
        ("failed trash move", case_failed_trash_move),
        ("same-version metadata", case_same_version_metadata_refresh),
        ("cache isolation", case_cache_isolation),
        ("schema2 local module", case_schema2_scan_persists_local_module),
        ("mount state signature", case_mount_state_signature),
        ("repository target", case_repository_target_selection),
        ("list compatibility display", case_list_compatibility_display),
    )
    try:
        for label, case in cases:
            try:
                case(root)
            except Exception as exc:
                check(label, False, f"unexpected exception: {exc!r}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [name for name, ok, _detail in RESULTS if not ok]
    summary = {
        "total": len(RESULTS),
        "failed": len(failed),
        "failures": [{"name": n, "detail": d} for n, ok, d in RESULTS if not ok],
    }
    print("\n===REGRESSION SUMMARY===")
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
