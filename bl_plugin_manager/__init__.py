"""插件库管理器 —— 统一的 Blender 插件资源管理器。

集中存放所有插件（传统插件与扩展插件），提供分类、备注、标签、收藏、
启停与更新检测；通过把库目录注册为 Blender 脚本目录 / 扩展仓库来挂载，
因此升级 Blender 版本后仍可继续沿用同一套库与元数据。
"""

bl_info = {
    "name": "插件库管理器 (Plugin Library Manager)",
    "author": "ZCode",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D 视图 > 侧边栏 (N) > 插件库",
    "description": "集中管理所有 Blender 插件：分类、备注、启停、更新检测、自动收编",
    "category": "System",
}

import importlib
import os

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
    scan,
    store,
    ui,
    updates,
    watcher,
)

# 支持在 Blender 文本编辑器中热重载
if "bpy" in locals():  # pragma: no cover
    for _mod in (C, scan, db, bridge, library, store, updates, migrate, watcher,
                 items, preferences, operators, ui):
        importlib.reload(_mod)

_modules = (preferences, operators, ui)
_registered = []


def _apply_startup_on_launch():
    """启动时按「自启」标记同步（仅在偏好里开启时执行）。

    默认只启用标记为自启的插件，不主动停用其它插件，避免误关。
    作为定时器延迟执行，等 Blender 初始化完成后再动插件状态。
    """
    prefs = bridge.get_prefs()
    if not prefs or not prefs.library_path:
        return None
    if not getattr(prefs, "sync_startup_on_launch", False):
        return None
    try:
        lib_db = db.LibraryDB(prefs.library_path)
        stats = library.apply_startup(
            prefs.library_path, lib_db,
            enable_marked=True,
            disable_unmarked=bool(getattr(prefs, "startup_disable_unmarked", False)),
        )
        print(f"[插件库] 启动同步自启: 启用 {stats['enabled']}，"
              f"停用 {stats['disabled']}，失败 {len(stats['failed'])}")
    except Exception as exc:
        print("[插件库] 启动同步失败:", exc)
    return None


def _bootstrap_prefs():
    """库目录已存在时自动同步并重新挂载。

    库目录存在即代表用户已确认使用该库，因此在（例如升级 Blender 后的）新版本里
    启用本插件时，自动把库重新注册为脚本目录与扩展仓库，无需手动点击。
    """
    prefs = bridge.get_prefs()
    if prefs:
        try:
            from .storage import machine_config
            local = machine_config.load()
            path = local.get("library_path")
            if path:
                preferences._LOADING_MACHINE_PATH = True
                try:
                    prefs.library_path = path
                finally:
                    preferences._LOADING_MACHINE_PATH = False
        except Exception as exc:
            print("[插件库] 读取本机插件库路径失败:", exc)
    if not prefs or not prefs.library_path:
        return
    if not os.path.isdir(prefs.library_path):
        return
    try:
        bridge.ensure_library_dirs(prefs.library_path)
        library.sync_library(prefs.library_path, db.LibraryDB(prefs.library_path))
        if not bridge.is_registered(prefs.library_path):
            bridge.register_library(prefs.library_path, save=True)
            print("[插件库] 已自动挂载插件库")
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
        bpy.app.timers.unregister(_apply_startup_on_launch)
    except Exception:
        pass
    try:
        bpy.app.timers.register(_apply_startup_on_launch, first_interval=2.0)
    except Exception:
        pass
    print(f"[插件库] 已启用 v{C.ADDON_VERSION_STR}（投放区为手动扫描）")


def unregister():
    try:
        bpy.app.timers.unregister(_apply_startup_on_launch)
    except Exception:
        pass
    header.unregister()
    for cls in reversed(_registered):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _registered.clear()


if __name__ == "__main__":
    register()
