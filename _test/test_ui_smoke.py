"""UI 冒烟测试：用模拟布局对象真实执行面板 draw 与列表行 draw，
以捕获属性拼写错误、未注册操作符引用、逻辑异常。"""
import json
import os
import tempfile

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

    # 3) 列表行 draw
    class RowMock(MockLayout):
        def __init__(self):
            super().__init__("row")
            self.layout_type = "DEFAULT"

    if len(prefs.plugin_items):
        row = RowMock()
        item = prefs.plugin_items[0]
        pm_ui.PM_UL_plugins.draw_item(row, bpy.context, row, prefs, item, None, prefs, "active_index", 0, 0)
        print(f"[UI] 列表行 draw: {len(row.calls)} 次布局调用")

    # 4) 偏好设置 draw（AddonPreferences.draw 使用 self.layout）
    class PrefShim:
        """把未显式提供的属性转发到真实偏好，便于直接调用 draw。"""

        def __init__(self, real):
            object.__setattr__(self, "_real", real)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_real"), name)

    shim = PrefShim(prefs)
    shim.layout = MockLayout("prefs")
    from bl_plugin_manager.preferences import PMAddonPreferences

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
