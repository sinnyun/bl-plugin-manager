"""偏好设置与界面数据属性。"""

from __future__ import annotations

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, PropertyGroup

from . import bridge, constants as C

_LOADING_MACHINE_PATH = False


# ---------------------------------------------------------------------------
# 列表项 / 分类项
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
    compat: StringProperty(default="")        # ok / too_old / too_new / unknown
    max_version_text: StringProperty(default="")  # 最高支持版本（实时计算）
    supported: StringProperty(default="")     # yes / no / unknown（仅真实实测后才为 yes）
    residue: IntProperty(default=0)           # 注册残留数量
    compat_text: StringProperty(default="")   # 支持版本范围文案（实时计算）
    compat_detail: StringProperty(default="") # 不兼容时的具体说明
    load_state: StringProperty(default="")    # 实测结果: ok / failed / ""(未测)
    load_error: StringProperty(default="")
    author: StringProperty(default="")
    module: StringProperty(default="")
    note: StringProperty(default="")
    category: StringProperty(default="")
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
    from . import items
    items.maybe_rebuild(self, force=True)


def _on_device_name_update(self, context):
    if _LOADING_MACHINE_PATH or not self.device_name.strip():
        return
    try:
        from .storage import machine_config
        from .storage.device_profiles import DeviceProfileStore
        local = machine_config.load()
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
    device_name: StringProperty(
        name="设备名称",
        description="此电脑在同步插件库中的显示名称；启用插件清单按设备分别保存",
        default="",
        options={"SKIP_SAVE"},
        update=_on_device_name_update,
    )
    # 界面状态
    selected_key: StringProperty(default="")
    active_index: IntProperty(default=0, update=_on_index_update)
    active_category: StringProperty(default="全部", update=_on_filter_update)
    only_favorites: BoolProperty(default=False, update=_on_filter_update)
    only_updates: BoolProperty(default=False, update=_on_filter_update)
    only_enabled: BoolProperty(default=False, update=_on_filter_update)
    only_incompatible: BoolProperty(
        name="仅不兼容",
        description="只显示与当前 Blender 版本不兼容（版本过高或过低）的插件",
        default=False, update=_on_filter_update)
    search: StringProperty(default="", update=_on_filter_update)
    candidate_count: IntProperty(default=0)

    # 兼容性测试任务运行时状态（仅本会话，不写入偏好文件）
    compat_running: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_cancelling: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_total: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_done: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_ok: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_fail: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_current: StringProperty(default="", options={"SKIP_SAVE"})

    # 本次扫描/导入的结果明细（面板展示）
    report_items: CollectionProperty(type=PM_ReportItem)
    report_summary: StringProperty(default="")
    report_index: IntProperty(default=0)

    # 在线商店
    store_search: StringProperty(name="搜索", default="", description="按名称/描述搜索商店")
    store_only_new: BoolProperty(name="仅未安装", default=True,
                                 description="只显示尚未安装到插件库的扩展")
    store_only_compatible: BoolProperty(name="仅兼容版本", default=True,
                                        description="只显示与当前 Blender 版本兼容的扩展")

    # 启动控制
    sync_startup_on_launch: BoolProperty(
        name="启动时按「自启」标记同步",
        description=("Blender 启动时：启用在插件库中标记为「自启」的插件。"
                     "默认只做启用，不会停用其它插件"),
        default=False,
    )
    startup_disable_unmarked: BoolProperty(
        name="同时停用未标记自启的插件",
        description=("启动同步时，把没有标记「自启」的插件也停用。"
                     "能最大限度节省资源，但请先整理好自启标记再开启"),
        default=False,
    )

    plugin_items: CollectionProperty(type=PM_PluginItem)
    category_items: CollectionProperty(type=PM_CategoryItem)

    def draw(self, context):
        layout = self.layout

        # --- 设备与库 ---
        col = layout.column()
        col.prop(self, "device_name")
        try:
            from .storage import machine_config
            col.label(text=f"设备 ID: {machine_config.load()['device_id'][:12]}")
        except Exception:
            pass
        col.prop(self, "library_path")

        # --- 挂载 ---
        row = col.row(align=True)
        row.operator("plugin_manager.setup_library", icon="LINKED")
        row.operator("plugin_manager.unmount_library", icon="UNLINKED")

        if self.library_path:
            state = bridge.library_state(self.library_path)
            box = layout.box()
            box.label(
                text=f"脚本目录: {'已挂载' if state['script_dir'] else '未挂载'}",
                icon="CHECKMARK" if state["script_dir"] else "ERROR",
            )
            box.label(
                text=f"扩展仓库: {'已挂载' if state['repo'] else '未挂载'}",
                icon="CHECKMARK" if state["repo"] else "ERROR",
            )
            box.label(text="在 3D 视图侧边栏 '插件库' 标签中管理")
        else:
            layout.label(text="请先设置插件库路径", icon="ERROR")

        # --- 诊断与维护 ---
        box = layout.box()
        box.label(text="诊断与维护", icon="TOOL_SETTINGS")
        row = box.row(align=True)
        row.operator("plugin_manager.refresh", icon="FILE_REFRESH")
        row.operator("plugin_manager.scan_inbox", icon="FILE_REFRESH", text="扫描投放区")
        row.operator("plugin_manager.show_warnings", icon="INFO", text="诊断信息")
        row = box.row(align=True)
        row.operator("plugin_manager.cleanup_residue", icon="TRASH", text="清理失败插件残留")
        row.operator("plugin_manager.clear_updates", icon="X", text="清除更新标记")

        # --- 启动同步策略 ---
        box = layout.box()
        box.label(text="启动同步策略", icon="PLAY")
        box.prop(self, "sync_startup_on_launch")
        box.prop(self, "startup_disable_unmarked")
        box.operator("plugin_manager.apply_startup", icon="PLAY", text="立即同步自启状态")

        # --- 迁移收编 ---
        box = layout.box()
        box.label(text="迁移收编", icon="IMPORT")
        row = box.row(align=True)
        row.operator("plugin_manager.scan_candidates", icon="VIEWZOOM", text="扫描可收编插件")
        if self.candidate_count:
            box.label(text=f"可收编 {self.candidate_count} 个")
        box.operator("plugin_manager.import_candidates", icon="IMPORT", text="收编全部")

        # --- 官方商店目录关联 ---
        box = layout.box()
        box.label(text="官方商店目录关联", icon="URL")
        try:
            official = bool(bridge.library_state(self.library_path).get("official")) if self.library_path else False
        except Exception:
            official = False
        if official:
            box.label(text="官方商店面板已与本库共用目录", icon="CHECKMARK")
        else:
            box.operator("plugin_manager.unify_store", icon="LINKED",
                         text="让官方商店使用本插件库")


classes = (PM_PluginItem, PM_CategoryItem, PM_ReportItem, PMAddonPreferences)
