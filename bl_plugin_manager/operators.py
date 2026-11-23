"""操作符：刷新、启停、导入、扫描、更新检测、分类与备注维护。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    StringProperty,
)
from bpy.types import Operator

from . import (archive_jobs, bridge, compat_task, constants as C, library, library_jobs, migrate, scan,
               scoped_management, self_update, store, updates, watcher)
from .db import LibraryDB, compatibility_environment, now_iso
from .storage.catalog import CatalogConflictError
from .tasks import TaskController
from .security.paths import UnsafeLibraryPathError, resolve_record_path


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------
def _prefs(context):
    return bridge.get_prefs()


def _db(context) -> LibraryDB | None:
    prefs = _prefs(context)
    if not prefs or prefs.library_job_running or not prefs.library_path or not os.path.isdir(prefs.library_path):
        return None
    database = LibraryDB(prefs.library_path)
    return database if database.status not in {"CORRUPT", "UNSUPPORTED"} else None


def _selected(context) -> dict | None:
    db = _db(context)
    prefs = _prefs(context)
    if not db or not prefs:
        return None
    return db.get(prefs.selected_key)


def _report(self, ok: bool, msg: str, kind: str = "INFO"):
    """报告结果并返回 Blender 期望的操作符返回值集合。"""
    if ok:
        self.report({kind}, msg)
        return {"FINISHED"}
    self.report({"ERROR"}, msg)
    return {"CANCELLED"}


def _sync_device_config(prefs) -> str:
    try:
        scoped_management.sync_to_profile(prefs.library_path)
        return ""
    except Exception as exc:
        return str(exc)


class _PMBase(Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        prefs = bridge.get_prefs()
        return prefs is not None and not prefs.library_job_running


_SELF_UPDATE_TASK = TaskController()


def _fetch_self_update(installation, current, blender_version):
    check = self_update.latest_release(current)
    task = _SELF_UPDATE_TASK
    if not check or task.cancelled:
        return None
    with tempfile.TemporaryDirectory(prefix="blpm-github-") as directory:
        zip_path = Path(directory) / "release.zip"
        ok, error = store.download(
            check.url, str(zip_path), check.digest, check.size, cancelled=lambda: task.cancelled)
        if not ok:
            raise RuntimeError(error)
        self_update.stage_update(installation, zip_path, check, blender_version,
                                 cancelled=lambda: task.cancelled)
    return check.tag


def _self_update_tick():
    prefs = bridge.get_prefs()
    if not prefs:
        _SELF_UPDATE_TASK.cancel()
        return None
    if _SELF_UPDATE_TASK.running:
        return 0.2
    prefs.self_update_busy = False
    if _SELF_UPDATE_TASK.error:
        prefs.self_update_status = f"更新失败：{_SELF_UPDATE_TASK.error}"
    elif _SELF_UPDATE_TASK.result:
        prefs.self_update_status = f"{_SELF_UPDATE_TASK.result} 已就绪，重启 Blender 后生效。"
    else:
        prefs.self_update_status = "GitHub 暂无比当前版本更新的正式发行包。"
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()
    return None


def shutdown_self_update():
    _SELF_UPDATE_TASK.cancel()
    if bpy.app.timers.is_registered(_self_update_tick):
        bpy.app.timers.unregister(_self_update_tick)


class PM_OT_github_update(Operator):
    bl_idname = "plugin_manager.github_update"
    bl_label = "检查并下载 GitHub 更新"
    bl_description = "下载插件库管理器的正式发行包，已下载的新版将在下次启动时应用"

    def execute(self, context):
        prefs = _prefs(context)
        if not bpy.app.online_access:
            return _report(self, False, "请先在 Blender 系统设置中允许联网")
        if _SELF_UPDATE_TASK.running or bpy.app.timers.is_registered(_self_update_tick):
            return _report(self, False, "正在检查 GitHub 更新")
        try:
            installation = self_update.installed_path(__file__, bpy.utils.user_resource("SCRIPTS"))
            if self_update.pending_path(installation).exists():
                return _report(self, True, "已有更新就绪，重启 Blender 后生效")
            _SELF_UPDATE_TASK.start(_fetch_self_update, installation, C.ADDON_VERSION,
                                    tuple(bpy.app.version))
            bpy.app.timers.register(_self_update_tick, first_interval=0.2, persistent=True)
        except (OSError, RuntimeError, ValueError) as error:
            _SELF_UPDATE_TASK.cancel()
            return _report(self, False, str(error))
        prefs.self_update_busy = True
        prefs.self_update_status = "正在检查或下载 GitHub 更新…"
        return _report(self, True, prefs.self_update_status)


# ---------------------------------------------------------------------------
# 库注册与刷新
# ---------------------------------------------------------------------------
class PM_OT_setup_library(_PMBase):
    bl_idname = "plugin_manager.setup_library"
    bl_label = "启用/修复插件库"
    bl_description = "把插件库目录注册为 Blender 脚本目录与扩展仓库并刷新"

    def execute(self, context):
        prefs = _prefs(context)
        path = prefs.pending_library_path or prefs.library_path
        if not path:
            return _report(self, False, "请先选择插件库目录")
        try:
            library_jobs.start(prefs, path)
        except (RuntimeError, OSError, ValueError) as exc:
            return _report(self, False, str(exc))
        return _report(self, True, prefs.library_job_status)


class PM_OT_unify_store(_PMBase):
    bl_idname = "plugin_manager.unify_store"
    bl_label = "迁移原官方扩展"
    bl_description = ("把启用管理前位于 Blender 官方目录中的扩展安全迁移进插件库；\n"
                      "只移动确认导入成功的条目，失败项保留在原处")

    move: BoolProperty(name="迁移官方目录中的扩展到库（移动）", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")

        root = prefs.library_path
        # 1) 记录官方仓库当前目录（迁移用）
        _, repo = bridge.find_official_repo()
        src_dir = ""
        if repo is not None:
            src_dir = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
            # 若已经指向库，则无需迁移
            if os.path.normcase(os.path.abspath(src_dir)) == os.path.normcase(
                    os.path.abspath(os.path.join(root, C.DIR_EXTENSIONS))):
                res = bridge.unify_store_with_library(root)
                error = _sync_device_config(prefs)
                if error:
                    return _report(self, False, f"商店已切换，但设备配置保存失败: {error}")
                return _report(self, True, "官方商店已在使用本插件库目录")

        # 2) 把官方目录里的扩展迁移进库
        moved, failed = 0, []
        if src_dir and os.path.isdir(src_dir):
            for e in sorted(os.scandir(src_dir), key=lambda x: x.name.lower()):
                if not e.is_dir() or scan.is_ignored(e.name):
                    continue
                try:
                    library.import_plugin_dir(e.path, root, db,
                                              move=self.move, origin="官方商店迁移")
                    moved += 1
                except Exception as exc:
                    # 已在库中（同 id）不算失败
                    if "已存在" in str(exc) or "exists" in str(exc):
                        continue
                    failed.append(f"{e.name}: {exc}")
            # import_plugin_dir(move=True) only moves a successfully imported
            # source.  Never sweep the remaining directory: failed or unknown
            # entries belong to the user and must stay recoverable.

        # 3) 官方商店仓库指向库；联网权限仍由用户在 Blender 系统设置中控制
        res = bridge.unify_store_with_library(root)
        library.sync_library(root, db)
        sync_error = _sync_device_config(prefs)
        from . import items as _items

        _items.rebuild_items(prefs)

        msg = f"已迁移 {moved} 个扩展到插件库"
        if failed:
            msg += f"，{len(failed)} 个失败"
        msg += "；官方商店已指向插件库"
        if sync_error:
            msg += f"；设备配置保存失败: {sync_error}"
        if not res.get("online"):
            msg += "（请到 偏好设置>系统 手动开启「允许联网访问」）"
        for f_ in failed[:8]:
            print("[插件库] 迁移失败:", f_)
        self.report({"INFO"}, msg)
        bpy.ops.plugin_manager.show_report()
        return {"FINISHED"}


class PM_OT_pick_library_path(_PMBase):
    bl_idname = "plugin_manager.pick_library_path"
    bl_label = "选择插件库目录"
    bl_description = "在侧边栏直接选择/更改插件库位置，随后自动侦测该目录内容"

    directory: StringProperty(subtype="DIR_PATH", default="")
    cancel_preview: BoolProperty(options={"HIDDEN"})
    def invoke(self, context, event):
        prefs = _prefs(context)
        if self.cancel_preview:
            return self.execute(context)
        self.directory = prefs.pending_library_path or prefs.library_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        prefs = _prefs(context)
        if self.cancel_preview:
            prefs.pending_library_path = ""
            prefs.library_preview = ""
            prefs.library_job_status = ""
            prefs.library_job_failed = False
            return {"FINISHED"}
        if not self.directory:
            return _report(self, False, "未选择目录")
        try:
            library_jobs.start(prefs, self.directory, mode="preview")
        except (RuntimeError, OSError, ValueError) as exc:
            return _report(self, False, str(exc))
        return _report(self, True, prefs.library_job_status)


class PM_OT_unmount_library(_PMBase):
    bl_idname = "plugin_manager.unmount_library"
    bl_label = "卸载挂载"
    bl_description = "把插件库从 Blender 的脚本目录/扩展仓库中撤销挂载（不删除任何插件文件）"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs or not prefs.library_path:
            return _report(self, False, "插件库未就绪")
        if prefs.compat_running or prefs.archive_running:
            return _report(self, False, "请先结束当前任务")
        from .storage import machine_config
        if not machine_config.load().get("management_enabled"):
            return _report(self, True, "插件库管理已停止")
        result = scoped_management.deactivate(prefs.library_path)
        if result["state"] == "ERROR":
            return _report(self, False, f"停止管理时存在失败: {result['failures'][:3]}")
        if bridge.is_registered(prefs.library_path):
            return _report(self, False, "撤销挂载未完全生效")
        return _report(self, True, "已撤销挂载（插件文件保留在库中）")


class PM_OT_refresh(_PMBase):
    bl_idname = "plugin_manager.refresh"
    bl_label = "刷新列表"
    bl_description = "重新扫描插件库并同步启用状态"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        _status("插件库：正在扫描插件目录…")
        try:
            library.sync_library(prefs.library_path, db)
        finally:
            _clear_status()
        _redraw()
        return _report(self, True, "列表已刷新")


class PM_OT_show_warnings(_PMBase):
    bl_idname = "plugin_manager.show_warnings"
    bl_label = "查看诊断"
    bl_description = "显示插件库状态诊断信息"

    def execute(self, context):
        prefs = _prefs(context)
        state = bridge.library_state(prefs.library_path)
        database = LibraryDB(prefs.library_path) if prefs.library_path else None
        lines = [
            f"库路径: {prefs.library_path}",
            f"资料库: {database.status if database else '未配置'}",
            f"脚本目录已注册: {state['script_dir']}",
            f"扩展仓库已注册: {state['repo']}",
        ]
        for ln in lines:
            print("[插件库]", ln)
        self.report({"INFO"}, "；".join(lines))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 启停
# ---------------------------------------------------------------------------
class PM_OT_toggle(_PMBase):
    bl_idname = "plugin_manager.toggle"
    bl_label = "启用/停用"
    bl_description = "启用或停用当前选中的插件"

    key: StringProperty(default="")
    enable: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        key = self.key or prefs.selected_key
        rec = db.get(key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        if not rec.get("module"):
            return _report(self, False, "无法解析插件模块名")
        try:
            scoped_management.record_activation(prefs.library_path, key, self.enable)
        except (RuntimeError, OSError, ValueError) as exc:
            return _report(self, False, f"无法保存启用意图: {exc}")
        if rec.get("missing"):
            return _report(self, True, "已保存启用意图；插件文件恢复后可重新应用")
        ok, err = bridge.set_enabled(rec["module"], self.enable)
        rec["enabled"] = bridge.is_module_enabled(rec["module"])
        rec.pop("startup_override", None)
        # 记录/清除上次启用失败的原因，便于在详情里排查
        rec["last_error"] = "" if ok else (err or "未知错误")
        db.save()
        if ok:
            from . import items
            from .services import presets as preset_service

            if self.enable:
                preset_service.note_manual_enable(db, key)
            else:
                preset_service.note_manual_disable(key)
            if prefs:
                items.rebuild_items(prefs)
            _redraw()
            verb = "已启用" if self.enable else "已停用"
            return _report(self, True, f"{verb}: {library.effective_name(rec)}")
        return _report(self, False, f"操作失败: {err}")


class PM_OT_set_startup(_PMBase):
    bl_idname = "plugin_manager.set_startup"
    bl_label = "设为自启 / 取消自启"
    bl_description = "标记插件是否随 Blender 启动自动启用（可对选中项批量应用）"

    key: StringProperty(default="")
    value: BoolProperty(default=True)
    use_selection: BoolProperty(default=False)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, self.key, self.use_selection)
        if not keys:
            return _report(self, False, "未找到插件")
        n = 0
        for k in keys:
            rec = db.get(k)
            if not rec:
                continue
            rec["startup"] = bool(self.value)
            n += 1
        db.save()
        from . import items

        items.rebuild_items(prefs)
        verb = "设为自启" if self.value else "取消自启"
        return _report(self, True, f"已{verb}: {n} 个插件")


class PM_OT_verify_compat(_PMBase):
    bl_idname = "plugin_manager.verify_compat"
    bl_label = "一键测试插件支持"
    bl_description = ("分步真实加载插件测出可用性；不阻塞界面，可随时取消")

    scope: EnumProperty(
        name="测试范围",
        items=compat_task.SCOPE_ITEMS,
        default=compat_task.SCOPE_CONTINUE,
    )
    include_enabled: BoolProperty(
        name="也测试已启用的插件",
        description="已启用的插件通常可用；勾选后一并复测（更慢）",
        default=False,
    )

    def invoke(self, context, event):
        prefs = _prefs(context)
        if prefs is not None and getattr(prefs, "compat_running", False):
            return _report(self, False, "已有测试任务在运行，请等待完成或先取消")
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        if getattr(prefs, "compat_running", False):
            return _report(self, False, "已有测试任务在运行，请等待完成或先取消")
        if prefs.archive_running:
            return _report(self, False, "请先结束安装或更新任务")

        selection = compat_task.select_targets(
            list(db.plugins.items()), bridge.enabled_modules(),
            include_enabled=self.include_enabled, scope=self.scope,
        )
        if not selection.targets:
            return _report(self, True,
                           compat_task.no_targets_text(selection, self.scope))
        _compat_start(prefs, db, selection, self.include_enabled, self.scope)
        return _report(
            self, True,
            f"已开始测试 {len(selection.targets)} 个插件；可在侧栏查看进度或取消")


# ---------------------------------------------------------------------------
# 兼容性测试任务：计时器分步驱动
# ---------------------------------------------------------------------------
_COMPAT: dict = {"state": None, "db": None, "prefs": None}


class _CompatBridgeAPI:
    """把任务逻辑需要的 Blender 侧能力收拢到一个注入对象里。"""

    def set_enabled(self, module: str, enabled: bool):
        return bridge.set_enabled(module, enabled)

    def is_module_enabled(self, module: str) -> bool:
        return bridge.is_module_enabled(module)

    def module_residue(self, module: str) -> int:
        return bridge.module_residue(module)


def _compat_start(prefs, db, selection, include_enabled: bool, scope: str) -> None:
    """建立任务状态并把每步进度镜像到偏好属性（供侧栏绘制）。"""
    state = compat_task.TaskState(selection.targets, include_enabled, scope)
    _COMPAT.update(state=state, db=db, prefs=prefs)
    prefs.compat_running = True
    prefs.compat_cancelling = False
    prefs.compat_total = state.total
    prefs.compat_done = 0
    prefs.compat_ok = 0
    prefs.compat_fail = 0
    prefs.compat_current = state.current_name
    _status(f"插件库：正在测试 0/{state.total} …")
    _compat_register_timer()
    _redraw()


def _compat_register_timer() -> None:
    try:
        if not bpy.app.timers.is_registered(_compat_tick):
            bpy.app.timers.register(_compat_tick, first_interval=0.0)
    except Exception as exc:
        print("[插件库] 无法启动兼容性测试计时器:", exc)


def _compat_stop_timer() -> None:
    try:
        if bpy.app.timers.is_registered(_compat_tick):
            bpy.app.timers.unregister(_compat_tick)
    except Exception:
        pass


def _compat_tick():
    """每个计时器事件只处理一个插件，然后在事件之间把控制权交还 Blender。"""
    state, prefs, db = _COMPAT.get("state"), _COMPAT.get("prefs"), _COMPAT.get("db")
    if state is None or prefs is None or db is None:
        _compat_stop_timer()
        return None
    try:
        if state.cancelled or not state.pending():
            _compat_finish(state, db, prefs, cancelled=state.cancelled)
            return None
        outcome = compat_task.step(state, _CompatBridgeAPI())
        if outcome is not None:
            record = db.get(outcome.key) or {}
            record.update(outcome.fields)
            record["load_environment"] = compatibility_environment()
            record["key"] = outcome.key
            db.plugins[outcome.key] = record
            # 每步落盘：取消或异常时已完成的结果不丢。
            db.save()
            prefs.compat_done = state.index
            prefs.compat_ok = state.ok
            prefs.compat_fail = state.fail
            prefs.compat_current = state.current_name
        _status(f"插件库：正在测试 {state.index}/{state.total} — {state.current_name}")
        _redraw()
        if not state.pending():
            _compat_finish(state, db, prefs, cancelled=False)
            return None
        return 0.02
    except Exception as exc:  # noqa: BLE001 — 任务必须清理并留痕
        print("[插件库] 兼容性测试任务异常:", exc)
        _compat_finish(state, db, prefs, cancelled=False, error=str(exc))
        return None


def _compat_finish(state, db, prefs, cancelled: bool, error: str = "") -> None:
    """统一收尾：注销计时器、清状态栏、存结果、刷新列表、生成摘要。"""
    _compat_stop_timer()
    _clear_status()
    try:
        db.save()
    except Exception as exc:
        print("[插件库] 保存测试结果失败:", exc)

    prefs.report_items.clear()
    for item in state.fails:
        entry = prefs.report_items.add()
        entry.name = item.get("name") or ""
        entry.path = item.get("key") or ""
        entry.kind = "兼容性"
        entry.status = "failed"
        entry.detail = item.get("error") or ""
    prefs.report_summary = compat_task.summary_text(state, cancelled=cancelled)

    log_entries = [{"name": i.get("name"), "path": i.get("key"), "kind": "兼容性",
                    "status": "failed", "detail": i.get("error")} for i in state.fails]
    if error:
        prefs.report_summary += f"；任务异常：{error}"
        log_entries.append({"name": "任务异常", "path": "", "kind": "兼容性",
                            "status": "failed", "detail": error})
    _write_report_log(prefs, {"entries": log_entries})

    from . import items

    items.rebuild_items(prefs)
    prefs.compat_running = False
    prefs.compat_cancelling = False
    prefs.compat_current = ""
    try:
        bridge.save_prefs()
    except Exception:
        pass
    _redraw()
    # 失败项目进入报告；取消不报告为错误，也不弹窗。
    if state.fails and not cancelled:
        try:
            bpy.ops.plugin_manager.show_report()
        except Exception:
            pass
    _COMPAT.update(state=None, db=None, prefs=None)


def shutdown_compat_task() -> None:
    """停用插件或退出时调用：取消任务并完成收尾，避免计时器悬空。"""
    state, prefs, db = _COMPAT.get("state"), _COMPAT.get("prefs"), _COMPAT.get("db")
    if state is not None and db is not None and prefs is not None:
        state.request_cancel()
        _compat_finish(state, db, prefs, cancelled=True)
    _compat_stop_timer()


class PM_OT_cancel_compat(_PMBase):
    bl_idname = "plugin_manager.cancel_compat"
    bl_label = "取消兼容性测试"
    bl_description = "当前插件处理结束后停止后续测试；已完成结果会保留"

    def execute(self, context):
        state = _COMPAT.get("state")
        prefs = _prefs(context)
        if state is None or prefs is None:
            return _report(self, False, "没有正在运行的测试")
        # 不中断正在进行的加载/卸载，只设置取消请求。
        state.request_cancel()
        prefs.compat_cancelling = True
        _status("插件库：正在取消，等待当前插件处理结束…")
        _redraw()
        return _report(self, True, "已请求取消：当前插件处理结束后停止，已完成结果会保留")


class PM_OT_cleanup_residue(_PMBase):
    bl_idname = "plugin_manager.cleanup_residue"
    bl_label = "清理失败插件残留"
    bl_description = "清理插件启用失败时留下的残留面板/属性，避免界面持续报错"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")

        enabled_set = bridge.enabled_modules()
        _status("插件库：正在清理注册残留…")
        cleaned, still, skipped = [], [], 0
        try:
            for key, rec in db.plugins.items():
                mod = rec.get("module")
                if not mod or rec.get("missing"):
                    continue
                # 只处理"未启用"却有残留的插件
                if mod in enabled_set:
                    skipped += 1
                    continue
                before = bridge.module_residue(mod)
                if before <= 0:
                    rec["residue"] = 0
                    continue
                left, cerr = bridge.cleanup_residue(mod)
                rec["residue"] = left
                if left <= 0:
                    cleaned.append(rec.get("name") or mod)
                else:
                    still.append({"name": rec.get("name") or mod, "left": left})
                db.plugins[key] = rec
        finally:
            _clear_status()

        db.save()
        bridge.refresh_blender(full=False)
        from . import items

        items.rebuild_items(prefs)

        msg = f"已清理 {len(cleaned)} 个插件的残留"
        if still:
            names = "、".join(x["name"] for x in still[:3])
            msg += f"；{len(still)} 个仍有残留（{names}…），需重启 Blender"
        return _report(self, not still, msg)


class PM_OT_apply_startup(_PMBase):
    bl_idname = "plugin_manager.apply_startup"
    bl_label = "立即同步自启状态"
    bl_description = "把自启标记立即应用到 Blender（启用标记自启的插件）"

    disable_unmarked: BoolProperty(
        name="同时停用未标记自启的插件",
        description="勾选后，未标记自启的插件会被停用（更省资源，请先整理好标记）",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        _status("插件库：正在同步自启状态…")
        try:
            stats = library.apply_startup(prefs.library_path, db,
                                          enable_marked=True,
                                          disable_unmarked=self.disable_unmarked)
        finally:
            _clear_status()
        sync_error = _sync_device_config(prefs)
        from . import items

        items.rebuild_items(prefs)
        msg = f"已启用 {stats['enabled']}"
        if self.disable_unmarked:
            msg += f"，已停用 {stats['disabled']}"
        if stats.get("skipped_broken"):
            msg += f"，跳过不兼容/失败 {stats['skipped_broken']} 个"
        if stats["failed"]:
            bpy.ops.plugin_manager.show_report()
            msg += f"，{len(stats['failed'])} 个失败（见报告）"
        if sync_error:
            msg += f"；设备配置保存失败: {sync_error}"
        return _report(self, not stats["failed"] and not sync_error, msg)


# ---------------------------------------------------------------------------
# 批量操作（多选）
# ---------------------------------------------------------------------------
def _target_keys(prefs, single_key: str, use_selection: bool) -> list[str]:
    """确定批量操作的目标：当前筛选可见列表中的勾选项，或单个 key。

    批量操作严格作用于当前筛选结果里勾选的插件（所见即所选），
    不会误操作此前在其它筛选条件下遗留的隐藏勾选项。
    """
    from . import items

    if use_selection:
        return items.selected_keys(prefs)
    if single_key:
        return [single_key]
    if prefs.selected_key:
        return [prefs.selected_key]
    return []


def _status(msg: str) -> None:
    """在状态栏显示提示，让用户知道耗时操作正在进行（避免误以为卡死）。"""
    try:
        for w in bpy.context.window_manager.windows:
            w.status_text_set(msg)
    except Exception:
        pass


def _clear_status() -> None:
    try:
        for w in bpy.context.window_manager.windows:
            w.status_text_set(None)
    except Exception:
        pass


def _redraw():
    """请求界面重绘，让勾选状态立刻反映出来。"""
    try:
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                a.tag_redraw()
    except Exception:
        pass


class PM_OT_toggle_select(_PMBase):
    bl_idname = "plugin_manager.toggle_select"
    bl_label = "勾选"
    bl_description = "勾选/取消勾选该插件（用于批量操作）"

    key: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        from . import items

        # 默认取反；显式指定时按指定值
        new_val = not any(it.key == self.key and it.selected for it in prefs.plugin_items)
        items.set_selected(self.key, new_val)
        for it in prefs.plugin_items:
            if it.key == self.key:
                it.selected = new_val
                break
        _redraw()
        return {"FINISHED"}


class PM_OT_select_all(_PMBase):
    bl_idname = "plugin_manager.select_all"
    bl_label = "全选/全不选"

    value: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        from . import items

        if self.value:
            # 仅选中当前筛选结果，清空此前其它筛选下遗留的隐藏勾选项
            items.clear_selection()
            for it in prefs.plugin_items:
                items.set_selected(it.key, True)
        else:
            items.clear_selection()
        for it in prefs.plugin_items:
            it.selected = bool(self.value)
        _redraw()
        n = sum(1 for it in prefs.plugin_items if it.selected)
        return _report(self, True, f"已勾选当前筛选结果 {n} 个" if self.value else "已清空勾选")


def _set_plugins_enabled(operator, prefs, db, records, enabled):
    if not records:
        return _report(operator, False, "没有可操作的插件")
    try:
        scoped_management.record_activations(prefs.library_path, (rec["key"] for rec in records), enabled)
    except (OSError, RuntimeError, ValueError) as error:
        return _report(operator, False, f"无法保存启用意图: {error}")
    done = skipped = failed = 0
    for record in records:
        module = record.get("module")
        if not module or record.get("missing"):
            skipped += 1
            continue
        ok, error = bridge.set_enabled(module, enabled)
        record["enabled"] = bridge.is_module_enabled(module)
        record["last_error"] = "" if ok else error or "未知错误"
        record.pop("startup_override", None)
        if ok:
            done += 1
        else:
            failed += 1
    db.save()
    from . import items

    items.rebuild_items(prefs)
    _redraw()
    action = "启用" if enabled else "停用"
    message = f"已{action} {done} 项；失败 {failed}，仅保存意图 {skipped}"
    return _report(operator, True, message, kind="WARNING" if failed or skipped else "INFO")


class PM_OT_batch_enable(_PMBase):
    bl_idname = "plugin_manager.batch_enable"
    bl_label = "批量启用/停用"

    value: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, "", True)
        if not keys:
            return _report(self, False, "请先勾选要操作的插件")
        return _set_plugins_enabled(self, prefs, db,
                                    [db.plugins[key] for key in keys if key in db.plugins], self.value)


class PM_OT_batch_set_category(_PMBase):
    bl_idname = "plugin_manager.batch_set_category"
    bl_label = "批量归入分类"

    category: bpy.props.EnumProperty(name="分类", items=_category_enum)

    def invoke(self, context, event):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        from . import items as _items

        _items.refresh_cache(prefs)
        if not _items.selected_keys(prefs):
            return _report(self, False, "请先在当前筛选结果中勾选要归类的插件")
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, "", True)
        if not keys:
            return _report(self, False, "请先勾选要归类的插件")
        cat = self.category or C.DEFAULT_CATEGORY
        if cat != C.DEFAULT_CATEGORY:
            db.ensure_category(cat)
        for k in keys:
            if db.get(k):
                db.set_plugin_groups(k, [cat] if cat != C.DEFAULT_CATEGORY else [])
        db.save()
        from . import items

        items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, f"已将 {len(keys)} 个插件归入「{cat}」")


class PM_OT_batch_set_startup(_PMBase):
    bl_idname = "plugin_manager.batch_set_startup"
    bl_label = "批量自启标记"

    value: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, "", True)
        if not keys:
            return _report(self, False, "请先勾选要操作的插件")
        for k in keys:
            rec = db.get(k)
            if rec:
                rec["startup"] = bool(self.value)
        db.save()
        from . import items

        items.rebuild_items(prefs)
        verb = "设为自启" if self.value else "取消自启"
        _redraw()
        return _report(self, True, f"已批量{verb} {len(keys)} 个")


def _apply_preset_plan_op(op, prefs, db, plan: dict, label: str):
    from . import items
    from .services import presets as preset_service

    stats = preset_service.apply_preset_plan(
        db, plan,
        set_enabled_fn=bridge.set_enabled,
        is_enabled_fn=bridge.is_module_enabled,
    )
    if prefs and prefs.library_path and bridge.managed_library_root() == prefs.library_path:
        if stats["enabled_keys"]:
            scoped_management.record_activations(prefs.library_path, stats["enabled_keys"], True)
        if stats["disabled_keys"]:
            scoped_management.record_activations(prefs.library_path, stats["disabled_keys"], False)
    if prefs:
        items.rebuild_items(prefs)
    _redraw()
    parts = [label]
    if stats["enabled"]:
        parts.append(f"启用 {stats['enabled']} 个")
    if stats["disabled"]:
        parts.append(f"停用 {stats['disabled']} 个")
    if stats["kept_shared"]:
        parts.append(f"共有保留 {stats['kept_shared']} 个")
    if stats["kept_manual"]:
        parts.append(f"保留临时 {stats['kept_manual']} 个")
    if stats["skipped_broken"]:
        parts.append(f"跳过不兼容/失败 {stats['skipped_broken']} 个")
    if stats["failed"]:
        parts.append(f"失败 {len(stats['failed'])} 个")
    return _report(op, not stats["failed"], " · ".join(parts))


class PM_OT_enable_pack(_PMBase):
    bl_idname = "plugin_manager.enable_pack"
    bl_label = "启用当前分类全部"
    bl_description = "一次性启用当前分类下所有兼容插件（自动跳过已知不兼容或失败插件）"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        from .services import presets as preset_service

        target = prefs.active_category
        if target and target not in ("", "全部", C.DEFAULT_CATEGORY):
            records = [rec for key, rec in db.plugins.items() if target in db.plugin_groups(key)]
        else:
            records = [rec for key, rec in db.plugins.items() if not db.plugin_groups(key)]
        runnable = [r for r in records if not preset_service.is_known_broken(r)]
        skipped_broken = len(records) - len(runnable)
        if not runnable:
            msg = f"没有可启用的插件（跳过不兼容/失败 {skipped_broken} 个）" if skipped_broken else "没有可操作的插件"
            return _report(self, False, msg)
        res = _set_plugins_enabled(self, prefs, db, runnable, True)
        if skipped_broken:
            self.report({"INFO"}, f"已自动跳过 {skipped_broken} 个不兼容/失败插件")
        return res


class PM_OT_disable_pack(_PMBase):
    bl_idname = "plugin_manager.disable_pack"
    bl_label = "停用当前分类全部"
    bl_description = "一次性停用当前分类下的全部插件"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        target = prefs.active_category
        if target and target not in ("", "全部", C.DEFAULT_CATEGORY):
            records = [rec for key, rec in db.plugins.items() if target in db.plugin_groups(key)]
        else:
            records = [rec for key, rec in db.plugins.items() if not db.plugin_groups(key)]
        return _set_plugins_enabled(self, prefs, db, records, False)


# ---------------------------------------------------------------------------
# 工作方案 / 预设（精选插件一键启停，与分类筛选完全解耦）
# ---------------------------------------------------------------------------
_ENUM_ITEMS_CACHE: dict[str, list[tuple[str, str, str]]] = {}
_ENUM_STRING_HOLD: set[str] = set()


def _cache_enum_items(slot: str, entries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Keep strong Python references to dynamic EnumProperty strings and lists.

    Blender's C EnumProperty callback stores raw ``char*`` pointers to returned
    Python strings without incrementing refcounts; returning temporary lists or
    freshly formatted f-strings causes garbage-collected memory to show as
    garbled text (乱码) in UI dropdowns.
    """
    prev = _ENUM_ITEMS_CACHE.get(slot)
    if prev == entries:
        return prev
    held: list[tuple[str, str, str]] = []
    for item in entries:
        ident, label, desc = str(item[0]), str(item[1]), str(item[2])
        _ENUM_STRING_HOLD.add(ident)
        _ENUM_STRING_HOLD.add(label)
        _ENUM_STRING_HOLD.add(desc)
        held.append((ident, label, desc))
    _ENUM_ITEMS_CACHE[slot] = held
    return held


