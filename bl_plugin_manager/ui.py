"""界面：3D 视图侧边栏 (N 面板) 的插件库管理面板。

设计原则：**主面板只放高频操作，其余收进折叠子面板与菜单**。
所有功能都保留，只是分层收纳，避免一次性铺满整个侧边栏。
"""

from __future__ import annotations

import os

import bpy
from bpy.types import Menu, Panel, UIList

from . import bridge, constants as C, items


# ---------------------------------------------------------------------------
# 列表行
# ---------------------------------------------------------------------------
def _compat_display(item) -> tuple[str, str, bool]:
    """Return the compact compatibility text used by each list row.

    A declared version range is not the same as a successful runtime load.
    Keep the untested state explicit so a plugin cannot look startable merely
    because its metadata says it targets the current Blender version.
    """
    state = items.compatibility_status(getattr(item, "compat", ""), getattr(item, "load_state", ""))
    names = {"load_failed": "启动失败", "out_of_range": "版本不兼容",
             "load_ok": "实测可启动", "declared": "声明兼容 · 未测", "unknown": "未测"}
    maximum = getattr(item, "max_version_text", "")
    maximum = f"最高 {maximum}" if maximum and maximum != "不限" else "未声明上限"
    return names[state], maximum, state in {"load_failed", "out_of_range"}


def _progress_bar(layout, factor, text):
    """画进度条；不支持 progress 的布局回退为文本，保证进度始终可读。"""
    try:
        layout.progress(factor=max(0.0, min(1.0, float(factor))), text=text)
    except Exception:
        layout.label(text=text)


class PM_UL_plugins(UIList):
    bl_idname = "PM_UL_plugins"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        row = layout.row(align=True)

        # 勾选（批量操作用）
        op = row.operator("plugin_manager.toggle_select", text="",
                          icon="CHECKBOX_HLT" if item.selected else "CHECKBOX_DEHLT",
                          emboss=False)
        op.key = item.key

        op = row.operator("plugin_manager.toggle", text="",
                          icon="PLAY" if item.enabled else "PAUSE", emboss=False)
        op.key, op.enable = item.key, not item.enabled

        row.label(text=item.display_name or item.name or item.key,
                  icon="SOLO_ON" if item.favorite else "NONE")
        label, _, warning = _compat_display(item)
        icon = "ERROR" if warning else "NONE"
        if item.missing:
            label, icon, warning = "文件缺失", "ERROR", True
        elif item.last_error:
            label, icon, warning = "启用失败", "ERROR", True
        elif item.update_available and not warning:
            label, icon = "有更新", "FILE_REFRESH"
        status = row.row(align=True)
        status.alignment = "RIGHT"
        status.alert = warning
        status.label(text=label, icon=icon)


class PM_UL_report(UIList):
    """扫描/导入结果列表：状态图标 + 名称 + 类型。"""

    bl_idname = "PM_UL_report"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        row = layout.row(align=True)
        if item.status == "failed":
            row.label(text="", icon="CANCEL")
        elif item.status == "imported":
            row.label(text="", icon="CHECKMARK")
        else:
            row.label(text="", icon="INFO")
        row.label(text=item.name or "(未命名)")
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.kind)


# ---------------------------------------------------------------------------
# 菜单：把低频操作收纳起来，保持面板清爽
# ---------------------------------------------------------------------------
class PM_MT_library(Menu):
    """库级操作菜单。"""

    bl_idname = "PM_MT_library"
    bl_label = "库操作"

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            return
        if prefs.library_path:
            sub = layout.column()
            sub.enabled = False
            sub.label(text=prefs.library_path)
        layout.separator()
        layout.operator("plugin_manager.pick_library_path", icon="FILEBROWSER", text="选择其它库…")
        layout.operator("plugin_manager.refresh", icon="FILE_REFRESH", text="刷新列表")


