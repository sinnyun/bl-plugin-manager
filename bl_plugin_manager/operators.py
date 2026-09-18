"""操作符：刷新、启停、导入、扫描、更新检测、分类与备注维护。"""

from __future__ import annotations

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    StringProperty,
)
from bpy.types import Operator

from . import (bridge, compat_task, constants as C, library, migrate, scan,
               scoped_management, store, updates, watcher)
from .db import LibraryDB, now_iso
from .security.paths import UnsafeLibraryPathError, resolve_record_path


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------
def _prefs(context):
    return bridge.get_prefs()


def _db(context) -> LibraryDB | None:
    prefs = _prefs(context)
    if not prefs or not prefs.library_path:
        return None
    return LibraryDB(prefs.library_path)


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


def _initialize_schema2(root: str) -> LibraryDB:
    """确保总资料库就绪，并把旧版 library.json 迁移进资料库。

    调用方必须先确认目录是插件库（不是无关目录），否则会创建出空库。
    """
    db = LibraryDB(root, use_cache=False)
    if db.prepare() == "CORRUPT":
        raise RuntimeError("插件库元数据已损坏，已保持只读，未覆盖原文件")
    return db


class _PMBase(Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bridge.get_prefs() is not None


# ---------------------------------------------------------------------------
# 库注册与刷新
# ---------------------------------------------------------------------------
class PM_OT_setup_library(_PMBase):
    bl_idname = "plugin_manager.setup_library"
    bl_label = "启用/修复插件库"
    bl_description = "把插件库目录注册为 Blender 脚本目录与扩展仓库并刷新"

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs.library_path:
            return _report(self, False, "请先设置插件库路径")
        # 智能连接：按目录实际内容决定建空库 / 保留已有 / 收编散落插件
        try:
            res = library.connect_library(prefs.library_path, LibraryDB(prefs.library_path))
        except OSError as exc:
            return _report(self, False, f"无法创建插件库目录: {exc}")
        if res.get("error"):
            return _report(self, False, res["error"])
        _status("插件库：正在挂载并扫描…")
        try:
            db = _initialize_schema2(prefs.library_path)
            library.sync_library(prefs.library_path, db)
            result = scoped_management.activate(prefs.library_path)
            state = bridge.library_state(prefs.library_path, force=True)
        finally:
            _clear_status()
        if result["state"] == "ERROR":
            return _report(self, False, f"启用未完整完成: {result['failures'][:3]}")
        n = len(res["imported"])
        if res["created"]:
            return _report(self, True, f"已创建空插件库并挂载: {prefs.library_path}")
        if n:
            return _report(self, True, f"已挂载插件库，并收编 {n} 个散落插件")
        return _report(self, True, "插件库已启用并挂载到 Blender")


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
    move_flat: BoolProperty(name="收编散落插件（移动）", default=True)

    def invoke(self, context, event):
        prefs = _prefs(context)
        self.directory = (prefs.library_path or "") if prefs else ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        prefs = _prefs(context)
        if not prefs:
            return _report(self, False, "插件库未启用")
        path = os.path.abspath(self.directory) if self.directory else ""
        if not path:
            return _report(self, False, "未选择目录")

        # 先在内存中连接：任何失败都不改动当前偏好，避免留下无效配置
        db = LibraryDB(path)
        try:
            res = library.connect_library(path, db, move_flat=self.move_flat)
        except OSError as exc:
            return _report(self, False, f"无法创建插件库目录: {exc}")

        if res.get("error"):
            return _report(self, False, res["error"])

        try:
            db = _initialize_schema2(path)
        except RuntimeError as exc:
            return _report(self, False, str(exc))
        info = res["info"]
        prefs.library_path = path            # 触发 _on_library_path_update
        if info["is_library"]:
            library.sync_library(path, db)
            msg = (f"已切换插件库: 原有 {info['addon_count']} 传统 + "
                   f"{info['extension_count']} 扩展插件")
        elif res["imported"]:
            library.sync_library(path, db)
            msg = f"已识别并收编 {len(res['imported'])} 个散落插件后挂载"
        elif res["created"]:
            library.sync_library(path, db)
            msg = "已创建空插件库并挂载"
        else:
            library.sync_library(path, db)
            msg = "已挂载插件库"

        result = scoped_management.activate(path)
        state = bridge.library_state(path, force=True)

        if res["failed"]:
            msg += f"（{len(res['failed'])} 个收编失败，详见控制台）"
        if result["state"] == "ERROR":
            return _report(self, False, f"启用未完整完成: {result['failures'][:3]}")
        from . import items

        items.rebuild_items(prefs)
        return _report(self, True, msg)


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
        lines = [
            f"库路径: {prefs.library_path}",
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
        ok, err = bridge.set_enabled(rec["module"], self.enable)
        rec["enabled"] = bridge.is_module_enabled(rec["module"])
        # 记录/清除上次启用失败的原因，便于在详情里排查
        rec["last_error"] = "" if ok else (err or "未知错误")
        db.save()
        if ok:
            try:
                scoped_management.record_activation(prefs.library_path, key, self.enable)
            except Exception as exc:
                return _report(self, False, f"插件状态已改变，但设备启用清单保存失败: {exc}")
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
    """确定批量操作的目标：勾选的多选项，或单个 key。

    批量时作用于**所有勾选项**（含因过滤暂时不可见的），符合"勾了就操作"的直觉。
    """
    from . import items

    if use_selection:
        keys = items.all_selected_keys()
        if keys:
            return keys
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
            items.clear_selection()
            for it in prefs.plugin_items:
                items.set_selected(it.key, True)
        else:
            items.clear_selection()
        for it in prefs.plugin_items:
            it.selected = bool(self.value)
        _redraw()
        n = sum(1 for it in prefs.plugin_items if it.selected)
        return _report(self, True, f"已勾选 {n} 个" if self.value else "已取消全选")


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
        ok_n = fail_n = 0
        for k in keys:
            rec = db.get(k)
            if not rec or not rec.get("module"):
                continue
            ok, err = bridge.set_enabled(rec["module"], self.value)
            rec["enabled"] = bridge.is_module_enabled(rec["module"])
            rec["last_error"] = "" if ok else (err or "未知错误")
            ok_n += 1 if ok else 0
            fail_n += 0 if ok else 1
        db.save()
        sync_error = _sync_device_config(prefs)
        from . import items

        items.rebuild_items(prefs)
        verb = "启用" if self.value else "停用"
        _redraw()
        msg = f"已批量{verb} {ok_n} 个" + (f"，{fail_n} 个失败" if fail_n else "")
        if sync_error:
            return _report(self, False, msg + f"；设备配置保存失败: {sync_error}")
        return _report(self, True, msg)


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
        if not [it for it in prefs.plugin_items if it.selected]:
            return _report(self, False, "请先勾选要归类的插件")
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
            rec = db.get(k)
            if rec:
                rec["category"] = cat
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


class PM_OT_enable_pack(_PMBase):
    bl_idname = "plugin_manager.enable_pack"
    bl_label = "启用整组"
    bl_description = "启用当前分类下的全部插件"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        cat = prefs.active_category
        count, fail = 0, 0
        for rec in db.plugins.values():
            if (rec.get("category") or C.DEFAULT_CATEGORY) != cat:
                continue
            ok, _ = bridge.set_enabled(rec.get("module", ""), True)
            rec["enabled"] = bridge.is_module_enabled(rec.get("module", ""))
            count += 1 if ok else 0
            fail += 0 if ok else 1
        db.save()
        sync_error = _sync_device_config(prefs)
        msg = f"已启用 {count} 个插件" + (f"，{fail} 个失败" if fail else "")
        return _report(self, not sync_error, msg + (f"；设备配置保存失败: {sync_error}" if sync_error else ""))


class PM_OT_disable_pack(_PMBase):
    bl_idname = "plugin_manager.disable_pack"
    bl_label = "停用整组"
    bl_description = "停用当前分类下的全部插件"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not db:
            return _report(self, False, "插件库未就绪")
        cat = prefs.active_category
        count = 0
        for rec in db.plugins.values():
            if (rec.get("category") or C.DEFAULT_CATEGORY) != cat:
                continue
            ok, _ = bridge.set_enabled(rec.get("module", ""), False)
            rec["enabled"] = bridge.is_module_enabled(rec.get("module", ""))
            count += 1 if ok else 0
        db.save()
        sync_error = _sync_device_config(prefs)
        msg = f"已停用 {count} 个插件"
        return _report(self, not sync_error, msg + (f"；设备配置保存失败: {sync_error}" if sync_error else ""))


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

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        try:
            res = watcher.scan_inbox(prefs.library_path, db, move=True)
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
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        key = self.key or prefs.selected_key
        rec = db.get(key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        name = library.effective_name(rec)
        moved = library.remove_plugin(prefs.library_path, db, key, to_trash=True)
        # A failed move leaves both the source directory and DB record intact.
        # Do not report success in that case.
        if moved is None and db.get(key) is not None:
            return _report(self, False, f"移除失败：无法将 {name} 移入回收站，原插件已保留")
        if prefs.selected_key == key:
            prefs.selected_key = ""
        tail = f"（已移到回收站: {os.path.basename(moved)}）" if moved else ""
        return _report(self, True, f"已移除 {name}{tail}")


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


class PM_OT_update_all(_PMBase):
    bl_idname = "plugin_manager.update_all"
    bl_label = "一键更新全部"
    bl_description = "把库中所有检测到可更新的插件更新到最新版本"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        targets = [r for r in db.plugins.values() if r.get("update_available")]
        if not targets:
            return _report(self, True, "没有可更新的插件")
        ok = fail = 0
        errs = []
        _status(f"插件库：正在更新 {len(targets)} 个插件…")
        # 批量更新：逐个替换但**不逐个刷新** Blender（每次刷新约 0.25s，
        # N 个插件累计会长时间冻结界面），全部完成后统一刷新一次。
        for rec in targets:
            pkg = {
                "id": rec.get("id", ""),
                "version": rec.get("latest_version", ""),
                "archive_url": rec.get("latest_url", ""),
                "archive_hash": rec.get("latest_hash", ""),
                "archive_size": rec.get("latest_size", 0),
                "website": rec.get("latest_website", ""),
            }
            success, err = store.update_record(rec, pkg, prefs.library_path, db,
                                               defer_refresh=True)
            if success:
                ok += 1
            else:
                fail += 1
                errs.append(f"{rec.get('name')}: {err}")
        # 统一：保存一次、刷新一次、重建列表一次
        _clear_status()
        db.save()
        bridge.invalidate_module_index()
        bridge.refresh_blender()
        # Deferred replacements temporarily disable previously enabled plugins;
        # restore those states only after the single shared refresh.
        for rec in targets:
            if rec.get("enabled") and rec.get("module") and not bridge.is_module_enabled(rec["module"]):
                ok_restore, err_restore = bridge.set_enabled(rec["module"], True)
                rec["enabled"] = bridge.is_module_enabled(rec["module"])
                rec["restore_error"] = "" if ok_restore else (err_restore or "更新后重新启用失败")
        db.save()
        bridge.save_prefs()
        from . import items

        items.rebuild_items(prefs)
        for e in errs[:10]:
            print("[插件库] 更新失败:", e)
        if fail:
            return _report(self, False, f"更新完成：成功 {ok}，失败 {fail}（详见控制台）")
        return _report(self, True, f"已更新 {ok} 个插件")


# ---------------------------------------------------------------------------
# 在线商店
# ---------------------------------------------------------------------------
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
        layout.label(text=f"共 {len(results)} 个结果（显示前 40 个）")

        box = layout.box()
        for pkg in results[:40]:
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
            sub.label(text=(pkg.get("tagline") or "")[:66])

            if not store.is_compatible(pkg):
                row.label(text="版本不兼容", icon="ERROR")
            elif pkg["installed"]:
                if scan.compare_versions(pkg["version"], pkg["installed_version"]) > 0:
                    op = row.operator("plugin_manager.store_install", text="更新", icon="FILE_REFRESH")
                    op.package_id = pkg["id"]
                else:
                    row.label(text="已安装")
            else:
                op = row.operator("plugin_manager.store_install", text="安装", icon="IMPORT")
                op.package_id = pkg["id"]

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

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        pkg = next((p for p in _catalog_cache() if p["id"] == self.package_id), None)
        if not pkg:
            return _report(self, False, f"商店目录中未找到 {self.package_id}")

        # 已安装且版本更高 → 走替换更新，保留分类/备注/启用状态
        inst = store.installed_map(db).get(pkg["id"].lower())
        if inst:
            if scan.compare_versions(pkg["version"], inst.get("version", "0")) <= 0:
                return _report(self, True, f"{pkg['name']} 已是最新（{inst.get('version')}）")
            ok, err = store.update_record(inst, pkg, prefs.library_path, db)
            if not ok:
                return _report(self, False, f"更新失败: {err}")
            from . import items

            items.rebuild_items(prefs)
            return _report(self, True, f"{pkg['name']} 已更新到 {pkg['version']}")

        ok, err, rec = store.install_package(pkg, prefs.library_path, db)
        if not ok:
            return _report(self, False, f"安装失败: {err}")
        from . import items

        items.rebuild_items(prefs)
        return _report(self, True, f"已安装 {rec.get('name') or pkg['name']} {pkg['version']}")


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
    bl_label = "移入分类"

    key: StringProperty(default="")
    category: StringProperty(default="")

    def execute(self, context):
        db = _db(context)
        rec = db.get(self.key) if db else None
        if not rec:
            return _report(self, False, "未找到插件")
        rec["category"] = self.category or C.DEFAULT_CATEGORY
        # 只有非「未分类」才登记为分类，保持分类列表干净
        if rec["category"] != C.DEFAULT_CATEGORY:
            db.ensure_category(rec["category"])
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            items.rebuild_items(prefs)
        return _report(self, True, f"已移入分类: {rec['category']}")


def _category_enum(self, context):
    """分类下拉的候选项：从库文件刷新后取自模块缓存，不依赖 context。"""
    from . import items as _items

    _items.refresh_cache(bridge.get_prefs())
    return [(c, c, "") for c in _items.category_options()]


class PM_OT_assign_category(Operator):
    """弹出下拉框，把当前选中的插件移入某个已有分类（或未分类）。"""

    bl_idname = "plugin_manager.assign_category"
    bl_label = "移入分类"

    key: StringProperty(default="")
    category: bpy.props.EnumProperty(name="分类", items=_category_enum)

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
        rec["category"] = self.category or C.DEFAULT_CATEGORY
        if rec["category"] != C.DEFAULT_CATEGORY:
            db.ensure_category(rec["category"])
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            items.rebuild_items(prefs)
        return _report(self, True, f"已移入分类: {rec['category']}")


class PM_OT_add_category(Operator):
    bl_idname = "plugin_manager.add_category"
    bl_label = "新建分类"

    name: StringProperty(name="分类名", default="")

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
            return _report(self, True, f"已新建分类: {self.name}")
        return _report(self, False, "分类为空或已存在")


class PM_OT_reset_auto_categories(_PMBase):
    bl_idname = "plugin_manager.reset_auto_categories"
    bl_label = "清理自动分类"
    bl_description = ("把插件自带元数据分类（你未手动改动过的）统一收回「未分类」，"
                      "只保留你自己创建和指定的分类")

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
                       f"剩余分类 {res['categories_left']} 个")


class PM_OT_rename_category(Operator):
    bl_idname = "plugin_manager.rename_category"
    bl_label = "重命名分类"

    old: StringProperty(default="")
    new: StringProperty(name="新名称", default="")

    def invoke(self, context, event):
        self.new = self.old
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        db = _db(context)
        if not db or not self.old:
            return _report(self, False, "插件库未就绪")
        db.rename_category(self.old, self.new)
        db.save()
        return _report(self, True, f"分类已重命名为 {self.new}")


class PM_OT_delete_category(Operator):
    bl_idname = "plugin_manager.delete_category"
    bl_label = "删除分类"

    name: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        db = _db(context)
        if not db or not self.name:
            return _report(self, False, "插件库未就绪")
        db.delete_category(self.name)
        db.save()
        return _report(self, True, f"已删除分类: {self.name}")


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
    bl_description = "扫描 Blender 现有脚本目录与本地扩展仓库，列出可收编的插件"

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        cands = migrate.collect_candidates(prefs.library_path, db)
        prefs.candidate_count = len(cands)
        return _report(self, True, f"发现 {len(cands)} 个可收编插件，请查看'迁移'面板")


class PM_OT_import_candidates(_PMBase):
    bl_idname = "plugin_manager.import_candidates"
    bl_label = "收编全部"
    bl_description = "把扫描到的插件全部导入插件库"

    move: BoolProperty(name="移动而非复制", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        cands = migrate.collect_candidates(prefs.library_path, db)
        stats = migrate.import_candidates(prefs.library_path, db, cands, move=self.move)
        self.report({"INFO"}, f"收编 {stats['ok']} 个，跳过 {stats['skip']}，失败 {stats['fail']}")
        for err in stats["errors"][:5]:
            print("[插件库] 收编失败:", err)
        return {"FINISHED"}


class PM_OT_pick_category(Operator):
    bl_idname = "plugin_manager.pick_category"
    bl_label = "选择分类"

    category: StringProperty(default="")

    def execute(self, context):
        prefs = _prefs(context)
        if prefs is None:
            return _report(self, False, "插件库未启用")
        prefs.active_category = self.category or "全部"
        from . import items

        items.maybe_rebuild(prefs, force=True)
        return _report(self, True, f"已切换到分类: {prefs.active_category}")


class PM_OT_rename_display(Operator):
    """设置/清除插件的「显示名称」（别名）。

    实际插件名来自插件元数据，保持只读；别名只影响界面显示，
    方便把英文插件改成中文，或把冗长的名字缩短。
    """

    bl_idname = "plugin_manager.rename_display"
    bl_label = "设置显示名称"

    key: StringProperty(default="")
    display_name: StringProperty(name="显示名称", default="")
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
        if self.clear:
            rec["display_name"] = ""
            db.save()
            from . import items

            if prefs:
                items.rebuild_items(prefs)
            return _report(self, True, "已清除显示名称")
        rec["display_name"] = self.display_name.strip()
        db.save()
        from . import items

        if prefs:
            items.rebuild_items(prefs)
        return _report(self, True, f"显示名称已设为: {rec['display_name'] or '（空）'}")


class PM_OT_edit_meta(Operator):
    bl_idname = "plugin_manager.edit_meta"
    bl_label = "编辑插件信息"

    key: StringProperty(default="")
    display_name: StringProperty(name="显示名称（别名）", default="")
    note: StringProperty(name="备注", default="")
    category: StringProperty(name="分类", default="")
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
        self.display_name = rec.get("display_name", "")
        self.note = rec.get("note", "")
        self.category = rec.get("category", "") or C.DEFAULT_CATEGORY
        self.tags = ", ".join(rec.get("tags", []) or [])
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        db, rec = self._rec(context)
        if not rec:
            return _report(self, False, "未找到插件")
        rec["display_name"] = self.display_name.strip()
        rec["note"] = self.note.strip()
        rec["category"] = self.category.strip() or C.DEFAULT_CATEGORY
        rec["tags"] = [t.strip() for t in self.tags.split(",") if t.strip()]
        if rec["category"] != C.DEFAULT_CATEGORY:
            db.ensure_category(rec["category"])
        db.save()
        from . import items

        prefs = _prefs(context)
        if prefs:
            items.maybe_rebuild(prefs, force=True)
        return _report(self, True, "已保存插件信息")


classes = (
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
    PM_OT_batch_set_startup,
    PM_OT_enable_pack,
    PM_OT_disable_pack,
    PM_OT_import,
    PM_OT_import_zip,
    PM_OT_scan_inbox,
    PM_OT_show_report,
    PM_OT_copy_report,
    PM_OT_open_report_log,
    PM_OT_remove,
    PM_OT_check_updates,
    PM_OT_update_all,
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