def _preset_enum(self, context):
    from . import items as _items

    _items.refresh_cache(bridge.get_prefs())
    opts = _items.preset_options()
    entries = [(p, p, "") for p in opts] or [("__NONE__", "（暂无预设，可在下方输入新名称）", "")]
    return _cache_enum_items("preset", entries)


def _seed_category_enum(self, context):
    from . import items as _items

    _items.refresh_cache(bridge.get_prefs())
    opts = [("__NONE__", "不导入（创建空白预设）", "创建一个空白工作预设")]
    for name in _items.group_options():
        opts.append((name, f"从分类「{name}」导入成员", f"导入分类「{name}」下的插件"))
    return _cache_enum_items("seed_category", opts)


class PM_OT_preset_add(_PMBase):
    bl_idname = "plugin_manager.preset_add"
    bl_label = "新建工作预设"
    bl_description = "创建一个精选工作预设（可空白创建，或从现有分类导入成员）"

    name: StringProperty(name="预设名称", default="")
    from_category: EnumProperty(name="初始成员", items=_seed_category_enum)
    pinned: BoolProperty(name="设为常驻预设", description="切换其他预设时始终保留开启", default=False)
    add_plugin_key: StringProperty(default="", options={"HIDDEN"})

    def invoke(self, context, event):
        from . import items as _items

        _items.refresh_cache(_prefs(context))
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        clean = self.name.strip()
        if not clean:
            return _report(self, False, "请输入预设名称")
        members: list[str] = []
        if self.from_category and self.from_category != "__NONE__":
            members = [key for key in db.plugins if self.from_category in db.plugin_groups(key)]
        if self.add_plugin_key and self.add_plugin_key not in members and db.get(self.add_plugin_key):
            members.append(self.add_plugin_key)
        if not db.add_preset(clean, members, pinned=self.pinned):
            return _report(self, False, f"预设「{clean}」已存在")
        db.save()
        if self.add_plugin_key:
            from .services import presets as preset_service

            preset_service.note_assigned_to_preset(self.add_plugin_key)
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, f"已新建预设「{clean}」（含 {len(members)} 个插件）")


