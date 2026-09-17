"""在有 134 条数据的情况下，真实绘制插件列表，捕获 draw_item 错误。"""
import io
import json
import sys

import bpy

import bl_plugin_manager.ui as ui

calls = {"panel": 0, "list": 0, "list_error": 0}
orig_panel = ui.PM_PT_main.draw
orig_item = ui.PM_UL_plugins.draw_item
captured = []


def w_panel(self, context):
    calls["panel"] += 1
    return orig_panel(self, context)



def w_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
    calls["list"] += 1
    try:
        return orig_item(self, context, layout, data, item, icon, active_data, active_propname, index, flt_flag)
    except Exception as e:
        calls["list_error"] += 1
        captured.append(f"draw_item error: {type(e).__name__}: {e}")
        raise


ui.PM_PT_main.draw = w_panel
ui.PM_UL_plugins.draw_item = w_item

# 让列表有内容并展开侧边栏
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.active_category = "全部"
prefs.search = ""
prefs.only_favorites = False
prefs.only_updates = False
prefs.only_enabled = False
from bl_plugin_manager import items
n = len(items.rebuild_items(prefs))

for w in bpy.context.window_manager.windows:
    for a in w.screen.areas:
        if a.type == "VIEW_3D":
            for sp in a.spaces:
                if sp.type == "VIEW_3D":
                    sp.show_region_ui = True
            for r in a.regions:
                if r.type == "UI":
                    r.active_panel_category = "插件库"

buf = io.StringIO()
old_out, old_err = sys.stdout, sys.stderr
sys.stdout = buf
sys.stderr = buf
try:
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    res = "done"
except Exception as e:
    res = f"EXC {e!r}"
finally:
    sys.stdout = old_out
    sys.stderr = old_err
    ui.PM_PT_main.draw = orig_panel
    ui.PM_UL_plugins.draw_item = orig_item

text = buf.getvalue()
print(json.dumps({
    "items_in_list": n,
    "redraw": res,
    "calls": calls,
    "captured_errors": captured,
    "has_traceback": "Traceback" in text,
    "error_lines": [ln for ln in text.splitlines()
                    if any(k in ln for k in ("Traceback", "Error", "Exception", "ui.py"))][:25],
}, ensure_ascii=False, default=str))