class PM_MT_category(Menu):
    """分类选择与管理菜单（替代原来占满面板的分类网格）。"""

    bl_idname = "PM_MT_category"
    bl_label = "分类"

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            return
        counts = items.category_counts(prefs)
        for cat in prefs.category_items:
            name = cat.name or C.DEFAULT_CATEGORY
            icon = "RADIOBUT_ON" if prefs.active_category == name else "RADIOBUT_OFF"
            op = layout.operator(
                "plugin_manager.pick_category",
                text=f"{name}  ({counts.get(name, 0)})", icon=icon,
            )
            op.category = name
        layout.separator()
        layout.operator("plugin_manager.add_category", icon="ADD", text="新建分类…")
        if prefs.active_category not in ("", "全部", C.DEFAULT_CATEGORY):
            op = layout.operator("plugin_manager.rename_category", icon="SORTALPHA",
                                 text="重命名当前分类…")
            op.old = prefs.active_category
            op = layout.operator("plugin_manager.delete_category", icon="TRASH",
                                 text="删除当前分类")
            op.name = prefs.active_category
            layout.separator()
            layout.operator("plugin_manager.enable_pack", icon="CHECKBOX_HLT",
                            text="启用本分类全部")
            layout.operator("plugin_manager.disable_pack", icon="CHECKBOX_DEHLT",
                            text="停用本分类全部")
        layout.separator()
        layout.operator("plugin_manager.reset_auto_categories", icon="LOOP_BACK",
                        text="清理自动分类")


class PM_MT_plugin(Menu):
    """单个插件的次级操作（详情区用菜单收纳，避免按钮堆叠）。"""

    bl_idname = "PM_MT_plugin"
    bl_label = "更多操作"

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            return
        key = prefs.selected_key
        rec = _selected(prefs)

        op = layout.operator("plugin_manager.rename_display", icon="SORTALPHA",
                             text="设置显示名称…")
        op.key = key
        if rec and (rec.get("display_name") or "").strip():
            op = layout.operator("plugin_manager.rename_display", icon="X",
                                 text="清除显示名称")
            op.key = key
            op.clear = True

        layout.separator()
        op = layout.operator("plugin_manager.assign_category", icon="FILE_FOLDER",
                             text="移入分类…")
        op.key = key
        if rec:
            auto = (rec.get("auto_category") or "").strip()
            if auto and auto != C.DEFAULT_CATEGORY and auto != rec.get("category"):
                op = layout.operator("plugin_manager.set_category", icon="IMPORT",
                                     text=f"采用自带分类「{auto}」")
                op.key = key
                op.category = auto

        layout.separator()
        layout.operator("plugin_manager.edit_meta", icon="GREASEPENCIL", text="编辑信息…")
        if rec and rec.get("kind") == C.KIND_ADDON and not rec.get("missing"):
            layout.operator("plugin_manager.relink_addon", icon="LINKED").key = key
        op = layout.operator("plugin_manager.open_folder", icon="FILE_FOLDER",
                             text="打开所在文件夹")
        op.key = key


