"""界面：3D 视图侧边栏 (N 面板) 的插件库工作台。

信息架构分层：
* 主面板：库状态条 → 工作分组（多组叠加开关/自启/常驻/独占切换）→ 插件搜索与列表
* 插件详情（默认展开）：中文别名一键编辑、来源徽章、所属分组多选、启停/自启/收藏
* 批量操作（折叠，与主面板「批量」开关联动）
* 导入、商店与测试（折叠，消除主面板重复入口与低频干扰）
"""

from __future__ import annotations

import os

import bpy
from bpy.types import Menu, Panel, UIList

from . import bridge, constants as C, items


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _compat_display(item) -> tuple[str, str, bool]:
    """Return compact compatibility status for list rows."""
    state = items.compatibility_status(getattr(item, "compat", ""), getattr(item, "load_state", ""))
    names = {
        "load_failed": "启动失败",
        "out_of_range": "版本不兼容",
        "load_ok": "实测可启动",
        "declared": "声明兼容 · 未测",
        "unknown": "未测",
    }
    maximum = getattr(item, "max_version_text", "")
    maximum = f"最高 {maximum}" if maximum and maximum != "不限" else "未声明上限"
    return names[state], maximum, state in {"load_failed", "out_of_range"}


def _progress_bar(layout, factor, text):
    """画进度条；不支持 progress 的布局回退为文本，保证进度始终可读。"""
    try:
        layout.progress(factor=max(0.0, min(1.0, float(factor))), text=text)
    except Exception:
        layout.label(text=text)


# ---------------------------------------------------------------------------
# 列表行：工作分组 & 插件列表 & 扫描报告
# ---------------------------------------------------------------------------
class PM_UL_groups(UIList):
    """工作分组列表：含默认「全部插件」行，点击整行切换筛选，图标控制多组开关/自启/常驻/独占。"""

    bl_idname = "PM_UL_groups"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        row = layout.row(align=True)

        # 默认第 0 行：全部插件（只做切换查看全部，不混入分组开关图标）
        if getattr(item, "is_all", False):
            row.label(
                text=f"{item.name} ({item.enabled}/{item.total})",
                icon="OUTLINER_COLLECTION",
            )
            return

        # 1) 分组开关（支持多组同时开启 / 关闭）
        op = row.operator(
            "plugin_manager.group_toggle",
            text="",
            icon="CHECKBOX_HLT" if item.active else "CHECKBOX_DEHLT",
            emboss=False,
        )
        op.name = item.name
        op.active = not item.active

        # 2) 分组名 + (已启用/总数)：点击行即通过 active_group_index 切换筛选
        row.label(
            text=f"{item.name} ({item.enabled}/{item.total})",
            icon="PINNED" if item.pinned else "NONE",
        )

        # 3) 右侧紧凑动作：自启 / 常驻 / 独占切换
        right = row.row(align=True)
        right.alignment = "RIGHT"

        op = right.operator(
            "plugin_manager.group_set_startup",
            text="",
            icon="RADIOBUT_ON" if item.startup else "RADIOBUT_OFF",
            emboss=False,
        )
        op.name = item.name
        op.value = not item.startup

        op = right.operator(
            "plugin_manager.group_set_pinned",
            text="",
            icon="PINNED" if item.pinned else "UNPINNED",
            emboss=False,
        )
        op.name = item.name
        op.value = not item.pinned

        op = right.operator(
            "plugin_manager.group_solo",
            text="",
            icon="PLAY",
            emboss=False,
        )
        op.name = item.name
        op.keep_pinned = True


