"""3D 视口标题栏快捷入口：一排分类图标，点开即弹出该类插件，可一键启停。

借鉴 Plugin Manager Pro 的 Header 交互，但只读取我们库内的分类与插件，
不托管 Blender 的插件系统。
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import bridge, constants as C


def _draw_header(self, context):
    """挂到 VIEW3D_HT_header 的右侧。"""
    prefs = bridge.get_prefs()
    if not prefs or not prefs.library_path:
        return
    layout = self.layout
    layout.separator_spacer()

    row = layout.row(align=True)
    # 主按钮：打开侧边栏的插件库面板提示
    op = row.operator("plugin_manager.header_popup", text="", icon="PLUGIN")
    op.category = "全部"
    # 各分类（最多显示前若干个，避免标题栏过长）
    cats = [it.name for it in prefs.category_items if it.name and it.name != "全部"]
    shown = cats[:8]
    for name in shown:
        op = row.operator("plugin_manager.header_popup", text="", icon="FILE_FOLDER")
        op.category = name
    if len(cats) > len(shown):
        layout.label(text=f"+{len(cats) - len(shown)}")


class PM_OT_header_popup(Operator):
    """标题栏弹出的分类插件列表：点击条目即可启用/停用。"""

    bl_idname = "plugin_manager.header_popup"
    bl_label = "插件库"
    bl_description = "快速启用/停用该分类下的插件"

    category: StringProperty(default="全部")

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=330)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        prefs = bridge.get_prefs()
        if not prefs:
            layout.label(text="插件库未启用", icon="ERROR")
            return

        cat = self.category or "全部"
        layout.label(text=f"分类: {cat}", icon="FILE_FOLDER")

        from .db import LibraryDB

        db = LibraryDB(prefs.library_path)
        recs = [
            r for r in db.plugins.values()
            if cat == "全部" or (r.get("category") or C.DEFAULT_CATEGORY) == cat
        ]
        recs.sort(key=lambda r: (r.get("display_name") or r.get("name") or "").lower())

        if not recs:
            layout.label(text="该分类下没有插件", icon="INFO")
            return

        layout.separator()
        # 只列前 25 条，避免弹出过长
        for r in recs[:25]:
            row = layout.row(align=True)
            mod = r.get("module") or ""
            enabled = bridge.is_module_enabled(mod) if mod else False
            op = row.operator(
                "plugin_manager.toggle",
                text=(r.get("display_name") or r.get("name") or r.get("folder_name") or ""),
                icon="CHECKBOX_HLT" if enabled else "CHECKBOX_DEHLT",
                emboss=False,
            )
            op.key = r.get("key", "")
            op.enable = not enabled
            # 自启标记
            row.label(text="", icon="RADIOBUT_ON" if r.get("startup") else "RADIOBUT_OFF")
        if len(recs) > 25:
            layout.label(text=f"…还有 {len(recs) - 25} 个，请在侧边栏查看")

        layout.separator()
        row = layout.row(align=True)
        row.operator("plugin_manager.refresh", icon="FILE_REFRESH", text="刷新")
        row.operator("plugin_manager.apply_startup", icon="PLAY", text="同步自启")


_classes = (PM_OT_header_popup,)


def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    try:
        bpy.types.VIEW3D_HT_header.append(_draw_header)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_header)
    except Exception:
        pass
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
