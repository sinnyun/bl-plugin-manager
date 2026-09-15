"""验证 header 弹窗真实绘制 + 清理残留过滤器。"""
import io
import json
import sys

import bpy

from bl_plugin_manager import db as pdb, items
import bl_plugin_manager.header as hdr

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
# 清理所有过滤器
prefs.active_category = "全部"
prefs.only_favorites = False
prefs.only_updates = False
prefs.only_enabled = False
prefs.search = ""

class L:
    def __init__(self):
        self.n = 0
    def __getattr__(self, k):
        def m(*a, **kw):
            self.n += 1
            return L()
        return m

# 直接调用弹窗操作符的 invoke + draw（通过 bpy.ops 拿到真实实例不可行，
# 改为用其 draw 逻辑函数式验证：构造一个带 category 的轻量对象）
class FakeOp:
    """模拟 Blender 操作符实例：draw() 会用 self.layout 和 self.category。"""

    bl_idname = "plugin_manager.header_popup"

    def __init__(self, cat, layout):
        self.category = cat
        self.layout = layout

lay = L()
try:
    hdr.PM_OT_header_popup.draw(FakeOp("全部", lay), None)
    out["popup_draw"] = "ok"
    out["popup_calls"] = lay.n
except Exception as e:
    out["popup_draw"] = f"ERR {e}"

# 分类维度弹窗
lay2 = L()
try:
    hdr.PM_OT_header_popup.draw(FakeOp("未分类", lay2), None)
    out["popup_draw_uncat"] = "ok"
    out["popup_calls_uncat"] = lay2.n
except Exception as e:
    out["popup_draw_uncat"] = f"ERR {e}"

# 列表重建后数量
n = len(items.rebuild_items(prefs))
out["items_after_reset"] = n

# 真实面板 + header 绘制
import bl_plugin_manager.ui as ui
calls = {"panel": 0, "list": 0}
op_, oi_ = ui.PM_PT_main.draw, ui.PM_UL_plugins.draw_item


def wp(self, ctx):
    calls["panel"] += 1
    return op_(self, ctx)


def wi(self, ctx, layout, data, item, icon, ad, ap, index=0, ff=0):
    calls["list"] += 1
    return oi_(self, ctx, layout, data, item, icon, ad, ap, index, ff)


ui.PM_PT_main.draw, ui.PM_UL_plugins.draw_item = wp, wi
for w in bpy.context.window_manager.windows:
    for ar in w.screen.areas:
        if ar.type == "VIEW_3D":
            for sp in ar.spaces:
                if sp.type == "VIEW_3D":
                    sp.show_region_ui = True
            for rg in ar.regions:
                if rg.type == "UI":
                    rg.active_panel_category = "插件库"

buf = io.StringIO()
o, e = sys.stdout, sys.stderr
sys.stdout = sys.stderr = buf
try:
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    out["redraw"] = "done"
except Exception as ex:
    out["redraw"] = f"EXC {ex!r}"
finally:
    sys.stdout, sys.stderr = o, e
    ui.PM_PT_main.draw, ui.PM_UL_plugins.draw_item = op_, oi_
txt = buf.getvalue()
out["calls"] = calls
out["has_traceback"] = "Traceback" in txt
out["error_lines"] = [l for l in txt.splitlines()
                      if any(k in l for k in ("Traceback", "Error", "Exception", "ui.py", "header.py"))][:15]

# 自启统计与分类
d = pdb.LibraryDB(LIB)
out["startup_stats"] = d.startup_stats()
out["categories"] = d.categories[:8]

# label 列表行是否含自启图标信息
if prefs.plugin_items:
    it = prefs.plugin_items[0]
    out["sample_item"] = {"name": it.name, "display": it.display_name,
                          "startup": it.startup, "selected": it.selected,
                          "enabled": it.enabled}

print("@@HDR2@@" + json.dumps(out, ensure_ascii=False, default=str))
