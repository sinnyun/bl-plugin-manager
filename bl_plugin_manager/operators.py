"""操作符：刷新、启停、导入、扫描、更新检测、分类与备注维护。"""

from __future__ import annotations

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    StringProperty,
)
from bpy.types import Operator

from . import bridge, constants as C, library, migrate, scan, store, updates, watcher
from .db import LibraryDB, now_iso
from .security.paths import UnsafeLibraryPathError, resolve_record_path
from .storage.shared_db import SharedDatabase


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


def _initialize_schema2(root: str) -> LibraryDB:
    """Archive non-schema-2 metadata, then open the clean shared database."""
    report = SharedDatabase(root).initialize()
    if report.status == "CORRUPT":
        raise RuntimeError("插件库元数据已损坏，已保持只读，未覆盖原文件")
    return LibraryDB(root, use_cache=False)


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
            state = bridge.register_library(prefs.library_path, save=True)
            library.sync_library(prefs.library_path, db)
        finally:
            _clear_status()
        if not (state["script_dir"] and state["repo"]):
            return _report(self, False, "注册未完整完成，请检查控制台输出")
        n = len(res["imported"])
        if res["created"]:
            return _report(self, True, f"已创建空插件库并挂载: {prefs.library_path}")
        if n:
            return _report(self, True, f"已挂载插件库，并收编 {n} 个散落插件")
        return _report(self, True, "插件库已启用并挂载到 Blender")