class PM_UL_plugins(UIList):
    bl_idname = "PM_UL_plugins"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        row = layout.row(align=True)

        # 仅在开启「批量选择」模式时显示左侧复选框，把日常宽度留给插件名称
        if getattr(data, "batch_mode", False):
            op = row.operator(
                "plugin_manager.toggle_select",
                text="",
                icon="CHECKBOX_HLT" if item.selected else "CHECKBOX_DEHLT",
                emboss=False,
            )
            op.key = item.key

        op = row.operator(
            "plugin_manager.toggle",
            text="",
            icon="PLAY" if item.enabled else "PAUSE",
            emboss=False,
        )
        op.key, op.enable = item.key, not item.enabled

        # 名称显示：有中文别名优先突出中文，收藏加星标
        title = item.display_name or item.name or item.key
        name_icon = "SOLO_ON" if item.favorite else ("BLENDER" if item.kind == C.KIND_BUILTIN else "NONE")
        row.label(text=title, icon=name_icon)

        label, _, warning = _compat_display(item)
        st_icon = "ERROR" if warning else "NONE"
        if item.missing:
            label, st_icon, warning = "文件缺失", "ERROR", True
        elif item.last_error:
            label, st_icon, warning = "启用失败", "ERROR", True
        elif item.kind == C.KIND_BUILTIN and not warning:
            label, st_icon = "内置", "NONE"
        elif item.update_available and not warning:
            label, st_icon = "有更新", "FILE_REFRESH"
        elif label == "实测可启动":
            label, st_icon = "", "CHECKMARK"
        elif label in ("声明兼容 · 未测", "未测"):
            label, st_icon = "", "NONE"

        if label or st_icon != "NONE":
            status = row.row(align=True)
            status.alignment = "RIGHT"
            status.alert = warning
            status.label(text=label, icon=st_icon)


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
# 菜单
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
        layout.operator("plugin_manager.refresh", icon="FILE_REFRESH", text="刷新插件库")
        layout.operator("plugin_manager.pick_library_path", icon="FILEBROWSER", text="切换插件库…")


class PM_MT_category(Menu):
    """分组筛选与管理菜单。"""

    bl_idname = "PM_MT_category"
    bl_label = "分组管理"

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
                text=f"{name}  ({counts.get(name, 0)})",
                icon=icon,
            )
            op.category = name
        layout.separator()
        layout.operator("plugin_manager.add_category", icon="ADD", text="新建分组…")
        if prefs.active_category not in ("", "全部", C.DEFAULT_CATEGORY):
            op = layout.operator("plugin_manager.rename_category", icon="SORTALPHA",
                                 text=f"重命名「{prefs.active_category}」…")
            op.old = prefs.active_category
            op = layout.operator("plugin_manager.delete_category", icon="TRASH",
                                 text=f"删除「{prefs.active_category}」")
            op.name = prefs.active_category
            layout.separator()
            layout.operator("plugin_manager.enable_pack", icon="CHECKBOX_HLT",
                            text="开启当前分组全部")
            layout.operator("plugin_manager.disable_pack", icon="CHECKBOX_DEHLT",
                            text="关闭当前分组全部")
        layout.separator()
        layout.operator("plugin_manager.group_deactivate_all", icon="CANCEL",
                        text="关闭全部工作分组")
        layout.operator("plugin_manager.reset_auto_categories", icon="LOOP_BACK",
                        text="清理自动分类")


class PM_MT_plugin_groups(Menu):
    """为选中插件多选勾选所属分组。"""

    bl_idname = "PM_MT_plugin_groups"
    bl_label = "所属分组"

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs or not prefs.selected_key:
            return
        from .db import LibraryDB

        db = LibraryDB(prefs.library_path) if prefs.library_path else None
        if not db:
            return
        current = set(db.plugin_groups(prefs.selected_key))
        group_names = db.group_names()
        if not group_names:
            layout.label(text="还没有工作分组", icon="INFO")
        else:
            for name in group_names:
                op = layout.operator(
                    "plugin_manager.plugin_toggle_group",
                    text=name,
                    icon="CHECKBOX_HLT" if name in current else "CHECKBOX_DEHLT",
                )
                op.key = prefs.selected_key
                op.group = name
        layout.separator()
        layout.operator("plugin_manager.add_category", icon="ADD", text="新建分组…")


