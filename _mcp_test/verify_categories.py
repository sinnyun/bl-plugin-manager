"""验证：新建分类 → 把插件移入 → 面板真实绘制，全在运行实例中完成。"""
import io
import json
import sys

import bpy

from bl_plugin_manager import db as pdb, items
import bl_plugin_manager.ui as ui

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}
d = pdb.LibraryDB(LIB)

# 1) 新建两个分类
d.add_category("建模工具")
d.add_category("动画工具")
d.save()
out["categories_after_add"] = pdb.LibraryDB(LIB).categories

# 2) 用操作符把两个插件分别移入
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
recs = sorted(pdb.LibraryDB(LIB).plugins.values(), key=lambda r: r.get("name") or "")
a, b = recs[0], recs[1]
bpy.ops.plugin_manager.set_category(key=a["key"], category="建模工具")
bpy.ops.plugin_manager.set_category(key=b["key"], category="动画工具")
d2 = pdb.LibraryDB(LIB)
out["a_category"] = d2.get(a["key"])["category"]
out["b_category"] = d2.get(b["key"])["category"]
out["single_category_each"] = isinstance(d2.get(a["key"])["category"], str)
out["categories_now"] = d2.categories
out["counts"] = d2.category_counts()

# 3) assign_category 操作符（下拉）也应可用：直接以 execute 方式指定
try:
    bpy.ops.plugin_manager.assign_category(key=a["key"], category="动画工具")
    out["assign_op"] = pdb.LibraryDB(LIB).get(a["key"])["category"]
except Exception as e:
    out["assign_op"] = f"ERR {e}"

# 4) 面板真实绘制（含分类区）
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
    prefs.active_category = "全部"
    n = len(items.rebuild_items(prefs))
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    out["redraw"] = "done"
    out["items"] = n
except Exception as ex:
    out["redraw"] = f"EXC {ex!r}"
finally:
    sys.stdout, sys.stderr = o, e
    ui.PM_PT_main.draw, ui.PM_UL_plugins.draw_item = op_, oi_

txt = buf.getvalue()
out["calls"] = calls
out["has_traceback"] = "Traceback" in txt
out["error_lines"] = [l for l in txt.splitlines()
                      if any(k in l for k in ("Traceback", "Error", "Exception", "ui.py", "db.py"))][:20]

# 5) 清理：删掉刚建的测试分类，插件回未分类
d3 = pdb.LibraryDB(LIB)
d3.delete_category("建模工具")
d3.delete_category("动画工具")
d3.save()
out["after_cleanup_categories"] = pdb.LibraryDB(LIB).categories
out["a_after_cleanup"] = pdb.LibraryDB(LIB).get(a["key"])["category"]

print("@@CATTEST@@" + json.dumps(out, ensure_ascii=False, default=str))