# ---------------------------------------------------------------------------
# 主面板：只保留高频内容
# ---------------------------------------------------------------------------
class PM_PT_main(Panel):
    bl_idname = "PM_PT_main"
    bl_label = C.ADDON_NAME
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if prefs is None:
            layout.label(text="插件库未启用", icon="ERROR")
            return

        items.maybe_rebuild(prefs)
        if not PM_PT_main.draw_library_status(layout, prefs):
            return

        row = layout.row(align=True)
        row.operator("plugin_manager.scan_inbox", text="扫描投放区", icon="FILE_REFRESH")
        row.operator("plugin_manager.import_zip", text="导入 ZIP", icon="IMPORT")
        row = layout.row(align=True)
        row.prop(prefs, "search", text="", icon="VIEWZOOM")
        row.menu("PM_MT_category", text=prefs.active_category, icon="FILE_FOLDER")
        filters = layout.row(align=True)
        filters.prop(prefs, "only_enabled", text="已启用", toggle=True)
        filters.prop(prefs, "only_updates", text="有更新", toggle=True)
        filters.prop(prefs, "only_favorites", text="收藏", toggle=True)
        filters.prop(prefs, "only_incompatible", text="有问题", toggle=True)

        if len(prefs.plugin_items):
            layout.template_list("PM_UL_plugins", "", prefs, "plugin_items", prefs, "active_index",
                                 rows=9, sort_lock=True)
            yes, no, unknown = _support_counts(prefs)
            layout.label(text=f"当前结果 {len(prefs.plugin_items)} · 可启动 {yes} · 有问题 {no} · 未测 {unknown}")
        else:
            filtered = (prefs.search or prefs.active_category not in ("", "全部")
                        or any((prefs.only_enabled, prefs.only_updates, prefs.only_favorites,
                                prefs.only_incompatible)))
            layout.label(text="没有匹配的插件，请调整筛选" if filtered else "库内还没有插件，可导入或扫描投放区",
                         icon="INFO")

        if prefs.compat_running:
            box = layout.box()
            box.label(text=f"启动测试 {prefs.compat_done}/{prefs.compat_total}", icon="TIME")
            if prefs.compat_current:
                box.label(text=prefs.compat_current)
            _progress_bar(box, prefs.compat_done / max(1, prefs.compat_total),
                          f"可启动 {prefs.compat_ok} · 失败 {prefs.compat_fail}")
            if prefs.compat_cancelling:
                box.label(text="等待当前插件处理结束…", icon="INFO")
            else:
                box.operator("plugin_manager.cancel_compat", icon="X", text="取消测试")
        elif prefs.archive_running:
            box = layout.box()
            _progress_bar(box, prefs.archive_progress, prefs.archive_status)
            if prefs.archive_cancelling:
                box.label(text="正在取消下载…", icon="INFO")
            else:
                box.operator("plugin_manager.cancel_archives", icon="X", text="取消")
        else:
            layout.operator("plugin_manager.verify_compat", icon="CHECKMARK", text="测试插件能否启动…")

    @staticmethod
    def draw_library_status(layout, prefs) -> bool:
        from .storage import machine_config

        root = prefs.library_path
        if prefs.pending_library_path:
            box = layout.box()
            box.label(text=prefs.pending_library_path, icon="FILE_FOLDER")
            box.label(text=prefs.library_preview)
            box.label(text="将应用扩展仓库、脚本目录与本机插件启用清单")
            row = box.row(align=True)
            row.operator("plugin_manager.setup_library", text="启用管理", icon="LINKED")
            row.operator("plugin_manager.pick_library_path", text="更换…", icon="FILEBROWSER")
            row.operator("plugin_manager.pick_library_path", text="取消").cancel_preview = True
            if not root:
                return False
        if not root:
            layout.operator("plugin_manager.pick_library_path", text="选择插件库…", icon="FILEBROWSER")
            return False
        row = layout.row(align=True)
        name = os.path.basename(root.rstrip("\\/")) or root
        if not os.path.isdir(root):
            row.label(text="插件库离线", icon="ERROR")
            row.operator("plugin_manager.setup_library", text="重连")
            row.operator("plugin_manager.pick_library_path", text="更换…")
            return False
        if items.database_status(prefs) in {"CORRUPT", "UNSUPPORTED"}:
            layout.label(text="资料库损坏或版本不受支持，原文件已保留", icon="ERROR")
            layout.operator("plugin_manager.show_warnings", text="查看诊断")
            return False
        if not machine_config.load().get("management_enabled"):
            row.label(text=f"{name} · 管理已停止", icon="UNLINKED")
            row.operator("plugin_manager.setup_library", text="启用管理")
            row.operator("plugin_manager.pick_library_path", text="更换…")
            return False
        if not bridge.is_registered(root):
            row.label(text=f"{name} · 挂载未就绪", icon="ERROR")
            row.operator("plugin_manager.setup_library", text="重连")
            row.operator("plugin_manager.pick_library_path", text="更换…")
            return False
        row.label(text=name, icon="CHECKMARK")
        row.menu("PM_MT_library", text="", icon="DOWNARROW_HLT")
        return True


