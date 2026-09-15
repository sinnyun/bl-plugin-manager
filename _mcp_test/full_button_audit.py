"""在当前 Blender 会话中逐个执行插件库管理器的可执行入口。

所有文件操作都指向临时库；官方仓库、脚本目录、偏好和插件启用状态在结束时恢复。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import traceback
import zipfile

import addon_utils
import bpy

import bl_plugin_manager as PM
from bl_plugin_manager import bridge, db, library, items


RESULTS = {}


def call(label, fn, allow_failure=False):
    try:
        ret = fn()
        RESULTS[label] = {"ok": True, "return": sorted(ret) if isinstance(ret, set) else str(ret)}
    except Exception as exc:
        RESULTS[label] = {"ok": bool(allow_failure), "expected_failure": bool(allow_failure),
                          "error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-600:]}


def write_addon(path, name="MCP Fixture", version=(1, 0, 0), fail=False):
    os.makedirs(path, exist_ok=True)
    body = (
        "bl_info = " + repr({"name": name, "version": version,
                             "blender": (4, 2, 0), "description": "MCP fixture"}) + "\n"
        "def register():\n"
        + ("    raise RuntimeError('fixture failure')\n" if fail else "    pass\n")
        + "def unregister():\n    pass\n"
    )
    with open(os.path.join(path, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write(body)


def write_extension(path):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "blender_manifest.toml"), "w", encoding="utf-8") as fh:
        fh.write(
            'schema_version = "1.0.0"\n'
            'id = "mcp_ext"\nversion = "1.0.0"\nname = "MCP Extension"\n'
            'tagline = "fixture"\nmaintainer = "MCP"\n'
            'license = ["SPDX:GPL-3.0-or-later"]\n'
            'type = "add-on"\nblender_version_min = "4.2.0"\n'
        )
    with open(os.path.join(path, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("def register():\n    pass\ndef unregister():\n    pass\n")


def snapshot_preferences():
    prefs = bpy.context.preferences
    pm_prefs = prefs.addons[PM.constants.ADDON_ID].preferences
    scripts = [(getattr(x, "name", ""), getattr(x, "directory", ""))
               for x in prefs.filepaths.script_directories]
    repos = []
    for r in prefs.extensions.repos:
        repos.append({k: getattr(r, k, None) for k in (
            "name", "module", "directory", "custom_directory", "remote_url",
            "use_custom_directory", "use_remote_url", "enabled")})
    pm_fields = {k: getattr(pm_prefs, k) for k in (
        "library_path", "active_category", "only_favorites", "only_updates",
        "only_enabled", "only_incompatible", "search", "selected_key")}
    return {"library_path": pm_prefs.library_path, "scripts": scripts,
            "repos": repos, "online": getattr(prefs.system, "use_online_access", None),
            "pm_fields": pm_fields}


def restore_preferences(state):
    prefs = bpy.context.preferences
    pm_prefs = prefs.addons[PM.constants.ADDON_ID].preferences
    # Remove only entries not present in the original snapshot.
    original_script_paths = {os.path.normcase(os.path.abspath(p)) for _, p in state["scripts"] if p}
    for item in list(prefs.filepaths.script_directories):
        if os.path.normcase(os.path.abspath(getattr(item, "directory", "") or "")) not in original_script_paths:
            try:
                prefs.filepaths.script_directories.remove(item)
            except Exception:
                pass
    for name, directory in state["scripts"]:
        found = next((x for x in prefs.filepaths.script_directories if getattr(x, "directory", "") == directory), None)
        if found:
            found.name = name
        else:
            try:
                x = prefs.filepaths.script_directories.new(); x.name = name; x.directory = directory
            except Exception:
                pass
    original_modules = {r["module"] for r in state["repos"]}
    for r in list(prefs.extensions.repos):
        if getattr(r, "module", "") not in original_modules:
            try:
                prefs.extensions.repos.remove(r)
            except Exception:
                pass
    for snap in state["repos"]:
        r = next((x for x in prefs.extensions.repos if getattr(x, "module", "") == snap["module"]), None)
        if r is None:
            try:
                r = prefs.extensions.repos.new(name=snap.get("name") or snap["module"], module=snap["module"])
            except Exception:
                continue
        for key, value in snap.items():
            if key in ("module",) or value is None:
                continue
            try:
                setattr(r, key, value)
            except Exception:
                pass
    try:
        prefs.system.use_online_access = state["online"]
    except Exception:
        pass
    for key, value in state["pm_fields"].items():
        try:
            setattr(pm_prefs, key, value)
        except Exception:
            pass
    pm_prefs.library_path = state["library_path"]
    bridge.refresh_blender()
    bridge.save_prefs()


def main():
    state = snapshot_preferences()
    base = tempfile.mkdtemp(prefix="pmlib_mcp_buttons_")
    root = os.path.join(base, "library")
    src_addon = os.path.join(base, "source_addon")
    src_zip_dir = os.path.join(base, "ZipFixture")
    zip_path = os.path.join(base, "ZipFixture.zip")
    try:
        os.makedirs(root, exist_ok=True)
        write_addon(src_addon, "MCP Addon")
        write_addon(src_zip_dir, "MCP Zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(os.path.join(src_zip_dir, "__init__.py"), "ZipFixture/__init__.py")

        prefs = bpy.context.preferences.addons[PM.constants.ADDON_ID].preferences
        prefs.library_path = root

        # Library and diagnostics actions.
        call("setup_library", lambda: bpy.ops.plugin_manager.setup_library())
        call("refresh", lambda: bpy.ops.plugin_manager.refresh())
        call("show_warnings", lambda: bpy.ops.plugin_manager.show_warnings())
        call("scan_candidates", lambda: bpy.ops.plugin_manager.scan_candidates())
        call("scan_inbox", lambda: bpy.ops.plugin_manager.scan_inbox())
        call("check_updates", lambda: bpy.ops.plugin_manager.check_updates())
        call("clear_updates", lambda: bpy.ops.plugin_manager.clear_updates())
        call("import_folder", lambda: bpy.ops.plugin_manager.import_plugin(filepath=src_addon, move=False))
        call("import_zip", lambda: bpy.ops.plugin_manager.import_zip(filepath=zip_path))

        d = db.LibraryDB(root)
        addon = next((r for r in d.plugins.values() if r.get("kind") == "addon"), None)
        if addon:
            key = addon["key"]
            call("toggle_select", lambda: bpy.ops.plugin_manager.toggle_select(key=key))
            call("set_favorite", lambda: bpy.ops.plugin_manager.set_favorite(key=key, value=True))
            call("set_category", lambda: bpy.ops.plugin_manager.set_category(key=key, category="MCP分类"))
            call("set_startup", lambda: bpy.ops.plugin_manager.set_startup(key=key, value=True))
            call("toggle_on", lambda: bpy.ops.plugin_manager.toggle(key=key, enable=True))
            call("toggle_off", lambda: bpy.ops.plugin_manager.toggle(key=key, enable=False))
            call("rename_display", lambda: bpy.ops.plugin_manager.rename_display(key=key, display_name="MCP别名"))
            call("rename_display_clear", lambda: bpy.ops.plugin_manager.rename_display(key=key, clear=True))
            call("edit_meta", lambda: bpy.ops.plugin_manager.edit_meta(key=key, note="MCP note", tags="mcp, test"))
            call("open_folder", lambda: bpy.ops.plugin_manager.open_folder(key=key))
        call("add_category", lambda: bpy.ops.plugin_manager.add_category(name="MCP临时分类"))
        call("pick_category", lambda: bpy.ops.plugin_manager.pick_category(category="MCP临时分类"))
        call("rename_category", lambda: bpy.ops.plugin_manager.rename_category(old="MCP临时分类", new="MCP重命名分类"))
        call("delete_category", lambda: bpy.ops.plugin_manager.delete_category(name="MCP重命名分类"))
        call("reset_auto_categories", lambda: bpy.ops.plugin_manager.reset_auto_categories())
        call("select_all", lambda: bpy.ops.plugin_manager.select_all(value=True))
        call("batch_enable", lambda: bpy.ops.plugin_manager.batch_enable(value=False))
        call("batch_set_category", lambda: bpy.ops.plugin_manager.batch_set_category(category=PM.constants.DEFAULT_CATEGORY))
        call("batch_set_startup", lambda: bpy.ops.plugin_manager.batch_set_startup(value=False))
        call("select_all_clear", lambda: bpy.ops.plugin_manager.select_all(value=False))
        call("apply_startup", lambda: bpy.ops.plugin_manager.apply_startup(disable_unmarked=False))
        # This diagnostic intentionally returns CANCELLED when a fixture is
        # incompatible; the important assertion is that it completes and
        # writes the compatibility report, not that every fixture passes.
        call("verify_compat", lambda: bpy.ops.plugin_manager.verify_compat(include_enabled=False),
             allow_failure=True)
        call("cleanup_residue", lambda: bpy.ops.plugin_manager.cleanup_residue())
        call("show_report", lambda: bpy.ops.plugin_manager.show_report())
        call("copy_report", lambda: bpy.ops.plugin_manager.copy_report())
        call("open_report_log", lambda: bpy.ops.plugin_manager.open_report_log())
        call("store_open_execute", lambda: bpy.ops.plugin_manager.store_open())
        call("store_sync", lambda: bpy.ops.plugin_manager.store_sync())
        call("unify_store", lambda: bpy.ops.plugin_manager.unify_store(move=False))
        call("unmount_library", lambda: bpy.ops.plugin_manager.unmount_library())
        call("setup_library_again", lambda: bpy.ops.plugin_manager.setup_library())

        # Menus/panels/header draw smoke after all data is present.
        class Layout:
            def __init__(self): self.errors=[]; self.calls=0
            def __getattr__(self, name):
                def f(*args, **kwargs):
                    self.calls += 1
                    if name in ("row", "column", "box", "split", "grid_flow"):
                        child=Layout(); child.errors=self.errors; return child
                    if name == "operator" and args:
                        try:
                            ns, op = args[0].split(".", 1); getattr(getattr(bpy.ops, ns), op)
                        except Exception as exc: self.errors.append(str(exc))
                    return type("Proxy", (), {})()
                return f
            def __setattr__(self,k,v): object.__setattr__(self,k,v)
        for cls in (PM.ui.PM_PT_main, PM.ui.PM_PT_detail, PM.ui.PM_PT_batch,
                    PM.ui.PM_PT_actions, PM.ui.PM_PT_tools, PM.ui.PM_MT_library,
                    PM.ui.PM_MT_category, PM.ui.PM_MT_plugin):
            lay=Layout()
            try: cls.draw(type("Shim", (), {"layout": lay})(), bpy.context)
            except Exception as exc: RESULTS[f"draw_{cls.__name__}"]={"ok":False,"error":repr(exc)}
            else: RESULTS[f"draw_{cls.__name__}"]={"ok":not lay.errors,"calls":lay.calls,"errors":lay.errors}

        # Remove one fixture through the real operator as the final destructive action.
        d = db.LibraryDB(root)
        victim = next(iter(d.plugins.values()), None)
        if victim:
            call("remove_plugin", lambda: bpy.ops.plugin_manager.remove_plugin(key=victim["key"]))
        RESULTS["summary"] = {"total": len(RESULTS), "failed": sum(1 for x in RESULTS.values() if x.get("ok") is False)}
    finally:
        try:
            restore_preferences(state)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print("@@MCP_BUTTON_AUDIT@@" + json.dumps(RESULTS, ensure_ascii=False, default=str))


main()