class PM_MT_plugin(Menu):
    """单个插件的次级操作。"""

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
                             text="设置中文显示名…")
        op.key = key
        if rec and (rec.get("display_name") or "").strip():
            op = layout.operator("plugin_manager.rename_display", icon="X",
                                 text="清除中文显示名")
            op.key = key
            op.clear = True

        layout.separator()
        op = layout.operator("plugin_manager.assign_category", icon="FILE_FOLDER",
                             text="设置单一主分组…")
        op.key = key
        if rec:
            auto = (rec.get("auto_category") or "").strip()
            if auto and auto != C.DEFAULT_CATEGORY and auto != rec.get("category"):
                op = layout.operator("plugin_manager.set_category", icon="IMPORT",
                                     text=f"采用自带分类「{auto}」")
                op.key = key
                op.category = auto

        layout.separator()
        layout.operator("plugin_manager.edit_meta", icon="GREASEPENCIL", text="编辑备注与标签…")
        if rec and rec.get("kind") == C.KIND_ADDON and not rec.get("missing"):
            layout.operator("plugin_manager.relink_addon", icon="LINKED").key = key
        op = layout.operator("plugin_manager.open_folder", icon="FILE_FOLDER",
                             text="打开所在文件夹")
        op.key = key


# ---------------------------------------------------------------------------
# 主面板：日常工作台
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

        if prefs.library_job_status:
            layout.label(
                text=prefs.library_job_status,
                icon="TIME" if prefs.library_job_running else "ERROR" if prefs.library_job_failed else "INFO",
            )
        if prefs.library_job_running:
            return

        items.maybe_rebuild(prefs)
        if not PM_PT_main.draw_library_status(layout, prefs):
            return

        # --- 1. 工作分组区（含默认「全部插件」行；多组叠加 / 自启 / 独占切换） ---
        group_box = layout.box()
        hdr = group_box.row(align=True)
        active_groups = sum(1 for g in prefs.group_items if not getattr(g, "is_all", False) and g.active)
        hdr.label(text=f"工作分组（已开 {active_groups}）", icon="OUTLINER_COLLECTION")
        hdr.operator("plugin_manager.add_category", text="", icon="ADD")
        hdr.operator("plugin_manager.group_deactivate_all", text="", icon="X")
        hdr.menu("PM_MT_category", text="", icon="DOWNARROW_HLT")

        if len(prefs.group_items):
            rows = min(max(3, len(prefs.group_items)), 6)
            group_box.template_list(
                "PM_UL_groups", "", prefs, "group_items", prefs, "active_group_index",
                rows=rows, sort_lock=True,
            )
            if len(prefs.group_items) <= 1:
                group_box.label(text="点右上角「+」新建分组即可一键启停工作流", icon="INFO")
        else:
            row = group_box.row(align=True)
            row.label(text="暂无分组，新建后可一键启停工作流", icon="INFO")

        # --- 2. 插件搜索与列表区 ---
        search_row = layout.row(align=True)
        search_row.prop(prefs, "search", text="", icon="VIEWZOOM")
        search_row.prop(prefs, "kind_filter", text="")

        filters = layout.row(align=True)
        filters.prop(prefs, "only_enabled", text="已启用", toggle=True)
        filters.prop(prefs, "only_favorites", text="收藏", toggle=True)
        filters.prop(prefs, "only_updates", text="更新", toggle=True)
        filters.prop(prefs, "only_incompatible", text="问题", toggle=True)
        filters.prop(prefs, "batch_mode", text="批量", toggle=True)

        if prefs.active_category == C.DEFAULT_CATEGORY:
            chip = layout.row(align=True)
            chip.label(text=f"当前筛选：{C.DEFAULT_CATEGORY}", icon="FILTER")
            op = chip.operator("plugin_manager.pick_category", text="回到全部", icon="X")
            op.category = "全部"

        # 批量模式开启时，直接内联展示针对「当前筛选结果」的批量操作栏
        if prefs.batch_mode:
            vis_n = len(items.selected_keys(prefs))
            batch_box = layout.box()
            brow = batch_box.row(align=True)
            brow.label(text=f"当前筛选已选 {vis_n}/{len(prefs.plugin_items)}", icon="CHECKBOX_HLT")
            brow.operator("plugin_manager.select_all", text="全选当前").value = True
            brow.operator("plugin_manager.select_all", text="清空").value = False
            act = batch_box.row(align=True)
            act.enabled = bool(vis_n)
            act.operator("plugin_manager.batch_enable", text="启用", icon="CHECKBOX_HLT").value = True
            act.operator("plugin_manager.batch_enable", text="停用", icon="CHECKBOX_DEHLT").value = False
            act.operator("plugin_manager.batch_add_group", text="分组…", icon="OUTLINER_COLLECTION")
            act.operator("plugin_manager.batch_set_startup", text="自启", icon="RADIOBUT_ON").value = True

        if len(prefs.plugin_items):
            layout.template_list(
                "PM_UL_plugins", "", prefs, "plugin_items", prefs, "active_index",
                rows=8, sort_lock=True,
            )
            enabled_cnt = sum(1 for it in prefs.plugin_items if it.enabled)
            builtin_cnt = sum(1 for it in prefs.plugin_items if it.kind == C.KIND_BUILTIN)
            missing_cnt = sum(1 for it in prefs.plugin_items if it.missing)
            lib_cnt = len(prefs.plugin_items) - builtin_cnt - missing_cnt
            yes, no, _ = _support_counts(prefs)
            summary = f"库内 {lib_cnt} · 已启 {enabled_cnt}"
            if builtin_cnt:
                summary += f" · 内置链接 {builtin_cnt}"
            if missing_cnt:
                summary += f" · 缺失 {missing_cnt}"
            if no:
                summary += f" · 问题 {no}"
            elif yes:
                summary += f" · 可启动 {yes}"
            layout.label(text=summary)
        else:
            filtered = (
                prefs.search or prefs.active_category not in ("", "全部")
                or prefs.kind_filter != "all"
                or any((prefs.only_enabled, prefs.only_updates, prefs.only_favorites,
                        prefs.only_incompatible))
            )
            layout.label(
                text="没有匹配的插件，请调整筛选条件" if filtered else "库内还没有插件，可在下方「导入与商店」添加",
                icon="INFO",
            )

        # 仅在后台任务运行中于主面板展示进度条，平时不占空间
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
    """选中插件的详情与快速操作（默认展开）。"""

    bl_idname = "PM_PT_detail"
    bl_label = "插件详情"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_parent_id = "PM_PT_main"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        sel = _selected(prefs) if prefs else None
        if not sel:
            layout.label(text="在上方列表中点击一个插件查看详情", icon="INFO")
            return

        col = layout.column(align=True)
        alias = (sel.get("display_name") or "").strip()
        actual = sel.get("name") or sel.get("folder_name", "")
        is_builtin = sel.get("kind") == C.KIND_BUILTIN

        # 标题行 + 一键改中文名（直接同步原生界面）
        row = col.row(align=True)
        row.label(text=alias or actual, icon="BLENDER" if is_builtin else "PLUGIN")
        op = row.operator("plugin_manager.rename_display", text="中文名", icon="SORTALPHA")
        op.key = sel.get("key", "")

        if alias and alias != actual:
            sub = col.row()
            sub.enabled = False
            sub.label(text=f"原名：{actual}")

        # 类型与版本一行
        kind_text = "内置插件（仅链接，不入库）" if is_builtin else C.KIND_LABELS.get(sel.get("kind"), "")
        bits = [kind_text]
        if sel.get("version"):
            bits.append(f"v{sel['version']}")
        col.label(text=" · ".join(b for b in bits if b))

        # 所属分组（多对多）
        groups = sel.get("groups") or []
        group_row = col.row(align=True)
        group_row.label(text="分组：" + (" / ".join(groups) if groups else "未分组"), icon="OUTLINER_COLLECTION")
        group_row.menu("PM_MT_plugin_groups", text="分配…", icon="DOWNARROW_HLT")

        # 兼容与异常提示（紧凑显示）
        from . import scan as _scan

        bmin = sel.get("blender_min", "")
        bmax = sel.get("blender_max", "")
        state, detail = _scan.blender_compat(bmin, bmax)
        hi = _scan.max_version_label(bmax) if bmax else "不限"
        lo = _scan.version_str(bmin) or "不限"
        if not is_builtin:
            sub = col.row()
            if state in ("too_new", "too_old"):
                sub.alert = True
                sub.label(text=f"版本不兼容（{lo} ~ {hi} · {detail}）", icon="ERROR")
            elif sel.get("load_state") == "ok":
                sub.label(text=f"已实测可启动（支持 {lo} ~ {hi}）", icon="CHECKMARK")
            elif sel.get("load_state") != "failed":
                sub.enabled = False
                sub.label(text=f"声明版本：{lo} ~ {hi}")

        if sel.get("load_state") == "failed":
            err_row = col.row()
            err_row.alert = True
            err_row.label(text=f"启动失败: {(sel.get('load_error') or '')[:60]}", icon="CANCEL")
            if int(sel.get("residue") or 0) > 0:
                res_row = col.row(align=True)
                res_row.alert = True
                res_row.label(text=f"存在 {sel['residue']} 项注册残留", icon="ERROR")
                res_row.operator("plugin_manager.cleanup_residue", text="清理", icon="TRASH")

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

        col.separator()
        # 主操作栏
        row = col.row(align=True)
        op = row.operator(
            "plugin_manager.toggle",
            text="停用" if sel.get("enabled") else "启用",
            icon="CHECKBOX_DEHLT" if sel.get("enabled") else "CHECKBOX_HLT",
        )
        op.key = sel.get("key", "")
        op.enable = not sel.get("enabled")

        op = row.operator(
            "plugin_manager.set_startup",
            text="取消自启" if sel.get("startup") else "自启",
            icon="RADIOBUT_ON" if sel.get("startup") else "RADIOBUT_OFF",
        )
        op.key = sel.get("key", "")
        op.value = not sel.get("startup")
        op.use_selection = False

        op = row.operator(
            "plugin_manager.set_favorite",
            text="",
            icon="SOLO_ON" if sel.get("favorite") else "SOLO_OFF",
        )
        op.key = sel.get("key", "")
        op.value = not sel.get("favorite")

        row = col.row(align=True)
        row.operator("plugin_manager.edit_meta", text="编辑信息", icon="GREASEPENCIL")
        row.menu("PM_MT_plugin", text="更多", icon="DOWNARROW_HLT")
        trash_sub = row.row(align=True)
        trash_sub.enabled = not is_builtin
        op = trash_sub.operator("plugin_manager.remove_plugin", text="", icon="TRASH")
        op.key = sel.get("key", "")