class PM_OT_preset_capture(_PMBase):
    bl_idname = "plugin_manager.preset_capture"
    bl_label = "从当前已启保存预设"
    bl_description = "把当前 Blender 中已启用的插件一键保存为工作预设"

    name: StringProperty(name="预设名称", default="当前工作流")
    include_builtins: BoolProperty(name="包含已启用的内置插件", default=True)
    overwrite: BoolProperty(name="若同名预设已存在则更新其成员", default=True)
    pinned: BoolProperty(name="设为常驻预设", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        clean = self.name.strip()
        if not clean:
            return _report(self, False, "请输入预设名称")
        enabled_set = bridge.enabled_modules()
        members: list[str] = []
        for key, rec in db.plugins.items():
            module = rec.get("module") or ""
            if not module or rec.get("missing") or module == C.ADDON_ID:
                continue
            if not self.include_builtins and rec.get("kind") == C.KIND_BUILTIN:
                continue
            if module in enabled_set:
                members.append(key)
        if not members:
            return _report(self, False, "当前没有已启用的库内插件可保存")
        exists = db.get_preset(clean) is not None
        if exists and not self.overwrite:
            return _report(self, False, f"预设「{clean}」已存在，请更换名称或勾选更新")
        if exists:
            db.set_preset_plugins(clean, members)
            if self.pinned:
                db.set_preset_pinned(clean, True)
            msg = f"已更新预设「{clean}」（{len(members)} 个插件）"
        else:
            db.add_preset(clean, members, pinned=self.pinned)
            msg = f"已保存预设「{clean}」（{len(members)} 个插件）"
        db.save()
        from . import items
        from .services import presets as preset_service

        for k in members:
            preset_service.note_assigned_to_preset(k)
        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, msg)


