"""插件库管理器 —— 统一的 Blender 插件资源管理器。

集中存放所有插件（传统插件与扩展插件），提供分类、备注、标签、收藏、
启停与更新检测；通过把库目录注册为 Blender 脚本目录 / 扩展仓库来挂载，
因此升级 Blender 版本后仍可继续沿用同一套库与元数据。
"""

bl_info = {
    "name": "插件库管理器 (Plugin Library Manager)",
    "author": "ZCode",
    "version": (2, 5, 5),
    "blender": (4, 2, 0),
    "location": "3D 视图 > 侧边栏 (N) > 插件库",
    "description": "集中管理所有 Blender 插件：分类、备注、启停、更新检测、自动收编",
    "category": "System",
}

import importlib
import inspect
import os

_PM_WAS_IMPORTED = "bpy" in globals()
import bpy

import sys

from . import self_update

_update_boot_message = ""
_update_backup = None
if not _PM_WAS_IMPORTED:
    try:
        _update_installation = self_update.installed_path(__file__, bpy.utils.user_resource("SCRIPTS"))
    except ValueError:
        pass
    else:
        try:
            _update_backup = self_update.activate_pending(
                _update_installation, bl_info["version"], bpy.app.version)
        except (OSError, SyntaxError, ValueError) as error:
            _update_boot_message = f"未能应用更新，旧版仍可用：{error}"
            print("[插件库]", _update_boot_message)

if _update_backup:
    self_update.load_replacement(sys.modules[__name__], _update_installation, _update_backup)