class PM_PT_batch(Panel):
    """批量操作（配合主面板「批量」开关使用）。"""

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
        if not prefs or prefs.library_job_running:
            return
        vis_n = len(items.selected_keys(prefs))
        top = layout.row(align=True)
        top.prop(prefs, "batch_mode", text="开启批量勾选", toggle=True)
        top.label(text=f"当前筛选已选 {vis_n}/{len(prefs.plugin_items)}")

        row = layout.row(align=True)
        row.operator("plugin_manager.select_all", text="全选当前结果").value = True
        row.operator("plugin_manager.select_all", text="清空勾选").value = False

        actions = layout.column(align=True)
        actions.enabled = bool(vis_n)
        row = actions.row(align=True)
        row.operator("plugin_manager.batch_enable", text="批量启用", icon="CHECKBOX_HLT").value = True
        row.operator("plugin_manager.batch_enable", text="批量停用", icon="CHECKBOX_DEHLT").value = False
        row = actions.row(align=True)
        row.operator("plugin_manager.batch_add_group", text="加入/移出分组…", icon="OUTLINER_COLLECTION")
        row.operator("plugin_manager.batch_set_category", text="设为单一分组…", icon="FILE_FOLDER")
        row = actions.row(align=True)
        row.operator("plugin_manager.batch_set_startup", text="设为自启",
                     icon="RADIOBUT_ON").value = True
        row.operator("plugin_manager.batch_set_startup", text="取消自启",
                     icon="RADIOBUT_OFF").value = False


