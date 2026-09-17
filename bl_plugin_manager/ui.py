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
    compat = getattr(item, "compat", "unknown")
    load_state = getattr(item, "load_state", "")
    alert = load_state == "failed" or compat in ("too_old", "too_new")
    if load_state == "failed":
        label = "✗ 启动测试失败"
    elif compat in ("too_old", "too_new"):
        label = "✗ 当前 Blender 不兼容"
    elif load_state == "ok":
        label = "✓ 已实测可启动"
    elif compat == "ok":
        label = "? 声明兼容，未实测"
    else:
        label = "? 未声明，未实测"

    maximum = getattr(item, "max_version_text", "") or "不限"
    if maximum == "不限":
        maximum = "最高 不限"
    elif not maximum.startswith("最高"):
        maximum = f"最高 {maximum}"
    return label, maximum, alert


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

        # 启用开关（会话内是否加载）
        op = row.operator("plugin_manager.toggle", text="",
                          icon="CHECKBOX_HLT" if item.enabled else "CHECKBOX_DEHLT",
                          emboss=False)
        op.key = item.key
        op.enable = not item.enabled

        # 自启标记（随 Blender 启动是否自动启用）
        op = row.operator("plugin_manager.set_startup", text="",
                          icon="RADIOBUT_ON" if item.startup else "RADIOBUT_OFF",
                          emboss=False)
        op.key = item.key
        op.value = not item.startup
        op.use_selection = False

        # 状态：实测失败 / 版本不兼容 优先用醒目图标
        if item.missing:
            row.label(text="", icon="ERROR")
        elif item.load_state == "failed":
            row.label(text="", icon="CANCEL")
        elif item.compat in ("too_new", "too_old"):
            row.label(text="", icon="CANCEL")
        elif item.last_error:
            row.label(text="", icon="CANCEL")
        elif item.update_available:
            row.label(text="", icon="FILE_REFRESH")
        else:
            row.label(text="", icon="PLUGIN")

        # 名称：显示名称（别名）为主，实际名以灰色附后
        actual = item.name or item.folder_name or item.key
        shown = item.display_name or actual
        if item.favorite:
            shown = "★ " + shown
        row.label(text=shown)
        if item.display_name and item.display_name != actual:
            alias = row.row()
            alias.enabled = False
            alias.label(text=f"（{actual}）")

        # 右侧：显示当前 Blender 兼容性和插件声明的最高支持版本；
        # 插件自身版本保留在详情面板，不在列表行重复显示。
        compat_label, max_label, compat_alert = _compat_display(item)
        sub = row.column(align=True)
        sub.alignment = "RIGHT"
        sub.alert = compat_alert
        sub.label(text=compat_label)
        max_row = sub.row(align=True)
        max_row.alignment = "RIGHT"
        max_row.enabled = False
        max_row.label(text=max_label)


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
        layout.operator("plugin_manager.setup_library", icon="LINKED", text="启用/修复挂载")
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

        # --- 库状态（一行紧凑显示，详情收进菜单）---
        box = layout.box()
        row = box.row(align=True)
        if prefs.library_path:
            state = bridge.library_state(prefs.library_path)
            try:
                from .storage import machine_config
                local = machine_config.load()
                managed = bool(local.get("management_enabled"))
            except Exception:
                local, managed = {}, False
            mounted = managed and state["script_dir"] and state["repo"]
            name = os.path.basename(prefs.library_path.rstrip("\\/")) or prefs.library_path
            row.label(text=name, icon="CHECKMARK" if mounted else "ERROR")
            row.operator("plugin_manager.pick_library_path", text="", icon="FILEBROWSER")
            row.operator("plugin_manager.setup_library", text="", icon="LINKED")
            row.menu("PM_MT_library", text="", icon="DOWNARROW_HLT")
            device_row = box.row(align=True)
            device_row.prop(prefs, "device_name", text="设备")
            if local.get("device_id"):
                device_row.label(text=local["device_id"][:8])
        else:
            row.label(text="未设置插件库位置", icon="ERROR")
            row.operator("plugin_manager.pick_library_path", text="选择目录",
                         icon="FILEBROWSER")
            row.menu("PM_MT_library", text="", icon="DOWNARROW_HLT")

        # 当前 Blender 版本（兼容范围以此为参照）
        try:
            cur = ".".join(str(v) for v in bpy.app.version)
        except Exception:
            cur = ""
        if cur:
            row = layout.row(align=True)
            sub = row.row()
            sub.enabled = False
            sub.label(text=f"当前 Blender {cur}", icon="BLENDER")
            yes_n, no_n, unk_n = _support_counts(prefs)
            sub2 = row.row(align=True)
            sub2.alignment = "RIGHT"
            if no_n:
                sub2.alert = True
                sub2.label(text=f"✗{no_n}", icon="CANCEL")
            if yes_n:
                sub2.label(text=f"✓{yes_n}")
            if unk_n and not (yes_n or no_n):
                grey = sub2.row()
                grey.enabled = False
                grey.label(text=f"?{unk_n}")
            # 一键测试（显眼入口）；任务运行时改为进度与取消
            row = layout.row(align=True)
            if prefs.compat_running:
                row.operator("plugin_manager.cancel_compat", icon="X", text="取消测试")
            else:
                row.operator("plugin_manager.verify_compat", icon="CHECKMARK",
                             text="一键测试插件支持")

        if prefs.compat_running:
            box = layout.box()
            box.label(text=f"测试进度 {prefs.compat_done} / {prefs.compat_total}",
                      icon="TIME")
            if prefs.compat_current:
                box.label(text=f"当前: {prefs.compat_current}")
            _progress_bar(box, prefs.compat_done / max(1, prefs.compat_total),
                          f"{prefs.compat_done}/{prefs.compat_total}")
            sub = box.row(align=True)
            sub.label(text=f"✓ {prefs.compat_ok}　✗ {prefs.compat_fail}")
            sub.operator("plugin_manager.cancel_compat", icon="X", text="取消")
            if prefs.compat_cancelling:
                box.label(text="正在取消，等待当前插件处理结束…", icon="INFO")

        # --- 搜索 + 过滤（一行）---
        row = layout.row(align=True)
        row.prop(prefs, "search", text="", icon="VIEWZOOM")
        row.prop(prefs, "only_enabled", text="", icon="CHECKBOX_HLT")
        row.prop(prefs, "only_updates", text="", icon="FILE_REFRESH")
        row.prop(prefs, "only_favorites", text="", icon="SOLO_ON")
        row.prop(prefs, "only_incompatible", text="", icon="ERROR")

        # --- 插件列表 ---
        layout.template_list(
            "PM_UL_plugins", "", prefs, "plugin_items", prefs, "active_index",
            rows=9, sort_lock=True,
        )

        # --- 计数 + 分类入口（菜单，按需展开）---
        row = layout.row(align=True)
        row.label(text=f"{len(prefs.plugin_items)} 个插件")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.menu("PM_MT_category",
                 text=f"分类: {prefs.active_category}", icon="FILE_FOLDER")


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
        hi = _scan.max_version_label(bmax)          # 最高支持版本
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
            col.label(text="✓ 支持当前 Blender", icon="CHECKMARK")

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
        op = row.operator("plugin_manager.set_favorite", text="收藏", icon="SOLO_ON")
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
        n = sum(1 for it in prefs.plugin_items if it.selected)
        row = layout.row(align=True)
        row.label(text=f"已勾选 {n} 个", icon="PRESET")
        row.operator("plugin_manager.select_all", text="全选", icon="CHECKMARK").value = True
        row.operator("plugin_manager.select_all", text="清空", icon="X").value = False

        row = layout.row(align=True)
        row.operator("plugin_manager.batch_enable", text="启用", icon="CHECKBOX_HLT").value = True
        row.operator("plugin_manager.batch_enable", text="停用", icon="CHECKBOX_DEHLT").value = False
        row.operator("plugin_manager.batch_set_category", text="归入分类…", icon="FILE_FOLDER")
        row = layout.row(align=True)
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
        row.operator("plugin_manager.update_all", icon="TRIA_DOWN_BAR", text="一键更新全部")
        row.operator("plugin_manager.store_sync", icon="FILE_REFRESH", text="")

        st = bridge.library_state(prefs.library_path) if prefs.library_path else {}
        if st.get("official"):
            col.label(text="官方商店面板已与本库共用目录", icon="CHECKMARK")
        else:
            col.operator("plugin_manager.unify_store", icon="LINKED",
                         text="让官方商店使用本插件库")

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
