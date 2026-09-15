"""插件库管理器 —— 统一的 Blender 插件资源管理器。

集中存放所有插件（传统插件与扩展插件），提供分类、备注、标签、收藏、
启停与更新检测；通过把库目录注册为 Blender 脚本目录 / 扩展仓库来挂载，
因此升级 Blender 版本后仍可继续沿用同一套库与元数据。
"""

bl_info = {
    "name": "插件库管理器 (Plugin Library Manager)",
    "author": "ZCode",
    "version": (2, 0, 1),
    "blender": (4, 2, 0),
    "location": "3D 视图 > 侧边栏 (N) > 插件库",
    "description": "集中管理所有 Blender 插件：分类、备注、启停、更新检测、自动收编",
    "category": "System",
}

import importlib
import os

_PM_WAS_IMPORTED = "bpy" in globals()
import bpy

from . import constants as C
from . import (
    bridge,
    db,
    header,
    items,
    library,
    migrate,
    operators,
    preferences,
    scoped_management,
    scan,
    store,
    ui,
    updates,
    watcher,
)

# 支持在 Blender 文本编辑器中热重载
if _PM_WAS_IMPORTED:  # pragma: no cover
    for _mod in (C, scan, db, bridge, scoped_management, library, store, updates, migrate, watcher,
                 items, preferences, operators, ui):
        importlib.reload(_mod)

_modules = (preferences, operators, ui)
_registered = []


def _sync_manager_state():
    """Mirror native-panel changes into the active device profile."""
    try:
        from .storage import machine_config
        local = machine_config.load()
        root = local.get("library_path")
        if local.get("management_enabled") and root and os.path.isdir(root):
            scoped_management.sync_to_profile(root)
    except Exception as exc:
        print("[插件库] 同步原生配置失败:", exc)
    return 2.0


def _bootstrap_prefs():
    """Load machine-local identity and resume only an explicitly active library."""
    prefs = bridge.get_prefs()
    local = None
    if prefs:
        try:
            from .storage import machine_config
            local = machine_config.load()
            path = local.get("library_path")
            preferences._LOADING_MACHINE_PATH = True
            try:
                prefs.device_name = local.get("device_name", "")
                if path:
                    prefs.library_path = path
            finally:
                preferences._LOADING_MACHINE_PATH = False
        except Exception as exc:
            print("[插件库] 读取本机插件库路径失败:", exc)
    if not prefs or not local or not local.get("management_enabled"):
        return
    if not os.path.isdir(prefs.library_path):
        bridge.reset_scoped_configuration()
        machine_config.save({"management_enabled": False})
        print("[插件库] 已配置的库不可用；仅将受管仓库和脚本路径恢复为默认")
        return
    try:
        bridge.ensure_library_dirs(prefs.library_path)
        from .storage.shared_db import SharedDatabase
        report = SharedDatabase(prefs.library_path).initialize()
        if report.status == "CORRUPT":
            print("[插件库] 元数据损坏，已保持只读，跳过自动扫描")
            return
        result = scoped_management.activate(prefs.library_path)
        if result["state"] == "ERROR":
            print("[插件库] 恢复设备配置时存在失败:", result["failures"])
        library.sync_library(prefs.library_path, db.LibraryDB(prefs.library_path, use_cache=False))
    except Exception as exc:
        print("[插件库] 初始化库失败:", exc)


def register():
    try:
        for mod in _modules:
            for cls in getattr(mod, "classes", ()):
                bpy.utils.register_class(cls)
                _registered.append(cls)
        _bootstrap_prefs()
        header.register()
    except Exception:
        try:
            header.unregister()
        except Exception:
            pass
        for cls in reversed(_registered):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
        _registered.clear()
        raise
    # 启动同步：延迟执行，等 Blender 初始化完成（仅当偏好开启时生效）
    try:
        bpy.app.timers.unregister(_sync_manager_state)
    except Exception:
        pass
    try:
        bpy.app.timers.register(_sync_manager_state, first_interval=2.0, persistent=True)
    except Exception:
        pass
    print(f"[插件库] 已启用 v{C.ADDON_VERSION_STR}（投放区为手动扫描）")


def unregister():
    try:
        bpy.app.timers.unregister(_sync_manager_state)
    except Exception:
        pass
    try:
        from .storage import machine_config
        local = machine_config.load()
        root = local.get("library_path")
        if local.get("management_enabled") and root:
            result = scoped_management.deactivate(root)
            if result["state"] == "ERROR":
                print("[插件库] 停用时存在失败:", result["failures"])
    except Exception as exc:
        print("[插件库] 撤销插件库挂载失败:", exc)
    header.unregister()
    for cls in reversed(_registered):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _registered.clear()


if __name__ == "__main__":
    register()