class PM_OT_preset_rename(_PMBase):
    bl_idname = "plugin_manager.preset_rename"
    bl_label = "重命名预设"
    bl_description = "重命名指定工作预设"

    old: StringProperty(default="")
    new: StringProperty(name="新名称", default="")

    def invoke(self, context, event):
        self.new = self.old
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.old:
            return _report(self, False, "未指定预设")
        new_name = self.new.strip()
        if not new_name:
            return _report(self, False, "名称不能为空")
        if not db.rename_preset(self.old, new_name):
            return _report(self, False, "重命名失败（可能与已有预设重名）")
        db.save()
        if prefs and prefs.active_preset_filter == self.old:
            prefs.active_preset_filter = new_name
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, f"已将预设「{self.old}」重命名为「{new_name}」")


class PM_OT_preset_delete(_PMBase):
    bl_idname = "plugin_manager.preset_delete"
    bl_label = "删除预设"
    bl_description = "删除该工作预设（不会删除或卸载任何插件）"

    name: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "未指定预设")
        if not db.delete_preset(self.name):
            return _report(self, False, f"未找到预设「{self.name}」")
        db.save()
        if prefs and prefs.active_preset_filter == self.name:
            prefs.active_preset_filter = ""
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, f"已删除预设「{self.name}」")


class PM_OT_preset_toggle(_PMBase):
    bl_idname = "plugin_manager.preset_toggle"
    bl_label = "开关预设"
    bl_description = "开启或关闭工作预设（自动跳过不兼容/失败项，关闭时保留其他已开预设共有项与临时插件）"

    name: StringProperty(default="")
    enable: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        from .services import presets as preset_service

        plan = preset_service.plan_preset_toggle(db, self.name, self.enable)
        verb = "已开启" if self.enable else "已关闭"
        return _apply_preset_plan_op(self, prefs, db, plan, f"{verb}预设「{self.name}」")


class PM_OT_preset_switch(_PMBase):
    bl_idname = "plugin_manager.preset_switch"
    bl_label = "切换至此预设"
    bl_description = "启用此预设并关闭其他非常驻预设（保留常驻预设与你手动临时启用的插件）"

    name: StringProperty(default="")
    keep_pinned: BoolProperty(default=True)
    keep_manual: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        from .services import presets as preset_service

        plan = preset_service.plan_preset_switch(
            db, self.name, keep_pinned=self.keep_pinned, keep_manual=self.keep_manual,
        )
        return _apply_preset_plan_op(self, prefs, db, plan, f"已切换至预设「{self.name}」")


class PM_OT_preset_set_pinned(_PMBase):
    bl_idname = "plugin_manager.preset_set_pinned"
    bl_label = "常驻预设开关"
    bl_description = "常驻预设在切换其他工作预设或全部关闭时保持开启"

    name: StringProperty(default="")
    value: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        db.set_preset_pinned(self.name, bool(self.value))
        db.save()
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        verb = "已设为常驻预设" if self.value else "已取消常驻"
        return _report(self, True, f"预设「{self.name}」{verb}")


class PM_OT_preset_set_startup(_PMBase):
    bl_idname = "plugin_manager.preset_set_startup"
    bl_label = "预设自启开关"
    bl_description = "在「干净启动」模式下，Blender 启动时自动加载该预设"

    name: StringProperty(default="")
    value: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        db.set_preset_state(self.name, startup=bool(self.value))
        db.save()
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        verb = "已设为干净启动自启" if self.value else "已取消自启"
        return _report(self, True, f"预设「{self.name}」{verb}")


class PM_OT_preset_deactivate_all(_PMBase):
    bl_idname = "plugin_manager.preset_deactivate_all"
    bl_label = "关闭非常驻预设"
    bl_description = "关闭所有非常驻工作预设的插件（保留常驻预设与手动临时启用的插件）"

    include_pinned: BoolProperty(default=False)
    keep_manual: BoolProperty(default=True)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        from .services import presets as preset_service

        plan = preset_service.plan_deactivate_all_presets(
            db, include_pinned=self.include_pinned, keep_manual=self.keep_manual,
        )
        if not plan["disable"]:
            return _report(self, True, "当前没有需要关闭的非常驻预设插件")
        return _apply_preset_plan_op(self, prefs, db, plan, "已关闭非常驻预设")


class PM_OT_preset_filter(_PMBase):
    bl_idname = "plugin_manager.preset_filter"
    bl_label = "按预设筛选列表"
    bl_description = "只显示该预设包含的插件（再次点击恢复全部）"

    name: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        if not self.name or prefs.active_preset_filter == self.name:
            prefs.active_preset_filter = ""
        else:
            prefs.active_preset_filter = self.name
        _redraw()
        return {"FINISHED"}


class PM_OT_plugin_toggle_preset(Operator):
    bl_idname = "plugin_manager.plugin_toggle_preset"
    bl_label = "加入/移出预设"
    bl_description = "把该插件加入或移出指定工作预设（一个插件可同时属于多个预设）"

    key: StringProperty(default="")
    preset: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        key = self.key or (prefs.selected_key if prefs else "")
        if not db or not key or not self.preset:
            return _report(self, False, "未找到插件或预设")
        from .services import presets as preset_service

        current = db.plugin_presets(key)
        if self.preset in current:
            db.remove_plugin_from_preset(key, self.preset)
            msg = f"已移出预设「{self.preset}」"
        else:
            db.add_plugin_to_preset(key, self.preset)
            preset_service.note_assigned_to_preset(key)
            msg = f"已加入预设「{self.preset}」"
        db.save()
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, msg)


