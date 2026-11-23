"""偏好设置与界面数据属性。"""

from __future__ import annotations

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, PropertyGroup

from . import bridge, constants as C, native_names

_LOADING_MACHINE_PATH = False
_SYNCING_GROUP_INDEX = False
_SYNCING_FILTERS = False


# ---------------------------------------------------------------------------
# 列表项 / 分组项 / 报告项
# ---------------------------------------------------------------------------
class PM_PluginItem(PropertyGroup):
    key: StringProperty(default="")
    name: StringProperty(default="")
    folder_name: StringProperty(default="")
    display_name: StringProperty(default="")
    kind: StringProperty(default="")
    version: StringProperty(default="")
    latest_version: StringProperty(default="")
    blender_min: StringProperty(default="")
    blender_max: StringProperty(default="")
    compat: StringProperty(default="")            # ok / too_old / too_new / unknown
    max_version_text: StringProperty(default="")  # 最高支持版本（实时计算）
    supported: StringProperty(default="")         # yes / no / unknown（仅真实实测后才为 yes）
    residue: IntProperty(default=0)               # 注册残留数量
    compat_text: StringProperty(default="")       # 支持版本范围文案（实时计算）
    compat_detail: StringProperty(default="")     # 不兼容时的具体说明
    load_state: StringProperty(default="")        # 实测结果: ok / failed / ""(未测)
    load_error: StringProperty(default="")
    author: StringProperty(default="")
    module: StringProperty(default="")
    note: StringProperty(default="")
    category: StringProperty(default="")
    groups_text: StringProperty(default="")       # 所属分类摘要
    presets_text: StringProperty(default="")      # 所属预设摘要
    is_temporary: BoolProperty(default=False)     # 本次会话临时启用
    enabled: BoolProperty(default=False)
    update_available: BoolProperty(default=False)
    missing: BoolProperty(default=False)
    favorite: BoolProperty(default=False)
    startup: BoolProperty(default=False)
    selected: BoolProperty(default=False)
    last_error: StringProperty(default="")
    restore_error: StringProperty(default="")


class PM_CategoryItem(PropertyGroup):
    name: StringProperty(default="")


class PM_GroupItem(PropertyGroup):
    """分类筛选列表项（含默认「全部插件」行，仅用于整理与筛选，不带开关）。"""

    name: StringProperty(default="")
    is_all: BoolProperty(default=False)
    active: BoolProperty(default=False)
    startup: BoolProperty(default=False)
    pinned: BoolProperty(default=False)
    total: IntProperty(default=0)
    enabled: IntProperty(default=0)


class PM_PresetItem(PropertyGroup):
    """工作预设列表项（状态由成员真实启用情况实时推导，绝不存过期开关）。"""

    name: StringProperty(default="")
    state: StringProperty(default="off")  # "all" | "partial" | "off"
    active: BoolProperty(default=False)
    startup: BoolProperty(default=False)
    pinned: BoolProperty(default=False)
    total: IntProperty(default=0)
    enabled: IntProperty(default=0)
    broken: IntProperty(default=0)


class PM_ReportItem(PropertyGroup):
    """一次扫描/导入的结果条目，用于在面板上展示明细与失败原因。"""

    name: StringProperty(default="")
    path: StringProperty(default="")
    kind: StringProperty(default="")     # addon / extension / zip / -
    status: StringProperty(default="")   # imported / skipped / failed
    detail: StringProperty(default="")
    active: BoolProperty(default=False)  # 是否展开查看详情


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------
def _on_index_update(self, context):
    keys = [it.key for it in self.plugin_items]
    if 0 <= self.active_index < len(keys):
        self.selected_key = keys[self.active_index]


def _on_filter_update(self, context):
    from . import items

    items.maybe_rebuild(self, force=True)


def _on_only_enabled_update(self, context):
    global _SYNCING_FILTERS
    if not _SYNCING_FILTERS:
        _SYNCING_FILTERS = True
        try:
            if self.only_enabled:
                self.enabled_filter = "enabled"
            elif self.enabled_filter == "enabled":
                self.enabled_filter = "all"
        finally:
            _SYNCING_FILTERS = False
    _on_filter_update(self, context)


def _on_enabled_filter_update(self, context):
    global _SYNCING_FILTERS
    if not _SYNCING_FILTERS:
        _SYNCING_FILTERS = True
        try:
            self.only_enabled = self.enabled_filter == "enabled"
        finally:
            _SYNCING_FILTERS = False
    _on_filter_update(self, context)