class PM_PT_detail(Panel):
    """选中插件的详情与操作（默认展开，随时可见）。"""

    bl_idname = "PM_PT_detail"
    bl_label = "插件详情"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_parent_id = "PM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        sel = _selected(prefs) if prefs else None
        if not sel:
            layout.label(text="在列表中选择一个插件", icon="INFO")
            return

        col = layout.column(align=True)
        # 名称
        alias = (sel.get("display_name") or "").strip()
        actual = sel.get("name") or sel.get("folder_name", "")
        row = col.row(align=True)
        row.label(text=alias or actual, icon="PLUGIN")
        if alias and alias != actual:
            sub = row.row()
            sub.enabled = False
            sub.label(text=f"（{actual}）")

        # 关键信息一行
        bits = [C.KIND_LABELS.get(sel.get("kind"), "")]
        if sel.get("version"):
            bits.append(f"v{sel['version']}")
        bits.append(sel.get("category") or C.DEFAULT_CATEGORY)
        col.label(text=" · ".join(b for b in bits if b))

        # 支持的 Blender 版本（不兼容时醒目提示）
        from . import scan as _scan

        bmin = sel.get("blender_min", "")
        bmax = sel.get("blender_max", "")
        state, detail = _scan.blender_compat(bmin, bmax)
        hi = _scan.max_version_label(bmax) if bmax else "未声明上限"
        lo = _scan.version_str(bmin) or "不限"
        try:
            cur = ".".join(str(v) for v in bpy.app.version)
        except Exception:
            cur = "?"
        col.label(text=f"支持版本：最低 {lo}　最高 {hi}")
        row = col.row(align=True)
        if state in ("too_new", "too_old"):
            row.alert = True
            row.label(text=f"✗ 不支持当前 Blender {cur}（{detail}）", icon="ERROR")
        elif state == "ok":
            row.label(text=f"✓ 支持当前 Blender {cur}", icon="CHECKMARK")
        else:
            sub = row.row()
            sub.enabled = False
            sub.label(text="未声明版本要求（以实测结果为准）", icon="INFO")
        if state in ("too_new", "too_old"):
            col.label(text=f"（{detail}）", icon="INFO")
        # 实测结果（点"一键测试插件支持"后才有）
        if sel.get("load_state") == "failed":
            col.label(text=f"✗ 不支持: {(sel.get('load_error') or '')[:60]}", icon="CANCEL")
            # 注册残留提示：这类残留会让界面持续报错，需清理（极端情况要重启）
            if int(sel.get("residue") or 0) > 0:
                row = col.row(align=True)
                row.alert = True
                row.label(text=f"有 {sel['residue']} 项注册残留（会导致界面报错）",
                          icon="ERROR")
                row.operator("plugin_manager.cleanup_residue", text="清理", icon="TRASH")
        elif sel.get("load_state") == "ok":
            col.label(text="已实测可启动", icon="CHECKMARK")

        if sel.get("update_source_required"):
            col.label(text="更新前需在商店确认来源", icon="INFO")
        if sel.get("update_available") and sel.get("latest_version"):
            col.label(text=f"可更新到 v{sel['latest_version']}", icon="FILE_REFRESH")
        if sel.get("last_error"):
            col.label(text=f"启用失败: {sel['last_error']}", icon="CANCEL")
        if sel.get("restore_error"):
            col.label(text=f"状态恢复失败: {sel['restore_error']}", icon="ERROR")
        for issue in (sel.get("issues") or []):
            col.label(text=f"⚠ {issue}", icon="ERROR")
        if sel.get("note"):
            col.label(text=f"备注: {sel['note']}")

        # 主操作
        row = col.row(align=True)
        op = row.operator("plugin_manager.toggle",
                          text="停用" if sel.get("enabled") else "启用",
                          icon="CHECKBOX_DEHLT" if sel.get("enabled") else "CHECKBOX_HLT")
        op.key = sel.get("key", "")
        op.enable = not sel.get("enabled")
        op = row.operator("plugin_manager.set_startup",
                          text="取消自启" if sel.get("startup") else "设为自启",
                          icon="RADIOBUT_ON" if sel.get("startup") else "RADIOBUT_OFF")
        op.key = sel.get("key", "")
        op.value = not sel.get("startup")
        op.use_selection = False
        op = row.operator("plugin_manager.set_favorite", text="取消收藏" if sel.get("favorite") else "收藏", icon="SOLO_ON")
        op.key = sel.get("key", "")
        op.value = not sel.get("favorite")

        row = col.row(align=True)
        row.operator("plugin_manager.edit_meta", text="编辑信息", icon="GREASEPENCIL")
        row.menu("PM_MT_plugin", text="更多操作", icon="DOWNARROW_HLT")
        op = row.operator("plugin_manager.remove_plugin", text="", icon="TRASH")
        op.key = sel.get("key", "")