else:
    from . import constants as C
    from . import (
        archive_jobs,
        bridge,
        compat_task,
        db,
        items,
        library,
        library_jobs,
        migrate,
        native_names,
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
                     native_names, items, compat_task, preferences, library_jobs, operators, ui):
            importlib.reload(_mod)

    _modules = (preferences, operators, ui)
    _registered = []
    _LAST_SYNC_SNAPSHOT = None


    def _current_sync_snapshot(root: str):
        """Pure in-memory snapshot of Blender's enabled addons & managed scopes (zero disk/network I/O)."""
        return (
            root,
            frozenset(bridge.enabled_modules()),
            bridge.capture_scoped_configuration(),
        )


    def _is_blender_exit_or_reload() -> bool:
        """Return True when unregister() is triggered by Blender shutdown or script reload.

        Exiting Blender calls ``addon_utils.disable_all()`` (from ``_on_exit``),
        which must NOT deactivate library management or wipe ``management_enabled``.
        Explicitly disabling this add-on in Preferences calls ``addon_utils.disable()``
        directly (outside ``disable_all``) and still cleanly deactivates & unmounts.
        """
        frame = inspect.currentframe()
        try:
            frame = frame.f_back if frame else None
            while frame is not None:
                code = frame.f_code
                fname = (code.co_filename or "").replace("\\", "/")
                if code.co_name in {"disable_all", "reset_all"} and fname.endswith("addon_utils.py"):
                    return True
                if code.co_name == "_on_exit":
                    return True
                frame = frame.f_back
            return False
        finally:
            del frame


    def _sync_manager_state():
        """Mirror native-panel changes into the active device profile (idle = zero network I/O)."""
        global _LAST_SYNC_SNAPSHOT
        if library_jobs.running():
            return 2.0
        try:
            from .storage import machine_config
            local = machine_config.load()
            root = local.get("library_path")
            if local.get("management_enabled") and root and bridge.managed_library_root() == root:
                snap = _current_sync_snapshot(root)
                if snap == _LAST_SYNC_SNAPSHOT:
                    return 2.0
                if os.path.isdir(root):
                    scoped_management.sync_to_profile(root)
                    _LAST_SYNC_SNAPSHOT = _current_sync_snapshot(root)
                    prefs = bridge.get_prefs()
                    if prefs is not None:
                        items.maybe_rebuild(prefs)
        except Exception as exc:
            print("[插件库] 同步原生配置失败:", exc)
        return 2.0


    def _load_local_prefs():
        """Fast local-only preference hydration (<1ms, reads only local SSD machine.json)."""
        prefs = bridge.get_prefs()
        local = None
        if prefs:
            try:
                from .storage import machine_config
                local = machine_config.initialize()
                path = local.get("library_path")
                preferences._LOADING_MACHINE_PATH = True
                try:
                    prefs.device_name = local.get("device_name", "")
                    prefs.startup_mode = local.get("startup_mode", "remember")
                    if path:
                        prefs.library_path = path
                finally:
                    preferences._LOADING_MACHINE_PATH = False
            except Exception as exc:
                print("[插件库] 初始化本机配置失败:", exc)
        return prefs, local


    def _bootstrap_prefs():
        """Load machine-local identity and resume only an explicitly active library."""
        prefs, local = _load_local_prefs()
        if not prefs or not local or not local.get("management_enabled"):
            return
        try:
            library_jobs.start(prefs, prefs.library_path, mode="resume")
        except Exception as exc:
            print("[插件库] 初始化库失败:", exc)


    def _deferred_startup():
        """Deferred post-boot hook: resumes the library in background after Blender opens."""
        try:
            _bootstrap_prefs()
        except Exception as exc:
            print("[插件库] 延迟恢复插件库失败:", exc)
        return None


    def _apply_launch_sync():
        """兼容入口：干净启动已集成进 library_jobs 启动激活，避免双次覆盖。"""
        prefs = bridge.get_prefs()
        if not prefs or not prefs.library_path:
            return
        if not (prefs.startup_mode == "clean" or prefs.sync_startup_on_launch):
            return
        if library_jobs.running():
            return 0.2
        from .storage import machine_config
        if (not machine_config.load().get("management_enabled")
                or bridge.managed_library_root() != prefs.library_path
                or not bridge.is_registered(prefs.library_path)):
            return
        try:
            from .db import LibraryDB
            disable_unmarked = (prefs.startup_mode == "clean") or bool(prefs.startup_disable_unmarked)
            library.apply_startup(prefs.library_path, LibraryDB(prefs.library_path),
                                  enable_marked=True,
                                  disable_unmarked=disable_unmarked)
            from . import items
            items.rebuild_items(prefs)
        except Exception as exc:
            print("[插件库] 启动同步自启状态失败:", exc)


    def register():
        global _LAST_SYNC_SNAPSHOT
        _LAST_SYNC_SNAPSHOT = None
        try:
            for mod in _modules:
                for cls in getattr(mod, "classes", ()):
                    bpy.utils.register_class(cls)
                    _registered.append(cls)
            # 仅在注册阶段读取本机轻量配置并挂载零 I/O 的原生中文名钩子（<1ms），不阻塞启动
            _load_local_prefs()
            try:
                native_names.install()
            except Exception as exc:
                print("[插件库] 挂载原生中文显示名失败:", exc)
        except Exception:
            try:
                operators.shutdown_compat_task()
            except Exception:
                pass
            for cls in reversed(_registered):
                try:
                    bpy.utils.unregister_class(cls)
                except Exception:
                    pass
            _registered.clear()
            raise
        # 延迟到 Blender 启动完成后再挂载原生中文名与后台恢复插件库，避免拖慢程序启动
        for fn in (_deferred_startup, _sync_manager_state):
            try:
                bpy.app.timers.unregister(fn)
            except Exception:
                pass
        try:
            bpy.app.timers.register(_deferred_startup, first_interval=0.25)
        except Exception:
            # 无计时器环境（极简测试上下文）降级为立即挂载
            _deferred_startup()
        try:
            bpy.app.timers.register(_sync_manager_state, first_interval=3.0, persistent=True)
        except Exception:
            pass
        print(f"[插件库] 已启用 v{C.ADDON_VERSION_STR}（投放区为手动扫描）")


    def unregister():
        global _LAST_SYNC_SNAPSHOT
        for fn in (_deferred_startup, _sync_manager_state, _apply_launch_sync):
            try:
                bpy.app.timers.unregister(fn)
            except Exception:
                pass
        try:
            native_names.uninstall()
        except Exception as exc:
            print("[插件库] 卸载原生中文显示名失败:", exc)
        try:
            operators.shutdown_compat_task()
            operators.shutdown_self_update()
        except Exception as exc:
            print("[插件库] 关闭兼容性测试任务失败:", exc)
        try:
            archive_jobs.shutdown()
            library_jobs.shutdown()
        except Exception as exc:
            print("[插件库] 关闭安装任务失败:", exc)
        try:
            from .storage import machine_config
            local = machine_config.load()
            root = local.get("library_path")
            if local.get("management_enabled") and root:
                if _is_blender_exit_or_reload():
                    # Blender 正常退出或重载脚本：保留管理状态，仅同步有变化的启用意图
                    if bridge.managed_library_root() == root and os.path.isdir(root):
                        if _current_sync_snapshot(root) != _LAST_SYNC_SNAPSHOT:
                            scoped_management.sync_to_profile(root)
                else:
                    # 用户在偏好设置中显式停用本插件：完整撤销接管并恢复默认
                    result = scoped_management.deactivate(root)
                    if result["state"] == "ERROR":
                        print("[插件库] 停用时存在失败:", result["failures"])
        except Exception as exc:
            print("[插件库] 退出/停用处理失败:", exc)
        _LAST_SYNC_SNAPSHOT = None
        for cls in reversed(_registered):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
        _registered.clear()


    if __name__ == "__main__":
        register()