class PM_OT_batch_add_preset(_PMBase):
    bl_idname = "plugin_manager.batch_add_preset"
    bl_label = "批量加入/移出预设"
    bl_description = "把当前筛选中勾选的插件批量加入或移出工作预设"

    preset: EnumProperty(name="目标预设", items=_preset_enum)
    new_name: StringProperty(name="或新建预设名", default="",
                             description="填写新名称时将直接创建该预设并加入所选插件")
    remove: BoolProperty(name="移出该预设", default=False)

    def invoke(self, context, event):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        from . import items as _items

        _items.refresh_cache(prefs)
        if not _items.selected_keys(prefs):
            return _report(self, False, "请先在当前筛选结果中勾选要操作的插件")
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, "", True)
        if not keys:
            return _report(self, False, "请先勾选要操作的插件")
        target = self.new_name.strip() or ("" if self.preset == "__NONE__" else self.preset)
        if not target:
            return _report(self, False, "请选择预设或输入新预设名称")
        if self.new_name.strip() and not self.remove:
            db.add_preset(target, [])
        from .services import presets as preset_service

        for k in keys:
            if not db.get(k):
                continue
            if self.remove:
                db.remove_plugin_from_preset(k, target)
            else:
                db.add_plugin_to_preset(k, target)
                preset_service.note_assigned_to_preset(k)
        db.save()
        from . import items

        items.rebuild_items(prefs)
        _redraw()
        verb = "移出" if self.remove else "加入"
        return _report(self, True, f"已将 {len(keys)} 个插件{verb}预设「{target}」")


class PM_OT_manual_prompt_action(_PMBase):
    bl_idname = "plugin_manager.manual_prompt_action"
    bl_label = "处理手动启用的插件"
    bl_description = "将手动启用的插件标记为本次临时使用，或收起提示"

    key: StringProperty(default="")
    action: StringProperty(default="temp")  # "temp" | "untemp" | "dismiss"

    def execute(self, context):
        prefs = _prefs(context)
        from . import items
        from .services import presets as preset_service

        target_key = self.key or (prefs.manual_prompt_key if prefs else "")
        if self.action == "temp" and target_key:
            preset_service.mark_temporary(target_key)
            msg = "已标记为本次临时使用（切换/关闭预设时自动保留）"
        elif self.action == "untemp" and target_key:
            preset_service.unmark_temporary(target_key)
            msg = "已取消临时标记"
        else:
            preset_service.dismiss_prompt(target_key)
            msg = "已收起提示"
        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, msg)


class PM_OT_plugin_toggle_group(Operator):
    bl_idname = "plugin_manager.plugin_toggle_group"
    bl_label = "加入/移出分类"
    bl_description = "把当前插件加入或移出指定分类（仅用于整理筛选，不影响启停）"

    key: StringProperty(default="")
    group: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        key = self.key or (prefs.selected_key if prefs else "")
        if not db or not key or not self.group:
            return _report(self, False, "未找到插件或分类")
        groups = db.plugin_groups(key)
        if self.group in groups:
            db.remove_plugin_from_group(key, self.group)
            msg = f"已移出分类「{self.group}」"
        else:
            db.add_plugin_to_group(key, self.group)
            msg = f"已加入分类「{self.group}」"
        db.save()
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, msg)


def _group_enum(self, context):
    from . import items as _items

    _items.refresh_cache(bridge.get_prefs())
    opts = _items.group_options()
    entries = [(g, g, "") for g in opts] or [(C.DEFAULT_CATEGORY, "（暂无分类，请先新建）", "")]
    return _cache_enum_items("group", entries)


class PM_OT_batch_add_group(_PMBase):
    bl_idname = "plugin_manager.batch_add_group"
    bl_label = "批量加入/移出分组"
    bl_description = "把勾选的插件批量加入或移出某个分组（不影响它们已有的其他分组）"

    group: bpy.props.EnumProperty(name="分组", items=_group_enum)
    remove: BoolProperty(name="移出该分组", default=False)

    def invoke(self, context, event):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        from . import items as _items

        _items.refresh_cache(prefs)
        if not _items.selected_keys(prefs):
            return _report(self, False, "请先在当前筛选结果中勾选要操作的插件")
        if not _items.group_options():
            return _report(self, False, "暂无可用分组，请先新建分组")
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        keys = _target_keys(prefs, "", True)
        if not keys:
            return _report(self, False, "请先勾选要操作的插件")
        if not self.group or self.group == C.DEFAULT_CATEGORY:
            return _report(self, False, "请先新建工作分组")
        for k in keys:
            if not db.get(k):
                continue
            if self.remove:
                db.remove_plugin_from_group(k, self.group)
            else:
                db.add_plugin_to_group(k, self.group)
        db.save()
        from . import items

        items.rebuild_items(prefs)
        _redraw()
        verb = "移出" if self.remove else "加入"
        return _report(self, True, f"已将 {len(keys)} 个插件{verb}分组「{self.group}」")



# ---------------------------------------------------------------------------
# 导入 / 扫描 / 移除
# ---------------------------------------------------------------------------
class PM_OT_import(_PMBase):
    bl_idname = "plugin_manager.import_plugin"
    bl_label = "导入插件"
    bl_description = "从一个文件夹或 zip 导入插件到插件库"

    directory: StringProperty(subtype="DIR_PATH", default="")
    filepath: StringProperty(subtype="FILE_PATH", default="")
    move: BoolProperty(name="移动而非复制", default=False)
    enable_after: BoolProperty(name="导入后启用", default=False)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        path = self.filepath or self.directory
        if not path:
            return _report(self, False, "未选择路径")
        try:
            rec = library.import_path(path, prefs.library_path, db, move=self.move,
                                      enable=self.enable_after, origin="manual")
        except Exception as exc:
            return _report(self, False, f"导入失败: {exc}")
        prefs.selected_key = rec["key"]
        return _report(self, True, f"已导入: {library.effective_name(rec)}")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class PM_OT_import_zip(_PMBase):
    bl_idname = "plugin_manager.import_zip"
    bl_label = "导入压缩包"
    bl_description = "选择一个或多个 zip 插件包导入"

    filepath: StringProperty(subtype="FILE_PATH", default="")
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH", default="")
    enable_after: BoolProperty(name="导入后启用", default=False)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        paths = [os.path.join(self.directory, f.name) for f in self.files] or [self.filepath]
        ok, fail, names = 0, 0, []
        for p in paths:
            if not p:
                continue
            try:
                rec = library.import_path(p, prefs.library_path, db, move=True,
                                          enable=self.enable_after, origin="zip")
                names.append(library.effective_name(rec))
                ok += 1
            except Exception as exc:
                fail += 1
                print("[插件库] 导入失败", p, exc)
        if ok:
            return _report(self, True, f"已导入 {ok} 个插件" + (f"，{fail} 失败" if fail else ""))
        return _report(self, False, f"导入失败（{fail}）")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def _fill_report(prefs, result: dict) -> None:
    """把扫描结果写入偏好中的报告集合，供面板展示。"""
    prefs.report_items.clear()
    for e in result.get("entries", []):
        it = prefs.report_items.add()
        it.name = e.get("name", "")
        it.path = e.get("path", "")
        it.kind = e.get("kind", "")
        it.status = e.get("status", "")
        it.detail = e.get("detail", "")
    prefs.report_index = 0
    prefs.report_summary = (
        f"导入 {result['imported']}，跳过 {result['skipped']}，失败 {result['failed']}"
        f"（扫描 {result.get('scanned', 0)} 个位置）"
    )


def _write_report_log(prefs, result: dict) -> str:
    """把明细写入 <库>/.pm/last_scan.log，便于复制排查。"""
    path = os.path.join(prefs.library_path, C.DIR_META, "last_scan.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("插件库扫描报告\n")
            f.write(f"库路径: {prefs.library_path}\n")
            f.write(f"汇总: {prefs.report_summary}\n")
            f.write("-" * 60 + "\n")
            for e in result.get("entries", []):
                f.write(f"[{e['status']}] {e['name']}  ({e['kind']})\n")
                f.write(f"    来源: {e['path']}\n")
                f.write(f"    详情: {e['detail']}\n")
        return path
    except OSError:
        return ""


class PM_OT_scan_inbox(_PMBase):
    bl_idname = "plugin_manager.scan_inbox"
    bl_label = "扫描投放区"
    bl_description = "手动检查投放区(inbox)与库根目录，导入新插件并列出每项结果"

    def invoke(self, context, event):
        prefs = _prefs(context)
        if not _db(context):
            return _report(self, False, "插件库不可写")
        self._candidates = watcher.preview_paths(prefs.library_path)
        if not self._candidates:
            return _report(self, True, "投放区没有待处理的文件夹或 ZIP")
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        self.layout.label(text=f"检查 {len(self._candidates)} 项；成功导入后移走投放源")
        for path in self._candidates[:8]:
            self.layout.label(text=os.path.basename(path))
        if len(self._candidates) > 8:
            self.layout.label(text=f"另有 {len(self._candidates) - 8} 项")

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        try:
            res = watcher.scan_inbox(prefs.library_path, db, move=True,
                                     allowed=getattr(self, "_candidates", None))
        except Exception as exc:
            return _report(self, False, f"扫描失败: {exc}")
        _fill_report(prefs, res)
        log = _write_report_log(prefs, res)
        bridge.refresh_blender()

        if res["imported"]:
            library.sync_library(prefs.library_path, db)
            from . import items as _items

            _items.rebuild_items(prefs)

        # 无任何待处理项时直接提示，不弹报告
        if not res["entries"]:
            return _report(self, True, "投放区没有待处理的插件")

        # 有失败项 → 弹窗展示明细，方便定位问题
        if res["failed"]:
            bpy.ops.plugin_manager.show_report()
            return _report(self, False, f"{prefs.report_summary}；详见报告")

        if res["skipped"] and not res["imported"]:
            bpy.ops.plugin_manager.show_report()
        tail = f"；日志: {log}" if log else ""
        return _report(self, True, prefs.report_summary + tail)


class PM_OT_show_report(_PMBase):
    bl_idname = "plugin_manager.show_report"
    bl_label = "扫描报告"
    bl_description = "查看最近一次扫描/导入的明细与失败原因"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=620)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        prefs = _prefs(context)
        if not prefs:
            layout.label(text="插件库未启用", icon="ERROR")
            return
        layout.label(text=prefs.report_summary or "尚无扫描记录", icon="INFO")
        layout.separator()

        if not prefs.report_items:
            layout.label(text="没有明细可显示")
            return

        from bpy.types import UIList  # noqa: F401

        layout.template_list("PM_UL_report", "", prefs, "report_items",
                             prefs, "report_index", rows=10)

        idx = min(max(prefs.report_index, 0), len(prefs.report_items) - 1)
        sel = prefs.report_items[idx] if prefs.report_items else None
        if sel:
            box = layout.box()
            box.label(text=f"{sel.name}  [{sel.status}]", icon="INFO")
            box.label(text=f"类型: {sel.kind}")
            box.label(text=f"来源: {sel.path}")
            for line in _wrap(sel.detail, 78):
                box.label(text=line)

        row = layout.row(align=True)
        row.operator("plugin_manager.copy_report", icon="COPY_ID", text="复制报告到剪贴板")
        row.operator("plugin_manager.open_report_log", icon="TEXT", text="打开日志文件")

    def cancel(self, context):
        return None