def _on_only_incompatible_update(self, context):
    global _SYNCING_FILTERS
    if not _SYNCING_FILTERS:
        _SYNCING_FILTERS = True
        try:
            if self.only_incompatible:
                self.issue_filter = "only"
            elif self.issue_filter == "only":
                self.issue_filter = "all"
        finally:
            _SYNCING_FILTERS = False
    _on_filter_update(self, context)


def _on_issue_filter_update(self, context):
    global _SYNCING_FILTERS
    if not _SYNCING_FILTERS:
        _SYNCING_FILTERS = True
        try:
            self.only_incompatible = self.issue_filter == "only"
        finally:
            _SYNCING_FILTERS = False
    _on_filter_update(self, context)


def _on_group_index_update(self, context):
    if _SYNCING_GROUP_INDEX:
        return
    if 0 <= self.active_group_index < len(self.group_items):
        item = self.group_items[self.active_group_index]
        target = "全部" if item.is_all else item.name
        if self.active_category != target:
            self.active_category = target


def _on_batch_mode_update(self, context):
    from . import items

    if not self.batch_mode:
        items.clear_selection()
        for it in self.plugin_items:
            it.selected = False
    else:
        items.prune_selection_to_visible(self)


def _on_native_names_update(self, context):
    native_names.invalidate_cache()
    try:
        if self.native_display_names:
            native_names.install()
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


def _on_builtin_toggle_update(self, context):
    if _LOADING_MACHINE_PATH or not self.library_path or self.library_job_running:
        return
    try:
        from .db import LibraryDB
        from . import items, library

        if os.path.isdir(self.library_path):
            db = LibraryDB(self.library_path)
            if db.status == "OK":
                library.sync_builtins(self.library_path, db,
                                      enabled=bool(self.manage_builtin_addons), persist=True)
                items.rebuild_items(self)
                native_names.invalidate_cache()
    except Exception as exc:
        print("[插件库] 切换内置插件链接状态失败:", exc)


def _on_library_path_update(self, context):
    """Persist only the machine-local path; activation owns scanning/mounting."""
    if _LOADING_MACHINE_PATH:
        return
    try:
        from .storage import machine_config
        local = machine_config.load()
        previous = local.get("library_path")
        changed = previous and os.path.normcase(os.path.abspath(previous)) != os.path.normcase(
            os.path.abspath(self.library_path or ""))
        if changed and local.get("management_enabled"):
            from . import scoped_management
            scoped_management.deactivate(previous)
        machine_config.save({"library_path": self.library_path or None,
                             "management_enabled": False if changed else local.get("management_enabled", False)})
    except Exception as exc:
        print("[插件库] 保存本机插件库路径失败:", exc)
    native_names.invalidate_cache()
    from . import items
    items.maybe_rebuild(self, force=True)


def _on_startup_mode_update(self, context):
    if _LOADING_MACHINE_PATH:
        return
    try:
        from .storage import machine_config

        machine_config.save({"startup_mode": self.startup_mode})
    except Exception as exc:
        print("[插件库] 保存启动模式失败:", exc)
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


def _on_device_name_update(self, context):
    if _LOADING_MACHINE_PATH or not self.device_name.strip():
        return
    try:
        from .storage import machine_config
        from .storage.device_profiles import DeviceProfileStore
        local = machine_config.initialize()
        machine_config.save({"device_name": self.device_name.strip()})
        root = local.get("library_path")
        if root and os.path.isdir(root):
            store = DeviceProfileStore(root)
            if store.exists(local["device_id"]):
                profile = store.load(local["device_id"])
                store.save(local["device_id"], self.device_name.strip(),
                           profile["enabled_plugins"],
                           repositories=profile["repositories"],
                           script_directories=profile["script_directories"])
    except Exception as exc:
        print("[插件库] 保存设备名称失败:", exc)


# ---------------------------------------------------------------------------
# 插件偏好
# ---------------------------------------------------------------------------
_KIND_FILTER_ITEMS = (
    ("all", "全部来源", "显示库内扩展、传统插件与已链接的 Blender 内置插件"),
    (C.KIND_EXTENSION, "扩展插件", "仅显示库内扩展插件"),
    (C.KIND_ADDON, "传统插件", "仅显示库内传统插件"),
    (C.KIND_BUILTIN, "内置插件", "仅显示虚拟链接管理的 Blender 自带基础插件"),
)