class PM_PT_actions(Panel):
    """导入、在线商店、兼容性测试与扫描报告（集中收纳低频/中频工具）。"""

    bl_idname = "PM_PT_actions"
    bl_label = "导入、商店与维护"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "插件库"
    bl_parent_id = "PM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs or prefs.library_job_running:
            return

        # 1) 本地导入（唯一入口，避免主面板重复）
        col = layout.column(align=True)
        col.label(text="本地导入", icon="IMPORT")
        row = col.row(align=True)
        row.operator("plugin_manager.import_zip", icon="FILE_ARCHIVE", text="导入 ZIP")
        row.operator("plugin_manager.import_plugin", icon="FILE_FOLDER", text="导入文件夹")
        row.operator("plugin_manager.scan_inbox", icon="FILE_REFRESH", text="投放区")

        layout.separator()
        # 2) 在线商店与更新
        col = layout.column(align=True)
        col.label(text="在线商店 (extensions.blender.org)", icon="URL")
        row = col.row(align=True)
        row.operator("plugin_manager.store_open", icon="URL", text="浏览/安装")
        row.operator("plugin_manager.check_updates", icon="FILE_REFRESH", text="检查更新")
        row = col.row(align=True)
        row.operator("plugin_manager.update_all", icon="TRIA_DOWN_BAR", text="批量更新")
        row.operator("plugin_manager.store_sync", icon="FILE_REFRESH", text="")
        row.operator("plugin_manager.restore_plugin", icon="LOOP_BACK", text="回收站恢复")
        if prefs.archive_retry:
            col.operator("plugin_manager.retry_archives", text="重试失败项", icon="FILE_REFRESH")

        layout.separator()
        # 3) 兼容性实测与扫描报告
        col = layout.column(align=True)
        col.label(text="测试与报告", icon="TOOL_SETTINGS")
        if prefs.compat_running:
            col.operator("plugin_manager.cancel_compat", icon="X", text="取消启动测试")
        else:
            col.operator("plugin_manager.verify_compat", icon="CHECKMARK", text="测试插件能否启动…")
        row = col.row(align=True)
        row.operator("plugin_manager.show_report", icon="INFO", text="扫描报告")
        row.operator("plugin_manager.copy_report", icon="COPY_ID", text="")
        row.operator("plugin_manager.open_report_log", icon="TEXT", text="")
        if prefs.report_summary:
            col.label(text=prefs.report_summary)


