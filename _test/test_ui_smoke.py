"""UI 冒烟测试：用模拟布局对象真实执行面板 draw 与列表行 draw，
以捕获属性拼写错误、未注册操作符引用、逻辑异常。"""
import json
import os
import tempfile
from unittest.mock import patch

import addon_utils
import bpy

from bl_plugin_manager import operators as pm_ops
from bl_plugin_manager import ui as pm_ui

ERRORS = []
# 跨子布局收集所有 layout.operator(...) 的 idname，便于断言入口是否存在
OP_CALLS = []


class MockLayout:
    """记录调用并校验 operator idname 的模拟布局。"""

    def __init__(self, path="root"):
        self.path = path
        self.calls = []

    def __getattr__(self, name):
        # 属性赋值会走 __setattr__；这里处理未知方法
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "operator":
                idname = args[0] if args else kwargs.get("operator", "")
                OP_CALLS.append(idname)
                if idname and not hasattr(bpy.ops, idname.split(".")[0]):
                    ERRORS.append(f"{self.path}: 未知操作符命名空间 {idname}")
                elif idname:
                    ns, _, op = idname.partition(".")
                    if not hasattr(getattr(bpy.ops, ns), op):
                        ERRORS.append(f"{self.path}: 未注册的操作符 {idname}")
            if name in ("row", "column", "box", "grid_flow"):
                return MockLayout(f"{self.path}.{name}")
            if name == "operator":
                return MockOperator()
            return MockLayout(f"{self.path}.{name}")

        return method

    # 允许 layout.scale_x = 1.0 / layout.alignment = "RIGHT"
    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)


class MockOperator:
    pass