_ENABLED_FILTER_ITEMS = (
    ("all", "启停:全部", "同时显示已启用和未启用的插件"),
    ("enabled", "已启用", "仅显示当前已启用的插件"),
    ("disabled", "未启用", "仅显示当前未启用的插件"),
)

_STARTUP_FILTER_ITEMS = (
    ("all", "自启:全部", "同时显示已自启和未自启的插件"),
    ("startup", "已自启", "仅显示已标记自启或属于自启预设的插件"),
    ("not_startup", "未自启", "仅显示未标记自启且不在自启预设中的插件"),
)

_ISSUE_FILTER_ITEMS = (
    ("all", "问题:全部", "显示所有插件（含兼容性或启动问题）"),
    ("only", "仅问题", "仅显示启动实测失败或声明版本范围不兼容的插件"),
    ("hide", "隐藏问题", "隐藏启动失败、版本不兼容或文件缺失的插件"),
)


_STARTUP_MODE_ITEMS = (
    ("remember", "记住上次状态", "下次打开 Blender 自动恢复退出前启用的全部插件"),
    ("clean", "干净启动", "下次打开 Blender 仅启动标记为「自启」的预设与插件，临时开启的插件重启后自动恢复清爽"),
)


class PMAddonPreferences(AddonPreferences):
    bl_idname = C.ADDON_ID

    library_path: StringProperty(
        name="插件库路径",
        description="所有插件集中存放的根目录；内部会自动建立 addons / extensions / inbox",
        subtype="DIR_PATH",
        default="",
        options={"SKIP_SAVE"},
        update=_on_library_path_update,
    )
    pending_library_path: StringProperty(name="待连接插件库", subtype="DIR_PATH",
                                         options={"SKIP_SAVE"})
    library_preview: StringProperty(options={"SKIP_SAVE"})
    library_job_running: BoolProperty(options={"SKIP_SAVE"})
    library_job_failed: BoolProperty(options={"SKIP_SAVE"})
    library_job_status: StringProperty(options={"SKIP_SAVE"})
    self_update_busy: BoolProperty(options={"SKIP_SAVE"})
    self_update_status: StringProperty(options={"SKIP_SAVE"})
    device_name: StringProperty(
        name="设备名称",
        description="此电脑在同步插件库中的显示名称；启用插件清单按设备分别保存",
        default="",
        options={"SKIP_SAVE"},
        update=_on_device_name_update,
    )

    # 界面状态与筛选
    selected_key: StringProperty(default="")
    active_index: IntProperty(default=0, update=_on_index_update)
    active_group_index: IntProperty(default=0, update=_on_group_index_update)
    active_preset_index: IntProperty(default=0)
    active_preset_filter: StringProperty(default="", options={"SKIP_SAVE"}, update=_on_filter_update)
    manual_prompt_key: StringProperty(default="", options={"SKIP_SAVE"})
    manual_prompt_name: StringProperty(default="", options={"SKIP_SAVE"})
    active_category: StringProperty(default="全部", update=_on_filter_update)
    kind_filter: EnumProperty(
        name="来源",
        items=_KIND_FILTER_ITEMS,
        default="all",
        update=_on_filter_update,
    )
    enabled_filter: EnumProperty(
        name="启用筛选",
        items=_ENABLED_FILTER_ITEMS,
        default="all",
        update=_on_enabled_filter_update,
    )
    startup_filter: EnumProperty(
        name="自启筛选",
        items=_STARTUP_FILTER_ITEMS,
        default="all",
        update=_on_filter_update,
    )
    issue_filter: EnumProperty(
        name="问题筛选",
        items=_ISSUE_FILTER_ITEMS,
        default="all",
        update=_on_issue_filter_update,
    )
    batch_mode: BoolProperty(
        name="批量选择",
        description="对当前筛选结果开启批量勾选与操作栏（关闭时清空勾选）",
        default=False,
        update=_on_batch_mode_update,
    )
    only_favorites: BoolProperty(default=False, update=_on_filter_update)
    only_updates: BoolProperty(default=False, update=_on_filter_update)
    only_enabled: BoolProperty(default=False, update=_on_only_enabled_update)
    only_incompatible: BoolProperty(
        name="兼容性问题",
        description="显示启动实测失败或声明版本范围不符合当前 Blender 的插件",
        default=False, update=_on_only_incompatible_update)
    search: StringProperty(default="", update=_on_filter_update)
    candidate_count: IntProperty(default=0)

    # 原生界面名称覆写 & 内置插件链接
    native_display_names: BoolProperty(
        name="在 Blender 原生界面显示中文别名",
        description="运行时在「偏好设置 → 插件」和「获取扩展」显示中文别名，不修改任何插件源码",
        default=True,
        update=_on_native_names_update,
    )
    native_name_style: EnumProperty(
        name="原生名称格式",
        items=native_names.STYLE_ITEMS,
        default=native_names.STYLE_ORIG_CN,
        update=_on_native_names_update,
    )
    manage_builtin_addons: BoolProperty(
        name="虚拟链接管理 Blender 内置插件",
        description="将 Blender 自带基础插件（如 Node Wrangler、Rigify）以只读链接方式纳入分组与中文名管理，不复制文件进库",
        default=True,
        update=_on_builtin_toggle_update,
    )

    # 兼容性测试任务运行时状态（仅本会话，不写入偏好文件）
    compat_running: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_cancelling: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_total: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_done: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_ok: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_fail: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_current: StringProperty(default="", options={"SKIP_SAVE"})
    archive_running: BoolProperty(options={"SKIP_SAVE"})
    archive_cancelling: BoolProperty(options={"SKIP_SAVE"})
    archive_retry: BoolProperty(options={"SKIP_SAVE"})
    archive_status: StringProperty(options={"SKIP_SAVE"})
    archive_progress: FloatProperty(min=0.0, max=1.0, options={"SKIP_SAVE"})

    # 本次扫描/导入的结果明细（面板展示）
    report_items: CollectionProperty(type=PM_ReportItem)
    report_summary: StringProperty(default="")
    report_index: IntProperty(default=0)

    # 在线商店
    store_search: StringProperty(name="搜索", default="", description="按名称/描述搜索商店")
    store_page: IntProperty(name="页码", default=1, min=1, options={"SKIP_SAVE"})
    store_only_new: BoolProperty(name="仅未安装", default=True,
                                 description="只显示尚未安装到插件库的扩展")
    store_only_compatible: BoolProperty(name="仅兼容版本", default=True,
                                        description="只显示与当前 Blender 版本兼容的扩展")

    # 启动控制（二选一明确策略；实际持久化到本机 machine.json）
    startup_mode: EnumProperty(
        name="启动模式",
        description="决定下次打开 Blender 时如何恢复插件启用状态",
        items=_STARTUP_MODE_ITEMS,
        default="remember",
        options={"SKIP_SAVE"},
        update=_on_startup_mode_update,
    )
    # 兼容旧版脚本/测试的布尔字段（不再单独触发二次冲突覆盖）
    sync_startup_on_launch: BoolProperty(
        name="启动时同步自启预设与插件",
        description="兼容选项：推荐直接使用上方「启动模式」选择记住上次或干净启动",
        default=False,
    )
    startup_disable_unmarked: BoolProperty(
        name="同时停用未标记自启的库内插件",
        description="兼容选项：干净启动模式默认自动停用未标记自启的库内插件（不强关内置插件）",
        default=False,
    )

    plugin_items: CollectionProperty(type=PM_PluginItem)
    category_items: CollectionProperty(type=PM_CategoryItem)
    group_items: CollectionProperty(type=PM_GroupItem)
    preset_items: CollectionProperty(type=PM_PresetItem)

    def draw(self, context):
        layout = self.layout
        from . import _update_boot_message, self_update
        from .storage import machine_config

        if self.library_job_status:
            layout.label(
                text=self.library_job_status,
                icon="TIME" if self.library_job_running else "ERROR" if self.library_job_failed else "INFO",
            )
        if self.library_job_running:
            return

        # --- 1. 插件库与设备 ---
        box = layout.box()
        box.label(text="1. 插件库与设备", icon="FILE_FOLDER")
        col = box.column(align=False)
        row = col.row(align=True)
        row.prop(self, "device_name")
        device_id = machine_config.load()["device_id"]
        sub = row.row()
        sub.enabled = False
        sub.label(text=f"ID: {device_id[:10]}" if device_id else "ID 暂不可用")

        if self.library_path:
            col.label(text=self.library_path, icon="FILE_FOLDER")
        else:
            col.label(text="尚未选择插件库目录", icon="ERROR")
        if self.pending_library_path:
            col.label(text=f"待连接: {self.pending_library_path}", icon="INFO")
            if self.library_preview:
                col.label(text=self.library_preview)

        row = col.row(align=True)
        row.operator("plugin_manager.pick_library_path", text="选择插件库…", icon="FILEBROWSER")
        row.operator("plugin_manager.setup_library", text="启用管理", icon="LINKED")
        row.operator("plugin_manager.unmount_library", text="停止管理", icon="UNLINKED")

        if self.library_path:
            state = bridge.library_state(self.library_path)
            status_row = box.row(align=True)
            status_row.label(
                text=f"脚本目录: {'已挂载' if state['script_dir'] else '未挂载'}",
                icon="CHECKMARK" if state["script_dir"] else "ERROR",
            )
            status_row.label(
                text=f"扩展仓库: {'已挂载' if state['repo'] else '未挂载'}",
                icon="CHECKMARK" if state["repo"] else "ERROR",
            )

        # --- 2. 显示与集成 ---
        box = layout.box()
        box.label(text="2. 显示、内置插件与启动策略", icon="PREFERENCES")
        box.prop(self, "native_display_names")
        row = box.row()
        row.enabled = self.native_display_names
        row.prop(self, "native_name_style")
        nst = native_names.status()
        if self.native_display_names:
            if nst["installed"]:
                scope = []
                if nst["addons_hooked"]:
                    scope.append("插件列表")
                if nst["extensions_hooked"]:
                    scope.append("获取扩展")
                box.label(
                    text=f"原生双语显示已生效（{' + '.join(scope)}，已映射 {nst['mapped_count']} 项，不改源码）",
                    icon="CHECKMARK",
                )
            elif nst["error"]:
                box.label(text=f"原生显示降级：{nst['error']}", icon="INFO")

        box.separator()
        box.prop(self, "manage_builtin_addons")

        box.separator()
        row = box.row(align=True)
        row.label(text="启动模式：", icon="TIME")
        row.prop(self, "startup_mode", expand=True)
        if self.startup_mode == "clean":
            startup_presets = sum(1 for p in self.preset_items if p.startup)
            box.label(
                text=f"下次打开 Blender 仅启动自启预设（{startup_presets} 个）与自启插件，临时启用的插件重启后自动归零",
                icon="INFO",
            )
            op = box.operator("plugin_manager.apply_startup", icon="PLAY", text="立即按干净启动清单重置")
            op.disable_unmarked = True
        else:
            box.label(
                text="下次打开 Blender 自动恢复退出前启用的全部插件（与 Blender 原生习惯一致）",
                icon="INFO",
            )
            op = box.operator("plugin_manager.apply_startup", icon="PLAY", text="手动应用自启预设与插件…")
            op.disable_unmarked = False

        # --- 3. 外部收编与官方商店 ---
        box = layout.box()
        box.label(text="3. 外部收编与官方商店", icon="IMPORT")
        row = box.row(align=True)
        row.operator("plugin_manager.scan_candidates", icon="VIEWZOOM", text="扫描外部插件")
        row.operator("plugin_manager.import_candidates", icon="IMPORT", text="收编进库（自动避开内置冲突）")
        if self.candidate_count:
            box.label(text=f"检测到外部插件 {self.candidate_count} 个", icon="INFO")

        try:
            official = bool(bridge.library_state(self.library_path).get("official")) if self.library_path else False
        except Exception:
            official = False
        if official:
            box.label(text="官方商店（extensions.blender.org）已与本库共用目录", icon="CHECKMARK")
        else:
            box.operator("plugin_manager.unify_store", icon="LINKED",
                         text="让官方商店使用本插件库目录")

        # --- 4. 维护与管理器更新 ---
        box = layout.box()
        box.label(text=f"4. 维护与更新（当前 v{C.ADDON_VERSION_STR}）", icon="TOOL_SETTINGS")
        row = box.row(align=True)
        row.operator("plugin_manager.refresh", icon="FILE_REFRESH", text="刷新列表")
        row.operator("plugin_manager.show_warnings", icon="INFO", text="诊断信息")
        row.operator("plugin_manager.cleanup_residue", icon="TRASH", text="清理残留")
        row.operator("plugin_manager.clear_updates", icon="X", text="清除更新标记")

        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = not self.self_update_busy
        sub.operator("plugin_manager.github_update", icon="FILE_REFRESH", text="检查 GitHub 更新")
        op = row.operator("wm.url_open", text="查看发布页", icon="URL")
        op.url = self_update.RELEASES_URL
        if message := self.self_update_status or _update_boot_message:
            box.label(text=message, icon="INFO")


classes = (
    PM_PluginItem,
    PM_CategoryItem,
    PM_GroupItem,
    PM_PresetItem,
    PM_ReportItem,
    PMAddonPreferences,
)