def _wrap(text: str, width: int) -> list[str]:
    text = text or ""
    return [text[i:i + width] for i in range(0, len(text), width)] or [""]


class PM_OT_copy_report(_PMBase):
    bl_idname = "plugin_manager.copy_report"
    bl_label = "复制报告"

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        lines = [prefs.report_summary, "-" * 50]
        for e in prefs.report_items:
            lines.append(f"[{e.status}] {e.name} ({e.kind})")
            lines.append(f"    来源: {e.path}")
            lines.append(f"    详情: {e.detail}")
        text = "\n".join(lines)
        try:
            context.window_manager.clipboard = text
        except Exception as exc:
            return _report(self, False, f"写入剪贴板失败: {exc}")
        return _report(self, True, "报告已复制到剪贴板")


class PM_OT_open_report_log(_PMBase):
    bl_idname = "plugin_manager.open_report_log"
    bl_label = "打开日志"

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs or not prefs.library_path:
            return _report(self, False, "插件库未就绪")
        path = os.path.join(prefs.library_path, C.DIR_META, "last_scan.log")
        if not os.path.isfile(path):
            return _report(self, False, "尚无日志文件，请先扫描")
        try:
            bpy.ops.wm.path_open(filepath=path)
        except Exception as exc:
            return _report(self, False, str(exc))
        return _report(self, True, "已打开日志")