def run():
    base = tempfile.mkdtemp(prefix="pmui_")
    lib = os.path.join(base, "library")
    addon_utils.enable("bl_plugin_manager", default_set=True, refresh_handled=True)
    prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
    prefs.library_path = lib
    bpy.ops.plugin_manager.setup_library()
    from bl_plugin_manager import items, library_jobs
    from bl_plugin_manager.preferences import PMAddonPreferences

    class PrefShim:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

    shim = PrefShim(prefs)
    shim.layout = MockLayout("prefs_preparing")
    pending = MockLayout("sidebar_preparing")
    with patch.object(items, "maybe_rebuild", side_effect=AssertionError("read during connection")), \
         patch.object(pm_ui.bridge, "library_state", side_effect=AssertionError("read during connection")):
        PMAddonPreferences.draw(shim, bpy.context)
        pm_ui.PM_PT_main.draw(type("S", (), {"layout": pending})(), bpy.context)
    for layout in (shim.layout, pending):
        if not any(name == "label" and kwargs.get("text") == prefs.library_job_status
                   for name, _args, kwargs in layout.calls):
            ERRORS.append(f"{layout.path}: 缺少后台连接状态")

    library_jobs._job.worker.join(timeout=10)
    assert library_jobs._tick() is None and not prefs.library_job_failed, prefs.library_job_status
    bpy.app.timers.unregister(library_jobs._tick)

    # 造两个插件，让面板有内容
    for name, body in (
        ("Alpha", 'bl_info={"name":"Alpha","version":(1,2,3),"blender":(4,2,0),"category":"X"}\n'),
        ("Beta", 'bl_info={"name":"Beta","version":(2,0,0),"blender":(4,2,0)}\n'),
    ):
        src = os.path.join(base, name)
        os.makedirs(src)
        with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
        from bl_plugin_manager import db as pdb, library

        library.import_plugin_dir(src, lib, pdb.LibraryDB(lib), move=True)

    prefs.selected_key = ""
    from bl_plugin_manager import items

    items.rebuild_items(prefs)

    # 1) 主面板 draw（选中与未选中两种状态）
    sel_ok = []
    for label, sel in (("no-selection", ""), ("with-selection", prefs.plugin_items[0].key if len(prefs.plugin_items) else "")):
        prefs.selected_key = sel
        mock = MockLayout(f"main[{label}]")
        pm_ui.PM_PT_main.draw(type("S", (), {"layout": mock})(), bpy.context)
        sel_ok.append(pm_ui._selected(prefs) is not None)
        print(f"[UI] 主面板 draw {label}: layout调用={len(mock.calls)} 有选中详情={sel_ok[-1]}")
    if len(prefs.plugin_items) and not sel_ok[1]:
        ERRORS.append("with-selection 状态下未渲染选中项详情")

    # 2) 工具面板已删除：迁移/维护入口改由偏好设置承载
    if hasattr(pm_ui, "PM_PT_tools"):
        ERRORS.append("PM_PT_tools 应已删除（迁移入口已移入偏好设置）")
    if hasattr(pm_ui, "PM_MT_library") is False:
        ERRORS.append("PM_MT_library 缺失")
    mock = MockLayout("library_menu")
    pm_ui.PM_MT_library.draw(type("S", (), {"layout": mock})(), bpy.context)
    print(f"[UI] 库菜单 draw: {len(mock.calls)} 次布局调用")

    # 3) 列表行 & 子面板 & 分组菜单 draw
    class RowMock(MockLayout):
        def __init__(self):
            super().__init__("row")
            self.layout_type = "DEFAULT"

    # 创建一个测试分类与一个测试预设，覆盖 PM_UL_groups / PM_UL_presets 与相关菜单
    bpy.ops.plugin_manager.add_category(name="烟雾测试分类")
    bpy.ops.plugin_manager.preset_add(name="烟雾测试预设")
    if len(prefs.plugin_items):
        bpy.ops.plugin_manager.plugin_toggle_group(key=prefs.plugin_items[0].key, group="烟雾测试分类")
        bpy.ops.plugin_manager.plugin_toggle_preset(key=prefs.plugin_items[0].key, preset="烟雾测试预设")
    items.rebuild_items(prefs)

    if len(prefs.preset_items):
        for pi in range(len(prefs.preset_items)):
            prow = RowMock()
            pm_ui.PM_UL_presets.draw_item(prow, bpy.context, prow, prefs, prefs.preset_items[pi], None, prefs, "active_preset_index", pi, 0)
        print(f"[UI] 预设列表行 draw: {len(prefs.preset_items)} 行")

    if len(prefs.group_items):
        for gi in range(len(prefs.group_items)):
            grow = RowMock()
            pm_ui.PM_UL_groups.draw_item(grow, bpy.context, grow, prefs, prefs.group_items[gi], None, prefs, "active_group_index", gi, 0)
        print(f"[UI] 分类列表行 draw: {len(prefs.group_items)} 行（含默认全部插件行）")

    if len(prefs.plugin_items):
        for bmode in (False, True):
            prefs.batch_mode = bmode
            mock_b = MockLayout(f"main[batch={bmode}]")
            pm_ui.PM_PT_main.draw(type("S", (), {"layout": mock_b})(), bpy.context)
            row = RowMock()
            item = prefs.plugin_items[0]
            pm_ui.PM_UL_plugins.draw_item(row, bpy.context, row, prefs, item, None, prefs, "active_index", 0, 0)
        prefs.batch_mode = False
        print(f"[UI] 列表行 draw: {len(row.calls)} 次布局调用")

    for panel_cls in (pm_ui.PM_PT_detail, pm_ui.PM_PT_batch, pm_ui.PM_PT_actions):
        pmock = MockLayout(panel_cls.bl_idname)
        panel_cls.draw(type("S", (), {"layout": pmock})(), bpy.context)
    for menu_cls in (pm_ui.PM_MT_preset, pm_ui.PM_MT_category, pm_ui.PM_MT_plugin_groups,
                     pm_ui.PM_MT_plugin_presets, pm_ui.PM_MT_prompt_presets, pm_ui.PM_MT_plugin):
        mmock = MockLayout(menu_cls.bl_idname)
        menu_cls.draw(type("S", (), {"layout": mmock})(), bpy.context)

    # 4) 偏好设置 draw（AddonPreferences.draw 使用 self.layout）
    shim.layout = MockLayout("prefs")

    del OP_CALLS[:]
    PMAddonPreferences.draw(shim, bpy.context)
    # unify_store 是条件入口：已统一时改为显示提示文案，因此不计入必选。
    for needed in ("plugin_manager.unmount_library", "plugin_manager.show_warnings",
                   "plugin_manager.clear_updates", "plugin_manager.cleanup_residue",
                   "plugin_manager.scan_candidates", "plugin_manager.import_candidates"):
        if needed not in OP_CALLS:
            ERRORS.append(f"偏好设置未渲染维护/迁移入口: {needed}")
    print(f"[UI] 偏好 draw: 通过（含 {len(OP_CALLS)} 个操作符入口）")

    # 4b) 任务运行时主面板 draw（进度 + 取消）
    prefs.compat_running = True
    prefs.compat_total = 3
    prefs.compat_done = 1
    prefs.compat_ok = 1
    prefs.compat_current = "SomePlugin"
    mock = MockLayout("main[running]")
    del OP_CALLS[:]
    pm_ui.PM_PT_main.draw(type("S", (), {"layout": mock})(), bpy.context)
    if "plugin_manager.cancel_compat" not in OP_CALLS:
        ERRORS.append("任务运行时主面板未渲染取消入口")
    if "plugin_manager.verify_compat" in OP_CALLS:
        ERRORS.append("任务运行时应显示取消按钮而非再次启动测试")
    prefs.compat_running = False
    prefs.compat_cancelling = False
    print("[UI] 主面板运行态 draw: 通过")

    # 5) 面板元数据
    meta = {
        "main_category": pm_ui.PM_PT_main.bl_category,
        "main_bl_idname": pm_ui.PM_PT_main.bl_idname,
        "list_idname": pm_ui.PM_UL_plugins.bl_idname,
        "tools_removed": not hasattr(pm_ui, "PM_PT_tools"),
    }
    print("@@UI_META@@", json.dumps(meta, ensure_ascii=False))

    # 校验面板引用的操作符均已注册
    registered = set()
    for cls in pm_ops.classes:
        if hasattr(cls, "bl_idname"):
            registered.add(cls.bl_idname)
    for name in pm_ui.all_operator_refs() if hasattr(pm_ui, "all_operator_refs") else []:
        if name not in registered:
            ERRORS.append(f"面板引用了未注册操作符: {name}")

    addon_utils.disable("bl_plugin_manager")
    import shutil

    shutil.rmtree(base, ignore_errors=True)


run()
print("===UI_SMOKE_SUMMARY===")
print(json.dumps({"errors": ERRORS}, ensure_ascii=False))