# ---------------------------------------------------------------------------
def _support_counts(prefs) -> tuple[int, int, int]:
    """当前可见列表的支持统计：(✓支持, ✗不支持, ?未测试)。"""
    yes = sum(1 for it in prefs.plugin_items if it.supported == "yes")
    no = sum(1 for it in prefs.plugin_items if it.supported == "no")
    unk = sum(1 for it in prefs.plugin_items if it.supported not in ("yes", "no"))
    return yes, no, unk


def _startup_stats(prefs) -> dict:
    if not prefs.library_path or prefs.library_job_running:
        return {"total": 0, "startup": 0, "not_startup": 0}
    from .db import LibraryDB

    return LibraryDB(prefs.library_path).startup_stats()


def _selected(prefs):
    if not prefs or prefs.library_job_running or not prefs.library_path or not prefs.selected_key:
        return None
    from .db import LibraryDB

    return LibraryDB(prefs.library_path).get(prefs.selected_key)


classes = (
    PM_UL_groups,
    PM_UL_plugins,
    PM_UL_report,
    PM_MT_library,
    PM_MT_category,
    PM_MT_plugin_groups,
    PM_MT_plugin,
    PM_PT_main,
    PM_PT_detail,
    PM_PT_batch,
    PM_PT_actions,
)
