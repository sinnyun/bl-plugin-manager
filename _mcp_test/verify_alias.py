"""在运行实例中验证双名称：设置中文别名 → 列表项同时带两种名称 → 面板绘制。"""
import io
import json
import sys

import bpy

from bl_plugin_manager import db as pdb, items
import bl_plugin_manager.ui as ui

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
# 清掉可能残留的过滤器，保证列表完整
prefs.active_category = "全部"
prefs.only_favorites = False
prefs.only_updates = False
prefs.only_enabled = False
prefs.search = ""

d = pdb.LibraryDB(LIB)
recs = sorted(d.plugins.values(), key=lambda r: r.get("name") or "")
# 挑几个英文名的插件
targets = [r for r in recs if all(ord(c) < 128 for c in (r.get("name") or ""))][:3]
out["targets"] = [r.get("name") for r in targets]

aliases = {0: "建模辅助", 1: "自动绑骨", 2: "材质工具"}
for i, r in enumerate(targets):
    out[f"before_{i}"] = {"name": r.get("name"), "display": r.get("display_name", "")}
    dd0 = pdb.LibraryDB(LIB)
    rr = dd0.get(r["key"])
    rr["display_name"] = aliases[i]
    dd0.upsert(r["key"], rr)
    dd0.save()

d2 = pdb.LibraryDB(LIB)
for i, r in enumerate(targets):
    got = d2.get(r["key"])
    out[f"after_{i}"] = {"name": got.get("name"), "display": got.get("display_name"),
                         "actual_unchanged": got.get("name") == r.get("name")}

# 列表项应同时带两种名称
keys = items.rebuild_items(prefs)
out["total_items"] = len(keys)
sample = []
for it in prefs.plugin_items:
    if it.display_name:
        sample.append({"shown": it.display_name, "actual": it.name})
out["aliased_items"] = sample

# 面板真实绘制
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
                      if any(k in l for k in ("Traceback", "Error", "Exception", "ui.py"))][:15]

# 清理：移除测试别名
dd = pdb.LibraryDB(LIB)
for r in targets:
    rr = dd.get(r["key"])
    rr["display_name"] = ""
    dd.upsert(r["key"], rr)
dd.save()
out["cleaned"] = all(not (pdb.LibraryDB(LIB).get(r["key"]) or {}).get("display_name") for r in targets)

print("@@ALIAS@@" + json.dumps(out, ensure_ascii=False, default=str))
