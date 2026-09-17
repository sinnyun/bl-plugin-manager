import bpy
import io
import json
import sys

res = {}

# 给面板与列表行的 draw 打桩计数，证明重绘确实走到了我的代码
import bl_plugin_manager.ui as ui
import bl_plugin_manager.items as items

calls = {"panel": 0, "list": 0}
orig_panel = ui.PM_PT_main.draw
orig_item = ui.PM_UL_plugins.draw_item


def w_panel(self, context):
    calls["panel"] += 1
    return orig_panel(self, context)



def w_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
    calls["list"] += 1
    return orig_item(self, context, layout, data, item, icon, active_data, active_propname, index, flt_flag)


ui.PM_PT_main.draw = w_panel
ui.PM_UL_plugins.draw_item = w_item

# 打开侧边栏 + 设置类别
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
    res["redraw"] = "done"
except Exception as e:
    res["redraw"] = f"EXC {e!r}"
finally:
    sys.stdout = old_out
    sys.stderr = old_err
    ui.PM_PT_main.draw = orig_panel
    ui.PM_UL_plugins.draw_item = orig_item

text = buf.getvalue()
res["calls"] = calls
res["has_traceback"] = "Traceback" in text
res["error_lines"] = [ln for ln in text.splitlines()
                      if any(k in ln for k in ("Traceback", "Error", "Exception", "ui.py"))][:30]
res["raw"] = text[:400]

print(json.dumps(res, ensure_ascii=False, default=str))