class PM_PT_batch(Panel):
    """批量操作（勾选列表项后使用）。"""

    bl_idname = "PM_PT_batch"
    bl_label = "批量操作"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_parent_id = "PM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            return
        n = len(items.all_selected_keys())
        hidden = n - len(items.selected_keys(prefs))
        layout.label(text=f"已勾选 {n} 个" + (f" · 筛选隐藏 {hidden} 个" if hidden else ""))
        row = layout.row(align=True)
        row.operator("plugin_manager.select_all", text="全选当前结果").value = True
        row.operator("plugin_manager.select_all", text="清空全部").value = False

        actions = layout.column(align=True)
        actions.enabled = bool(n)
        row = actions.row(align=True)
        row.operator("plugin_manager.batch_enable", text="启用", icon="CHECKBOX_HLT").value = True
        row.operator("plugin_manager.batch_enable", text="停用", icon="CHECKBOX_DEHLT").value = False
        row.operator("plugin_manager.batch_set_category", text="归入分类…", icon="FILE_FOLDER")
        row = actions.row(align=True)
        row.operator("plugin_manager.batch_set_startup", text="设为自启",
                     icon="RADIOBUT_ON").value = True
        row.operator("plugin_manager.batch_set_startup", text="取消自启",
                     icon="RADIOBUT_OFF").value = False


class PM_PT_actions(Panel):
    """导入、在线商店、更新、扫描报告。"""

    bl_idname = "PM_PT_actions"
    bl_label = "导入与在线商店"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_parent_id = "PM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            return

        row = layout.row(align=True)
        row.operator("plugin_manager.import_plugin", icon="IMPORT", text="导入文件夹")
        row.operator("plugin_manager.import_zip", icon="FILE_ARCHIVE", text="导入压缩包")
        row.operator("plugin_manager.scan_inbox", icon="FILE_REFRESH", text="扫描投放区")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="在线商店 (extensions.blender.org)", icon="URL")
        row = col.row(align=True)
        row.operator("plugin_manager.store_open", icon="URL", text="浏览/安装")
        row.operator("plugin_manager.check_updates", icon="FILE_REFRESH", text="检查更新")
        row = col.row(align=True)
        row.operator("plugin_manager.update_all", icon="TRIA_DOWN_BAR", text="预览批量更新")
        row.operator("plugin_manager.store_sync", icon="FILE_REFRESH", text="")

        col.operator("plugin_manager.restore_plugin", text="从回收站恢复", icon="LOOP_BACK")
        if prefs.archive_retry:
            col.operator("plugin_manager.retry_archives", text="重试失败项", icon="FILE_REFRESH")

        layout.separator()
        row = layout.row(align=True)
        row.operator("plugin_manager.show_report", icon="INFO", text="查看扫描报告")
        row.operator("plugin_manager.copy_report", icon="COPY_ID", text="")
        row.operator("plugin_manager.open_report_log", icon="TEXT", text="")
        if prefs.report_summary:
            layout.label(text=prefs.report_summary)


# ---------------------------------------------------------------------------
def _support_counts(prefs) -> tuple[int, int, int]:
    """当前可见列表的支持统计：(✓支持, ✗不支持, ?未测试)。"""
    yes = sum(1 for it in prefs.plugin_items if it.supported == "yes")
    no = sum(1 for it in prefs.plugin_items if it.supported == "no")
    unk = sum(1 for it in prefs.plugin_items if it.supported not in ("yes", "no"))
    return yes, no, unk


def _startup_stats(prefs) -> dict:
    if not prefs.library_path:
        return {"total": 0, "startup": 0, "not_startup": 0}
    from .db import LibraryDB

    return LibraryDB(prefs.library_path).startup_stats()


def _selected(prefs):
    if not prefs or not prefs.library_path or not prefs.selected_key:
        return None
    from .db import LibraryDB

    return LibraryDB(prefs.library_path).get(prefs.selected_key)


classes = (
    PM_UL_plugins,
    PM_UL_report,
    PM_MT_library,
    PM_MT_category,
    PM_MT_plugin,
    PM_PT_main,
    PM_PT_detail,
    PM_PT_batch,
    PM_PT_actions,
)