class PM_OT_remove(_PMBase):
    bl_idname = "plugin_manager.remove_plugin"
    bl_label = "移除插件"
    bl_description = "从插件库中移除选中的插件（移入回收站）"

    key: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        self.layout.label(text="将插件文件移入库内回收站，可按原位置恢复")
        self.layout.label(text="如果同步整个库，移除可能传播到其它设备", icon="INFO")

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        key = self.key or prefs.selected_key
        rec = db.get(key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        name = library.effective_name(rec)
        try:
            moved = library.remove_plugin(prefs.library_path, db, key, to_trash=True)
        except (OSError, RuntimeError, ValueError) as exc:
            return _report(self, False, f"移除失败: {exc}")
        # A failed move leaves both the source directory and DB record intact.
        # Do not report success in that case.
        if moved is None and db.get(key) is not None:
            return _report(self, False, f"移除失败：无法将 {name} 移入回收站，原插件已保留")
        if prefs.selected_key == key:
            prefs.selected_key = ""
        tail = f"（已移到回收站: {os.path.basename(moved)}）" if moved else ""
        return _report(self, True, f"已移除 {name}{tail}")


def _trash_choices(self, context):
    prefs = _prefs(context)
    entries = library.trash_entries(prefs.library_path) if prefs and prefs.library_path else {}
    return _cache_enum_items(
        "trash",
        [(key, item.get("name") or key, item["rel"]) for key, item in entries.items()],
    )


class PM_OT_restore_plugin(_PMBase):
    bl_idname = "plugin_manager.restore_plugin"
    bl_label = "从回收站恢复"
    trash_id: EnumProperty(name="插件", items=_trash_choices)

    def invoke(self, context, event):
        if not _trash_choices(self, context):
            return _report(self, True, "回收站没有可恢复的插件")
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        prefs, db = _prefs(context), _db(context)
        if not db:
            return _report(self, False, "插件库不可写")
        try:
            restored = library.restore_plugin(prefs.library_path, db, self.trash_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return _report(self, False, f"恢复失败: {exc}")
        _redraw()
        return _report(self, True, f"已恢复 {library.effective_name(restored)} 到原目录")


def _relink_choices(self, context):
    prefs, db = _prefs(context), _db(context)
    records = db.relink_options(self.key or prefs.selected_key) if db else {}
    return _cache_enum_items(
        "relink",
        [
            (key, library.effective_name(record), f"{record.get('author', '')} · {key}")
            for key, record in records.items()
        ],
    )


class PM_OT_relink_addon(_PMBase):
    bl_idname = "plugin_manager.relink_addon"
    bl_label = "关联已有插件资料"
    key: StringProperty(options={"HIDDEN"})
    target: EnumProperty(name="关联资料", items=_relink_choices)

    def invoke(self, context, event):
        self.key = self.key or _prefs(context).selected_key
        if not _relink_choices(self, context):
            return _report(self, True, "没有其它可关联的传统插件资料")
        return context.window_manager.invoke_props_dialog(self, width=430)

    def execute(self, context):
        prefs, db = _prefs(context), _db(context)
        if not db:
            return _report(self, False, "插件库不可写")
        try:
            db.relink_addon(self.key, self.target)
        except (RuntimeError, OSError, ValueError) as error:
            return _report(self, False, str(error))
        prefs.selected_key = self.target
        try:
            scoped_management.transfer_activation(prefs.library_path, self.key, self.target)
        except (RuntimeError, OSError, ValueError) as error:
            return _report(self, True, f"已关联资料；设备启用清单未更新: {error}", kind="WARNING")
        _redraw()
        return _report(self, True, "已关联资料，保留原安装位置")


# ---------------------------------------------------------------------------
# 更新检测
# ---------------------------------------------------------------------------
class PM_OT_check_updates(_PMBase):
    bl_idname = "plugin_manager.check_updates"
    bl_label = "检查更新"
    bl_description = "比对扩展仓库索引，检查插件是否有新版本"

    online: BoolProperty(name="联网同步索引", default=False)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        stats = updates.check_updates(db, prefs.library_path, online=self.online)
        return _report(self, True, f"发现 {stats['updates']} 个可更新（已比对 {stats['checked']} 个）")


def _update_requests(db):
    requests = []
    for rec in db.plugins.values():
        if not (rec.get("update_available") and rec.get("latest_url")
                and rec.get("archive_source") and not rec.get("missing")):
            continue
        package = {"id": rec.get("pkg_id") or rec["id"], "name": library.effective_name(rec),
                   "version": rec["latest_version"], "source": rec["archive_source"],
                   "repo": rec.get("latest_repo", ""), "archive_url": rec["latest_url"],
                   "archive_hash": rec.get("latest_hash", ""), "archive_size": rec.get("latest_size", 0)}
        requests.append((package, rec["key"], rec.get("version", "")))
    return requests


class PM_OT_update_all(_PMBase):
    bl_idname = "plugin_manager.update_all"
    bl_label = "预览批量更新"

    def invoke(self, context, event):
        db = _db(context)
        self._preview_root = _prefs(context).library_path
        self._requests = _update_requests(db) if db else []
        if not self._requests:
            return _report(self, False, "没有来源已确认的更新，请先检查更新或在商店确认来源")
        return context.window_manager.invoke_props_dialog(self, width=550)

    def draw(self, context):
        self.layout.label(text=f"将替换当前库中的 {len(self._requests)} 个扩展")
        self.layout.label(text="先备份；当前文件目录的外部同步也可能传播替换")
        for pkg, _key, version in self._requests[:10]:
            self.layout.label(text=f"{pkg['name']} · {version} → {pkg['version']} · {pkg['repo']}")
        if len(self._requests) > 10:
            self.layout.label(text=f"另有 {len(self._requests) - 10} 项")

    def execute(self, context):
        if (not getattr(self, "_requests", None)
                or self._preview_root != _prefs(context).library_path):
            return _report(self, False, "请先打开批量更新预览")
        if not bpy.context.preferences.system.use_online_access:
            return _report(self, False, "请先在 Blender 系统设置中允许联网")
        try:
            archive_jobs.start(_prefs(context), self._requests)
        except (RuntimeError, ValueError) as error:
            return _report(self, False, str(error))
        return _report(self, True, "已开始下载更新")


class PM_OT_cancel_archives(_PMBase):
    bl_idname = "plugin_manager.cancel_archives"
    bl_label = "取消安装或更新"

    def execute(self, context):
        archive_jobs.cancel()
        return _report(self, True, "正在取消下载；已完成的安装或更新保留")


class PM_OT_retry_archives(_PMBase):
    bl_idname = "plugin_manager.retry_archives"
    bl_label = "重试失败的安装或更新"

    def execute(self, context):
        if not bpy.context.preferences.system.use_online_access:
            return _report(self, False, "请先在 Blender 系统设置中允许联网")
        try:
            archive_jobs.retry(_prefs(context))
        except (RuntimeError, ValueError) as error:
            return _report(self, False, str(error))
        return _report(self, True, "正在重试失败项")


class PM_OT_store_sync(_PMBase):
    bl_idname = "plugin_manager.store_sync"
    bl_label = "刷新商店目录"
    bl_description = "联网刷新扩展商店索引（需要网络）"

    def execute(self, context):
        ok, err = store.sync_indexes()
        if not ok:
            return _report(self, False, err)
        n = len(store.read_catalog())
        return _report(self, True, f"商店目录已刷新，共 {n} 个扩展")


class PM_OT_store_open(_PMBase):
    bl_idname = "plugin_manager.store_open"
    bl_label = "打开在线商店"
    bl_description = "浏览 extensions.blender.org 的扩展，可直接安装到插件库"

    def invoke(self, context, event):
        prefs = _prefs(context)
        if not prefs or not prefs.library_path:
            return _report(self, False, "请先设置插件库")
        return context.window_manager.invoke_props_dialog(self, width=720)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        prefs = _prefs(context)
        db = LibraryDB(prefs.library_path)

        catalog = _catalog_cache()
        if not catalog:
            layout.label(text="商店目录为空，请先点「刷新商店目录」", icon="ERROR")
            return

        # 过滤器
        row = layout.row(align=True)
        row.prop(prefs, "store_search", text="", icon="VIEWZOOM")
        row.prop(prefs, "store_only_new", text="仅未安装", toggle=True)
        row.prop(prefs, "store_only_compatible", text="仅兼容", toggle=True)
        row.operator("plugin_manager.store_sync", text="", icon="FILE_REFRESH")

        results = store.search(catalog, db, prefs.store_search,
                               only_compatible=prefs.store_only_compatible,
                               only_new=prefs.store_only_new)
        page_size = 12
        pages = max(1, (len(results) + page_size - 1) // page_size)
        page = min(prefs.store_page, pages)
        row = layout.row(align=True)
        row.label(text=f"{len(results)} 个结果 · 第 {page}/{pages} 页")
        row.prop(prefs, "store_page", text="转到")
        if not results:
            layout.label(text="没有匹配的扩展，请调整搜索或筛选", icon="INFO")
        box = layout.box()
        for pkg in results[(page - 1) * page_size:page * page_size]:
            row = box.row(align=True)
            # 状态图标
            if pkg["installed"]:
                newer = scan.compare_versions(pkg["version"], pkg["installed_version"]) > 0
                row.label(text="", icon="FILE_REFRESH" if newer else "CHECKMARK")
            else:
                row.label(text="", icon="PLUGIN")
            col = row.column(align=True)
            col.label(text=f"{pkg['name']}  {pkg['version']}")
            sub = col.row()
            sub.enabled = False
            sub.label(text=f"{pkg['repo']} · {pkg.get('tagline') or ''}"[:85])

            if not store.is_compatible(pkg):
                row.label(text="版本不兼容", icon="ERROR")
            elif pkg["installed"]:
                if scan.compare_versions(pkg["version"], pkg["installed_version"]) > 0:
                    op = row.operator("plugin_manager.store_install", text="更新", icon="FILE_REFRESH")
                    op.package_id = pkg["id"]
                    op.source = pkg["source"]
                else:
                    row.label(text="已安装")
            else:
                op = row.operator("plugin_manager.store_install", text="安装", icon="IMPORT")
                op.package_id = pkg["id"]
                op.source = pkg["source"]

        row = layout.row(align=True)
        row.operator("plugin_manager.store_sync", icon="URL", text="刷新目录")
        row.operator("plugin_manager.check_updates", icon="FILE_REFRESH", text="检查库内更新")
        row.operator("plugin_manager.update_all", icon="PLAY", text="一键更新全部")


_CATALOG: list = []
_CATALOG_STAMP = 0.0


def _catalog_cache() -> list:
    """缓存目录，避免每次重绘都读文件；用索引 mtime 做失效判断。"""
    global _CATALOG, _CATALOG_STAMP
    import time

    stamp = 0.0
    for _name, path in store._repo_index_files():
        try:
            stamp = max(stamp, os.path.getmtime(path))
        except OSError:
            pass
    now = time.time()
    if _CATALOG and stamp == _CATALOG_STAMP and (now - getattr(_catalog_cache, "_t", 0)) < 20:
        return _CATALOG
    _CATALOG = store.read_catalog()
    _CATALOG_STAMP = stamp
    _catalog_cache._t = now
    return _CATALOG


class PM_OT_store_install(_PMBase):
    bl_idname = "plugin_manager.store_install"
    bl_label = "安装/更新"
    bl_description = "从在线商店下载该扩展并安装到插件库"

    package_id: StringProperty(default="")
    source: StringProperty(default="")
    confirm_source: BoolProperty(name="确认使用所选仓库替换当前安装", default=False)

    def _selection(self, context):
        db = _db(context)
        if not db:
            raise ValueError("插件库未就绪")
        packages = [pkg for pkg in _catalog_cache() if pkg["id"] == self.package_id
                    and (not self.source or self.source == pkg["source"])]
        if len(packages) != 1:
            raise ValueError("请选择唯一的扩展和仓库")
        pkg = packages[0]
        if not store.is_compatible(pkg):
            raise ValueError("该版本与当前 Blender 不兼容")
        record = store.installed_map(db).get(pkg["id"].casefold())
        if record and scan.compare_versions(pkg["version"], record.get("version")) <= 0:
            raise ValueError("目标版本没有高于本机版本")
        return pkg, record

    def invoke(self, context, event):
        try:
            self._preview = self._selection(context)
        except ValueError as error:
            return _report(self, False, str(error))
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        pkg, record = self._preview
        self.layout.label(text=f"{pkg['name']} · {pkg['repo']}")
        self.layout.label(text=f"{record.get('version') if record else '未安装'} → {pkg['version']}")
        self.layout.label(text="目标：当前插件库；文件修改可能随外部同步传播")
        if record and pkg["source"] != record.get("archive_source"):
            self.layout.label(text=f"当前来源：{record.get('archive_source') or '未记录'}", icon="INFO")
            self.layout.prop(self, "confirm_source")

    def execute(self, context):
        try:
            if not context.preferences.system.use_online_access:
                raise ValueError("请先在 Blender 系统设置中允许联网")
            pkg, record = self._selection(context)
            if getattr(self, "_preview", None) != (pkg, record):
                raise ValueError("请打开安装或更新预览并确认")
            if record and pkg["source"] != record.get("archive_source") and not self.confirm_source:
                raise ValueError("请确认所选仓库来源")
            archive_jobs.start(_prefs(context), [
                (pkg, record["key"] if record else "", record.get("version", "") if record else "")
            ])
        except (ValueError, RuntimeError) as error:
            return _report(self, False, str(error))
        return _report(self, True, "已开始下载")


class PM_OT_clear_updates(_PMBase):
    bl_idname = "plugin_manager.clear_updates"
    bl_label = "清除更新标记"

    def execute(self, context):
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        for rec in db.plugins.values():
            rec["update_available"] = False
        db.save()
        return _report(self, True, "已清除更新标记")


# ---------------------------------------------------------------------------
# 元数据编辑
# ---------------------------------------------------------------------------
class PM_OT_set_category(Operator):
    bl_idname = "plugin_manager.set_category"
    bl_label = "移入分组"

    key: StringProperty(default="")
    category: StringProperty(default="")

    def execute(self, context):
        db = _db(context)
        rec = db.get(self.key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        target = (self.category or C.DEFAULT_CATEGORY).strip() or C.DEFAULT_CATEGORY
        if target != C.DEFAULT_CATEGORY:
            db.ensure_category(target)
        db.set_plugin_groups(self.key, [target] if target != C.DEFAULT_CATEGORY else [])
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            items.rebuild_items(prefs)
        return _report(self, True, f"已移入分组: {target}")


def _category_enum(self, context):
    """分类下拉的候选项：从库文件刷新后持久缓存字符串引用，防止 Blender 枚举显示乱码。"""
    from . import items as _items

    _items.refresh_cache(bridge.get_prefs())
    return _cache_enum_items("category", [(c, c, "") for c in _items.category_options()])


class PM_OT_assign_category(Operator):
    """弹出下拉框，把当前选中的插件移入某个已有分组（或未分类）。"""

    bl_idname = "plugin_manager.assign_category"
    bl_label = "设置主分组"

    key: StringProperty(default="")
    category: bpy.props.EnumProperty(name="分组", items=_category_enum)

    def invoke(self, context, event):
        prefs = _prefs(context)
        db = _db(context)
        self.key = self.key or (prefs.selected_key if prefs else "")
        rec = db.get(self.key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        from . import items as _items

        _items.category_options()  # 确保缓存已填充
        cur = rec.get("category") or C.DEFAULT_CATEGORY
        if cur in _items.category_options():
            self.category = cur
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        db = _db(context)
        rec = db.get(self.key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        target = (self.category or C.DEFAULT_CATEGORY).strip() or C.DEFAULT_CATEGORY
        if target != C.DEFAULT_CATEGORY:
            db.ensure_category(target)
        db.set_plugin_groups(self.key, [target] if target != C.DEFAULT_CATEGORY else [])
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            items.rebuild_items(prefs)
        return _report(self, True, f"已移入分组: {target}")


class PM_OT_add_category(Operator):
    bl_idname = "plugin_manager.add_category"
    bl_label = "新建分组"

    name: StringProperty(name="分组名", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        if db.add_category(self.name):
            db.save()
            from . import items

            prefs = _prefs(context)
            if prefs:
                items.rebuild_items(prefs)
            return _report(self, True, f"已新建分组: {self.name}")
        return _report(self, False, "分组名为空或已存在")


class PM_OT_reset_auto_categories(_PMBase):
    bl_idname = "plugin_manager.reset_auto_categories"
    bl_label = "清理自动分类"
    bl_description = ("把插件自带元数据分类（你未手动改动过的）统一收回「未分类」，"
                      "只保留你自己创建和指定的分组")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        res = db.reset_auto_categories()
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            prefs.active_category = "全部"
            items.rebuild_items(prefs)
        return _report(self, True,
                       f"已收回 {res['moved']} 个插件到未分类，"
                       f"剩余分组 {max(0, res['categories_left'] - 1)} 个")


class PM_OT_rename_category(Operator):
    bl_idname = "plugin_manager.rename_category"
    bl_label = "重命名分组"

    old: StringProperty(default="")
    new: StringProperty(name="新名称", default="")

    def invoke(self, context, event):
        self.new = self.old
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        db = _db(context)
        prefs = _prefs(context)
        if not db or not self.old:
            return _report(self, False, "插件库未就绪")
        db.rename_category(self.old, self.new)
        db.save()
        from . import items

        if prefs:
            if prefs.active_category == self.old:
                prefs.active_category = self.new.strip() or "全部"
            items.rebuild_items(prefs)
        return _report(self, True, f"分组已重命名为 {self.new}")


class PM_OT_delete_category(Operator):
    bl_idname = "plugin_manager.delete_category"
    bl_label = "删除分组"

    name: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        db = _db(context)
        prefs = _prefs(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        db.delete_category(self.name)
        db.save()
        from . import items

        if prefs:
            if prefs.active_category == self.name:
                prefs.active_category = "全部"
            items.rebuild_items(prefs)
        return _report(self, True, f"已删除分组: {self.name}")


class PM_OT_set_favorite(Operator):
    bl_idname = "plugin_manager.set_favorite"
    bl_label = "收藏"

    key: StringProperty(default="")
    value: BoolProperty(default=True)

    def execute(self, context):
        db = _db(context)
        rec = db.get(self.key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        rec["favorite"] = self.value
        db.save()
        return _report(self, True, "已更新收藏")


class PM_OT_open_folder(Operator):
    bl_idname = "plugin_manager.open_folder"
    bl_label = "打开所在文件夹"

    key: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        rec = db.get(self.key or prefs.selected_key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        if rec.get("kind") == C.KIND_BUILTIN:
            src = rec.get("source_path") or ""
            path = os.path.dirname(src) if src and os.path.isfile(src) else src
            if not path or not os.path.isdir(path):
                return _report(self, False, "未找到该内置插件目录")
        else:
            try:
                path = str(resolve_record_path(prefs.library_path, rec["rel"], rec.get("kind", "addon")))
            except UnsafeLibraryPathError:
                return _report(self, False, "插件记录路径不安全，已阻止打开")
            if not os.path.isdir(path):
                return _report(self, False, "目录不存在")
        try:
            bpy.ops.wm.path_open(filepath=path)
        except Exception as exc:
            return _report(self, False, str(exc))
        return _report(self, True, "已打开目录")


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
class PM_OT_scan_candidates(_PMBase):
    bl_idname = "plugin_manager.scan_candidates"
    bl_label = "扫描现有插件"
    bl_description = "扫描 Blender 默认用户插件目录、自定义脚本目录与本地扩展仓库（含 User Default）"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        cands = migrate.collect_candidates(prefs.library_path, db)
        ready = [c for c in cands if not c.get("already") and not c.get("builtin_conflict")]
        already = sum(1 for c in cands if c.get("already"))
        conflicts = sum(1 for c in cands if c.get("builtin_conflict"))
        prefs.candidate_count = len(ready)

        # 写入扫描报告，方便用户逐条查看可收编项与跳过原因
        prefs.report_items.clear()
        for c in cands:
            it = prefs.report_items.add()
            it.name = c.get("name") or c.get("folder") or ""
            it.path = c.get("path") or ""
            it.kind = c.get("kind") or "-"
            if c.get("builtin_conflict"):
                it.status = "skipped"
                it.detail = f"与 Blender 内置插件同名（{c.get('source', '')}）"
            elif c.get("already"):
                it.status = "skipped"
                it.detail = f"库内已有同名插件（{c.get('source', '')}）"
            else:
                it.status = "imported"
                it.detail = f"可收编 · {c.get('source', '')}"
        summary = f"可收编 {len(ready)} 个"
        if already:
            summary += f"，库内已有 {already} 个"
        if conflicts:
            summary += f"，内置冲突 {conflicts} 个"
        prefs.report_summary = summary
        return _report(self, True, summary)


class PM_OT_import_candidates(_PMBase):
    bl_idname = "plugin_manager.import_candidates"
    bl_label = "收编全部"
    bl_description = "把扫描到的外部插件导入插件库（自动跳过库内已有项与内置冲突）"

    move: BoolProperty(
        name="移动而非复制（避免原位置重复显示）",
        description="推荐勾选：把外部插件移入插件库并清空原处副本，防止 Blender 列表里出现两个同名插件",
        default=True,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        cands = migrate.collect_candidates(prefs.library_path, db)
        stats = migrate.import_candidates(prefs.library_path, db, cands, move=self.move)
        prefs.candidate_count = 0
        prefs.report_items.clear()
        for d in stats.get("details") or ():
            it = prefs.report_items.add()
            it.name = d.get("name", "")
            it.path = d.get("path", "")
            it.kind = d.get("kind", "-")
            it.status = d.get("status", "skipped")
            it.detail = d.get("detail", "")
        from . import items
        items.rebuild_items(prefs)
        _redraw()
        extra = f"，内置冲突跳过 {stats['conflicts']} 个" if stats.get("conflicts") else ""
        msg = f"收编 {stats['ok']} 个，跳过 {stats['skip']}，失败 {stats['fail']}{extra}"
        prefs.report_summary = msg
        self.report({"INFO"}, msg)
        for err in stats["errors"][:5]:
            print("[插件库] 收编跳过/失败:", err)
        return {"FINISHED"}


class PM_OT_pick_category(Operator):
    bl_idname = "plugin_manager.pick_category"
    bl_label = "按分组筛选"

    category: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        if prefs is None:
            return _report(self, False, "插件库未启用")
        prefs.active_category = self.category or "全部"
        from . import items

        items.maybe_rebuild(prefs, force=True)
        return _report(self, True, f"已筛选分组: {prefs.active_category}")


class PM_OT_rename_display(Operator):
    """设置/清除插件的「中文显示名称」（别名）。

    实际插件名来自插件元数据，保持只读；别名同时同步到 Blender 原生插件界面
    （不修改任何插件代码）。
    """

    bl_idname = "plugin_manager.rename_display"
    bl_label = "设置中文显示名"

    key: StringProperty(default="")
    display_name: StringProperty(name="中文显示名", default="")
    clear: BoolProperty(default=False)

    def invoke(self, context, event):
        db = _db(context)
        prefs = _prefs(context)
        rec = db.get(self.key or (prefs.selected_key if prefs else "")) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        if not self.clear:
            self.display_name = rec.get("display_name", "") or ""
            return context.window_manager.invoke_props_dialog(self, width=380)
        return self.execute(context)

    def execute(self, context):
        db = _db(context)
        prefs = _prefs(context)
        rec = db.get(self.key or (prefs.selected_key if prefs else "")) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        from . import items, native_names

        if self.clear:
            rec["display_name"] = ""
            db.save()
            native_names.invalidate_cache()
            if prefs:
                items.rebuild_items(prefs)
            _redraw()
            return _report(self, True, "已清除中文显示名")
        rec["display_name"] = self.display_name.strip()
        db.save()
        native_names.invalidate_cache()
        if prefs:
            items.rebuild_items(prefs)
        _redraw()
        return _report(self, True, f"中文显示名已设为: {rec['display_name'] or '（空）'}")


class PM_OT_edit_meta(Operator):
    bl_idname = "plugin_manager.edit_meta"
    bl_label = "编辑插件信息"

    key: StringProperty(default="")
    display_name: StringProperty(name="中文显示名（别名）", default="")
    note: StringProperty(name="备注", default="")
    category: StringProperty(name="主分组", default="")
    tags: StringProperty(name="标签(逗号分隔)", default="")

    def _rec(self, context):
        db = _db(context)
        prefs = _prefs(context)
        if not db or not prefs:
            return None, None
        return db, db.get(self.key or prefs.selected_key)

    def invoke(self, context, event):
        db, rec = self._rec(context)
        if not rec:
            return {"CANCELLED"}
        self.key = rec["key"]
        self._opened_db = db
        self.display_name = rec.get("display_name", "")
        self.note = rec.get("note", "")
        self.category = rec.get("category", "") or C.DEFAULT_CATEGORY
        self.tags = ", ".join(rec.get("tags", []) or [])
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        prefs = _prefs(context)
        opened = getattr(self, "_opened_db", None)
        if opened and opened.root != prefs.library_path:
            return _report(self, False, "插件库已改变，请重新打开编辑")
        db, rec = (opened, opened.get(self.key)) if opened else self._rec(context)
        if not rec:
            return _report(self, False, "未找到插件")
        rec["display_name"] = self.display_name.strip()
        rec["note"] = self.note.strip()
        primary = self.category.strip() or C.DEFAULT_CATEGORY
        rec["tags"] = [t.strip() for t in self.tags.split(",") if t.strip()]
        existing_groups = db.plugin_groups(self.key)
        if primary == C.DEFAULT_CATEGORY:
            db.set_plugin_groups(self.key, [])
        else:
            db.ensure_category(primary)
            updated = [primary] + [g for g in existing_groups if g != primary]
            db.set_plugin_groups(self.key, updated)
        try:
            db.save()
        except CatalogConflictError as error:
            return _report(self, False, str(error))
        from . import items, native_names

        native_names.invalidate_cache()
        prefs = _prefs(context)
        if prefs:
            items.maybe_rebuild(prefs, force=True)
        _redraw()
        return _report(self, True, "已保存插件信息")


classes = (
    PM_OT_github_update,
    PM_OT_setup_library,
    PM_OT_pick_library_path,
    PM_OT_unmount_library,
    PM_OT_refresh,
    PM_OT_show_warnings,
    PM_OT_toggle,
    PM_OT_set_startup,
    PM_OT_apply_startup,
    PM_OT_verify_compat,
    PM_OT_cancel_compat,
    PM_OT_cleanup_residue,
    PM_OT_toggle_select,
    PM_OT_select_all,
    PM_OT_batch_enable,
    PM_OT_batch_set_category,
    PM_OT_batch_add_group,
    PM_OT_batch_add_preset,
    PM_OT_batch_set_startup,
    PM_OT_enable_pack,
    PM_OT_disable_pack,
    PM_OT_preset_add,
    PM_OT_preset_capture,
    PM_OT_preset_rename,
    PM_OT_preset_delete,
    PM_OT_preset_toggle,
    PM_OT_preset_switch,
    PM_OT_preset_set_pinned,
    PM_OT_preset_set_startup,
    PM_OT_preset_deactivate_all,
    PM_OT_preset_filter,
    PM_OT_plugin_toggle_preset,
    PM_OT_manual_prompt_action,
    PM_OT_plugin_toggle_group,
    PM_OT_import,
    PM_OT_import_zip,
    PM_OT_scan_inbox,
    PM_OT_show_report,
    PM_OT_copy_report,
    PM_OT_open_report_log,
    PM_OT_remove,
    PM_OT_restore_plugin,
    PM_OT_relink_addon,
    PM_OT_check_updates,
    PM_OT_update_all,
    PM_OT_cancel_archives,
    PM_OT_retry_archives,
    PM_OT_unify_store,
    PM_OT_store_sync,
    PM_OT_store_open,
    PM_OT_store_install,
    PM_OT_clear_updates,
    PM_OT_set_category,
    PM_OT_assign_category,
    PM_OT_add_category,
    PM_OT_reset_auto_categories,
    PM_OT_rename_category,
    PM_OT_delete_category,
    PM_OT_set_favorite,
    PM_OT_open_folder,
    PM_OT_scan_candidates,
    PM_OT_import_candidates,
    PM_OT_pick_category,
    PM_OT_edit_meta,
    PM_OT_rename_display,
)