class PM_OT_unify_store(_PMBase):
    bl_idname = "plugin_manager.unify_store"
    bl_label = "让官方商店使用本插件库"
    bl_description = ("把 Blender 官方扩展商店的目录指向本插件库，并把官方目录中已装的\n"
                      "扩展迁移进库。之后在官方商店面板下载/更新，都直接进入插件库管理")

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
                bridge.save_prefs()
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
            # 迁移后清理空的官方目录内容
            if self.move:
                for e in os.scandir(src_dir):
                    if e.name.startswith("."):
                        continue
                    p = e.path
                    try:
                        if e.is_dir():
                            import shutil as _sh
                            _sh.rmtree(p, ignore_errors=True)
                        elif not e.name.lower().endswith((".json",)):
                            os.remove(p)
                    except OSError:
                        pass

        # 3) 官方商店仓库指向库 + 开启联网
        res = bridge.unify_store_with_library(root)
        library.sync_library(root, db)
        bridge.save_prefs()
        from . import items as _items

        _items.rebuild_items(prefs)

        msg = f"已迁移 {moved} 个扩展到插件库"
        if failed:
            msg += f"，{len(failed)} 个失败"
        msg += "；官方商店已指向插件库"
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
        state = bridge.register_library(path, save=True)

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

        if res["failed"]:
            msg += f"（{len(res['failed'])} 个收编失败，详见控制台）"
        if not (state["script_dir"] and state["repo"]):
            return _report(self, False, "挂载未完整完成，请检查控制台")
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
        bridge.unregister_library(prefs.library_path, save=True)
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
    bl_description = "逐个尝试加载插件，测出哪些能在当前 Blender 下正常工作；结果用 ✓ / ✗ 标出"

    include_enabled: BoolProperty(
        name="也测试已启用的插件",
        description="已启用的插件通常可用；勾选后一并复测（更慢）",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def execute(self, context):
        prefs = _prefs(context)
        db = _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")

        enabled_set = bridge.enabled_modules()
        targets = []
        skipped_themes = 0
        for key, rec in db.plugins.items():
            mod = rec.get("module")
            if not mod or rec.get("missing"):
                continue
            # 主题(theme)不是插件，无法用 addon_enable 启用，跳过
            if (rec.get("pkg_type") or rec.get("type") or "").strip() == "theme":
                skipped_themes += 1
                continue
            already = mod in enabled_set
            if already and not self.include_enabled:
                continue
            targets.append((key, rec, already))

        if not targets:
            return _report(self, True, "没有需要测试的插件")

        total = len(targets)
        _status(f"插件库：正在测试 0/{total} …")
        ok_n = fail_n = 0
        fails = []
        # 只清理本轮实际参与测试的记录；未参与的历史结果仍然有效。
        for _key, rec, _was_enabled in targets:
            rec["load_state"] = ""
            rec["load_error"] = ""
            rec["restore_error"] = ""
        db.save()
        try:
            for done, (key, rec, was_enabled) in enumerate(targets, 1):
                if done % 3 == 1 or done == total:
                    _status(f"插件库：正在测试 {done}/{total} …")
                mod = rec["module"]
                rec["restore_error"] = ""
                # 已启用插件只有在用户明确选择复测时才做真实停用/重载。
                if was_enabled and self.include_enabled:
                    off_ok, off_err = bridge.set_enabled(mod, False)
                    if not off_ok or bridge.is_module_enabled(mod):
                        rec["load_state"] = "failed"
                        rec["load_error"] = "无法开始复测：停用失败"
                        rec["restore_error"] = off_err or rec["load_error"]
                        rec["enabled"] = bridge.is_module_enabled(mod)
                        fail_n += 1
                        fails.append({"name": rec.get("name"), "key": key,
                                      "error": rec["load_error"], "residue": 0})
                        db.upsert(key, rec)
                        continue

                # 尝试启用（能完整加载即可用），随后严格恢复原状态。
                success, err = bridge.set_enabled(mod, True)
                if success:
                    restore_ok, restore_err = True, ""
                    if not was_enabled:
                        restore_ok, restore_err = bridge.set_enabled(mod, False)
                        restore_ok = restore_ok and not bridge.is_module_enabled(mod)
                    elif self.include_enabled:
                        # 已启用插件已在上面停用，现在需要确认重新启用成功。
                        restore_ok = bridge.is_module_enabled(mod)
                    if restore_ok:
                        rec["load_state"] = "ok"
                        rec["load_error"] = ""
                        rec["enabled"] = was_enabled
                        ok_n += 1
                    else:
                        rec["load_state"] = "failed"
                        rec["load_error"] = "加载成功但无法恢复原启用状态"
                        rec["restore_error"] = restore_err or rec["load_error"]
                        rec["enabled"] = bridge.is_module_enabled(mod)
                        fail_n += 1
                        fails.append({"name": rec.get("name"), "key": key,
                                      "error": rec["restore_error"], "residue": 0})
                else:
                    # 失败：set_enabled 已尝试清理残留；记录剩余残留数量，
                    # 便于界面提示"需重启 Blender 清理"
                    left = bridge.module_residue(mod)
                    rec["residue"] = left
                    rec["load_state"] = "failed"
                    rec["load_error"] = err or "未知错误"
                    rec["last_error"] = err or rec.get("last_error", "")
                    rec["enabled"] = bridge.is_module_enabled(mod)
                    fail_n += 1
                    fails.append({"name": rec.get("name"), "key": key,
                                  "error": err, "residue": left})
                db.upsert(key, rec)
        finally:
            _clear_status()

        db.save()
        bridge.save_prefs()

        # 把失败项写进扫描报告，便于查看/复制
        prefs.report_items.clear()
        for f_ in fails:
            it = prefs.report_items.add()
            it.name = f_.get("name") or ""
            it.path = f_.get("key") or ""
            it.kind = "兼容性"
            it.status = "failed"
            it.detail = f_.get("error") or ""
        prefs.report_summary = (f"一键测试完成：✓ 支持 {ok_n} 个，"
                               f"✗ 不支持 {fail_n} 个（共 {len(targets)} 个）")
        log = _write_report_log(prefs, {"entries": [
            {"name": f_.get("name"), "path": f_.get("key"), "kind": "兼容性",
             "status": "failed", "detail": f_.get("error")} for f_ in fails]})

        from . import items

        items.rebuild_items(prefs)
        if fail_n:
            bpy.ops.plugin_manager.show_report()
            return _report(self, False, f"可用 {ok_n}，不可用 {fail_n}（详见报告）" + (f"；日志 {log}" if log else ""))
        return _report(self, True, f"实测完成：{ok_n} 个插件均可用")


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
                db.upsert(key, rec)
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
        from . import items

        items.rebuild_items(prefs)
        msg = f"已启用 {stats['enabled']}"
        if self.disable_unmarked:
            msg += f"，已停用 {stats['disabled']}"
        if stats["failed"]:
            bpy.ops.plugin_manager.show_report()
            msg += f"，{len(stats['failed'])} 个失败（见报告）"
        return _report(self, not stats["failed"], msg)


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
        from . import items

        items.rebuild_items(prefs)
        verb = "启用" if self.value else "停用"
        _redraw()
        return _report(self, True, f"已批量{verb} {ok_n} 个" + (f"，{fail_n} 个失败" if fail_n else ""))


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
        return _report(self, True, f"已启用 {count} 个插件" + (f"，{fail} 个失败" if fail else ""))


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
        return _report(self, True, f"已停用 {count} 个插件")


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
